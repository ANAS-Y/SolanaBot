import asyncio
import logging
import os
import sys
import base64
import aiohttp
from aiohttp import web
from dotenv import load_dotenv

# --- RENDER CONFIGURATION ---
PORT = int(os.environ.get("PORT", 8080))
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
RPC_URL = os.getenv("RPC_URL", "https://api.mainnet-beta.solana.com")

if not BOT_TOKEN:
    logging.error("❌ ERROR: Missing BOT_TOKEN")

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from solders.keypair import Keypair 
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solana.transaction import Transaction
from solana.rpc.async_api import AsyncClient
from solana.rpc.types import TxOpts
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

import database as db
import jupiter as jup

# --- LOGGING SETUP ---
logging.basicConfig(level=logging.INFO)

# --- WEB SERVER (KEEPS RENDER ALIVE) ---
async def handle_ping(request):
    return web.Response(text="Bot is running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logging.info(f"🌍 Web server running on port {PORT}")

# --- SECURITY & UTILS ---
ACTIVE_SESSIONS = {} 

def derive_key(pin: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=100000)
    return base64.urlsafe_b64encode(kdf.derive(pin.encode()))

def encrypt_key(priv_bytes, pin):
    salt = os.urandom(16)
    f = Fernet(derive_key(pin, salt))
    return f.encrypt(priv_bytes), salt

def decrypt_key(enc_bytes, salt, pin):
    try:
        f = Fernet(derive_key(pin, salt))
        return f.decrypt(enc_bytes)
    except:
        return None

# --- KEYBOARDS ---
def get_main_menu():
    kb = [
        [KeyboardButton(text="💰 Wallet Balance"), KeyboardButton(text="💸 Send SOL")],
        [KeyboardButton(text="🚀 Auto-Buy / Snipe"), KeyboardButton(text="🔐 Create/Reset Wallet")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# --- STATES ---
class BotStates(StatesGroup):
    create_pin = State()
    
    # Buy Flow
    trade_contract = State()
    trade_amount = State()
    trade_risk = State()
    trade_pin = State()
    
    # Send Flow
    send_dest = State()
    send_amount = State()
    send_pin = State()

# --- BOT SETUP ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- HANDLERS: START & MENU ---
@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👋 **Welcome to ProDex Bot!**\n\n"
        "Secure, Non-Custodial Trading on Solana.\n"
        "Select an option below:",
        reply_markup=get_main_menu(),
        parse_mode="Markdown"
    )

# --- HANDLER: CREATE WALLET ---
@dp.message(F.text == "🔐 Create/Reset Wallet")
async def create_wallet_start(message: types.Message, state: FSMContext):
    await message.answer("⚠️ **Security Warning**\n"
                         "This will overwrite any existing wallet on this bot.\n"
                         "Since you are on Render Free, ensure you export your keys if needed.\n\n"
                         "👉 **Enter a 6-digit PIN** to secure your new wallet:")
    await state.set_state(BotStates.create_pin)

@dp.message(BotStates.create_pin)
async def process_create_pin(message: types.Message, state: FSMContext):
    pin = message.text.strip()
    if not pin.isdigit() or len(pin) < 4:
        return await message.answer("❌ PIN must be at least 4 digits.")

    kp = Keypair()
    enc_key, salt = encrypt_key(bytes(kp), pin)
    await db.add_wallet(message.from_user.id, str(kp.pubkey()), enc_key, salt)
    
    await message.answer(
        f"✅ **Wallet Created Successfully**\n\n"
        f"Address: `{kp.pubkey()}`\n\n"
        f"⚠️ **IMPORTANT:** Save this PIN. If Render restarts, you will need it to unlock your wallet.",
        reply_markup=get_main_menu(),
        parse_mode="Markdown"
    )
    await state.clear()

# --- HANDLER: BALANCE ---
@dp.message(F.text == "💰 Wallet Balance")
async def check_balance(message: types.Message):
    wallet = await db.get_wallet(message.from_user.id)
    if not wallet:
        return await message.answer("❌ No wallet found. Please create one first.")

    pub_key_str = wallet[2] # Fetch public key from DB
    msg = await message.answer("⏳ Fetching blockchain data...")

    try:
        # 1. Get SOL Balance
        async with AsyncClient(RPC_URL) as client:
            resp = await client.get_balance(Pubkey.from_string(pub_key_str))
            sol_balance = resp.value / 1_000_000_000
        
        # 2. Get SOL Price in USD
        sol_price = await jup.get_price(jup.SOL_MINT)
        if not sol_price: sol_price = 0.0
        
        usd_value = sol_balance * sol_price

        await msg.edit_text(
            f"💰 **Wallet Balance**\n\n"
            f"Address: `{pub_key_str}`\n\n"
            f"SOL: **{sol_balance:.4f} SOL**\n"
            f"USD: **${usd_value:.2f}**\n"
            f"Price: ${sol_price:.2f}/SOL",
            parse_mode="Markdown"
        )
    except Exception as e:
        await msg.edit_text(f"❌ Network Error: {str(e)}")

# --- HANDLER: SEND SOL ---
@dp.message(F.text == "💸 Send SOL")
async def send_sol_start(message: types.Message, state: FSMContext):
    await message.answer("📤 **Transfer SOL**\n\nPaste the **Destination Address**:")
    await state.set_state(BotStates.send_dest)

@dp.message(BotStates.send_dest)
async def send_sol_dest(message: types.Message, state: FSMContext):
    await state.update_data(dest=message.text.strip())
    await message.answer("💸 Enter Amount (e.g. 0.1):")
    await state.set_state(BotStates.send_amount)

@dp.message(BotStates.send_amount)
async def send_sol_amt(message: types.Message, state: FSMContext):
    try:
        amt = float(message.text.strip())
        await state.update_data(amount=amt)
        # Check if session exists
        if message.from_user.id in ACTIVE_SESSIONS:
            await execute_transfer(message, state)
        else:
            await message.answer("🔐 Enter PIN to authorize transfer:")
            await state.set_state(BotStates.send_pin)
    except ValueError:
        await message.answer("❌ Invalid number.")

@dp.message(BotStates.send_pin)
async def process_send_pin(message: types.Message, state: FSMContext):
    wallet = await db.get_wallet(message.from_user.id)
    if not wallet:
        await message.answer("⚠️ Wallet missing (DB Reset). Please Create/Reset Wallet.")
        return await state.clear()
        
    decrypted = decrypt_key(wallet[0], wallet[1], message.text.strip())
    if not decrypted: return await message.answer("❌ Wrong PIN.")
    
    ACTIVE_SESSIONS[message.from_user.id] = Keypair.from_bytes(decrypted)
    await execute_transfer(message, state)

async def execute_transfer(message, state):
    data = await state.get_data()
    user_id = message.from_user.id
    sender_kp = ACTIVE_SESSIONS[user_id]
    
    msg = await message.answer("⏳ Sending Transaction...")
    
    try:
        lamports = int(data['amount'] * 1_000_000_000)
        dest_pubkey = Pubkey.from_string(data['dest'])
        
        async with AsyncClient(RPC_URL) as client:
            # Build Transfer TX
            ix = transfer(
                TransferParams(
                    from_pubkey=sender_kp.pubkey(),
                    to_pubkey=dest_pubkey,
                    lamports=lamports
                )
            )
            
            # Create Transaction Object
            txn = Transaction().add(ix)
            
            # Send
            resp = await client.send_transaction(txn, sender_kp, opts=TxOpts(skip_preflight=True))
            tx_sig = str(resp.value)
            
            await msg.edit_text(f"✅ **Sent!**\n\nTX: `https://solscan.io/tx/{tx_sig}`", parse_mode="Markdown")
            
    except Exception as e:
        await msg.edit_text(f"❌ Transfer Failed: {str(e)}")
    
    await state.clear()

# --- HANDLER: AUTO-BUY (Sniper) ---
@dp.message(F.text == "🚀 Auto-Buy / Snipe")
async def buy_start(message: types.Message, state: FSMContext):
    await message.answer("🛒 **Auto-Buy Setup**\n\nPaste the **Token Contract Address**:")
    await state.set_state(BotStates.trade_contract)

@dp.message(BotStates.trade_contract)
async def process_contract(message: types.Message, state: FSMContext):
    await state.update_data(contract=message.text.strip())
    await message.answer("💰 SOL Amount to Spend (e.g. 0.1):")
    await state.set_state(BotStates.trade_amount)

@dp.message(BotStates.trade_amount)
async def process_amount(message: types.Message, state: FSMContext):
    try:
        amt = float(message.text)
        await state.update_data(amount=amt)
        await message.answer("📊 Set Stop Loss % and Take Profit % (e.g. `-20 50`):")
        await state.set_state(BotStates.trade_risk)
    except:
        await message.answer("❌ Invalid amount.")

@dp.message(BotStates.trade_risk)
async def process_risk(message: types.Message, state: FSMContext):
    try:
        sl, tp = map(float, message.text.split())
        await state.update_data(sl=sl, tp=tp)
        
        if message.from_user.id in ACTIVE_SESSIONS:
            await execute_trade(message, state)
        else:
            await message.answer("🔐 Enter PIN to confirm trade:")
            await state.set_state(BotStates.trade_pin)
    except:
        await message.answer("❌ Format Error. Use space: `-20 100`")

@dp.message(BotStates.trade_pin)
async def process_trade_pin(message: types.Message, state: FSMContext):
    wallet = await db.get_wallet(message.from_user.id)
    if not wallet:
        await message.answer("⚠️ Wallet missing (DB Reset). Please Create/Reset Wallet.")
        return await state.clear()

    decrypted = decrypt_key(wallet[0], wallet[1], message.text.strip())
    if not decrypted: return await message.answer("❌ Wrong PIN.")
    
    ACTIVE_SESSIONS[message.from_user.id] = Keypair.from_bytes(decrypted)
    await execute_trade(message, state)

async def execute_trade(message, state):
    data = await state.get_data()
    user = message.from_user.id
    kp = ACTIVE_SESSIONS[user]
    
    msg = await message.answer("⏳ Swapping on Jupiter...")
    lamports = data['amount'] * 1_000_000_000
    
    tx = await jup.execute_swap(kp, jup.SOL_MINT, data['contract'], lamports, RPC_URL)
    
    if "Error" in tx:
        await msg.edit_text(f"❌ Failed: {tx}")
    else:
        entry = await jup.get_price(data['contract'])
        # Estimate tokens
        price = entry if entry else 0.000001
        est_tokens = (data['amount'] * await jup.get_price(jup.SOL_MINT)) / price
        
        await db.add_trade(user, data['contract'], est_tokens, entry, data['sl'], data['tp'])
        await msg.edit_text(
            f"✅ **Snipe Successful!**\n\n"
            f"Entry: ${entry:.6f}\n"
            f"SL/TP: {data['sl']}% / {data['tp']}%\n"
            f"TX: `https://solscan.io/tx/{tx}`\n\n"
            f"🤖 Monitor Activated.", 
            parse_mode="Markdown"
        )
    await state.clear()

# --- BACKGROUND MONITOR ---
async def monitor_market():
    while True:
        await asyncio.sleep(15) # Check every 15s
        try:
            trades = await db.get_active_trades()
            for trade in trades:
                tid, uid, mint, amt, entry, sl, tp = trade
                
                # If session lost (Render restart), skip trade until user logs in again
                if uid not in ACTIVE_SESSIONS: continue
                
                curr_price = await jup.get_price(mint)
                if not curr_price: continue
                
                pnl = ((curr_price - entry) / entry) * 100
                
                if pnl <= sl or pnl >= tp:
                    kp = ACTIVE_SESSIONS[uid]
                    # Swap ALL tokens back to SOL
                    # Note: We use estimated amount here. Real prod bots fetch wallet balance first.
                    tx = await jup.execute_swap(kp, mint, jup.SOL_MINT, int(amt), RPC_URL)
                    
                    if "Error" not in tx:
                        await db.delete_trade(tid)
                        await bot.send_message(uid, f"🔔 **Auto-Sell Triggered**\nReason: {pnl:.2f}%\nTX: `https://solscan.io/tx/{tx}`")
        except Exception as e:
            logging.error(f"Monitor Error: {e}")

# --- CATCH-ALL HANDLER (Unrecognized Messages) ---
@dp.message()
async def unknown_command(message: types.Message):
    # This catches ANY text that isn't handled above
    await message.answer(
        "❓ I didn't understand that command.\n"
        "Please use the menu below:",
        reply_markup=get_main_menu()
    )

# --- MAIN ENTRY ---
async def main():
    await db.init_db()
    
    # 1. Start Render Keep-Alive
    await start_web_server()
    
    # 2. Start Background Monitor
    asyncio.create_task(monitor_market())
    
    # 3. Start Bot (Drop pending updates to prevent conflicts on restart)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())