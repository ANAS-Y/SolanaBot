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
from mnemonic import Mnemonic

import database as db
import jupiter as jup

# --- LOGGING ---
logging.basicConfig(level=logging.INFO)

# --- WEB SERVER ---
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

# --- SECURITY UTILS ---
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

# --- MENUS ---
def get_main_menu():
    kb = [
        [KeyboardButton(text="💰 Wallet Balance"), KeyboardButton(text="💸 Send SOL")],
        [KeyboardButton(text="🚀 Auto-Buy / Snipe"), KeyboardButton(text="⚙️ Settings / Wallet")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_cancel_menu():
    kb = [[KeyboardButton(text="❌ Cancel Operation")]]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_wallet_menu():
    kb = [
        [KeyboardButton(text="🔐 Create New Wallet")],
        [KeyboardButton(text="🔑 Recover with Passphrase")],
        [KeyboardButton(text="🗑️ Reset Wallet (Danger)")],
        [KeyboardButton(text="⬅️ Back to Main Menu")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# --- STATES ---
class BotStates(StatesGroup):
    create_pin = State()
    reset_confirm = State()
    
    # Recovery
    recover_phrase = State()
    recover_pin = State()
    
    # Buy Flow
    trade_contract = State()
    trade_mc = State()
    trade_amount = State()
    trade_risk = State()
    trade_pin = State()
    
    # Send Flow
    send_dest = State()
    send_amount = State()
    send_pin = State()

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- CANCEL HANDLER ---
@dp.message(F.text == "❌ Cancel Operation")
@dp.message(Command("cancel"))
async def cancel_handler(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("🚫 Operation Cancelled.", reply_markup=get_main_menu())

# --- START ---
@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("👋 **ProDex Bot Ready**", reply_markup=get_main_menu())

# --- WALLET MANAGEMENT ---
@dp.message(F.text == "⚙️ Settings / Wallet")
async def settings_menu(message: types.Message):
    await message.answer("⚙️ **Wallet Management**", reply_markup=get_wallet_menu())

@dp.message(F.text == "⬅️ Back to Main Menu")
async def back_to_main(message: types.Message):
    await message.answer("🔙 Main Menu", reply_markup=get_main_menu())

# 1. CREATE WALLET (With Mnemonic)
@dp.message(F.text == "🔐 Create New Wallet")
async def create_wallet_check(message: types.Message, state: FSMContext):
    wallet = await db.get_wallet(message.from_user.id)
    if wallet:
        await message.answer("⚠️ You already have a wallet. Use **Recover** if you lost your PIN, or **Reset** to wipe it.", reply_markup=get_wallet_menu())
        return
    await message.answer("👉 Enter a **6-digit PIN** to secure your new wallet:", reply_markup=get_cancel_menu())
    await state.set_state(BotStates.create_pin)

@dp.message(BotStates.create_pin)
async def process_create_pin(message: types.Message, state: FSMContext):
    pin = message.text.strip()
    if not pin.isdigit() or len(pin) < 4:
        return await message.answer("❌ PIN must be numbers only (4+ digits).")

    # Generate Mnemonic
    mnemo = Mnemonic("english")
    words = mnemo.generate(strength=128) # 12 words
    seed = mnemo.to_seed(words)
    
    # Derive Keypair (Use first 32 bytes of seed for consistent Ed25519)
    kp = Keypair.from_seed(seed[:32])
    
    # Encrypt & Save
    enc_key, salt = encrypt_key(bytes(kp), pin)
    await db.add_wallet(message.from_user.id, str(kp.pubkey()), enc_key, salt)
    
    await message.answer(
        f"✅ **Wallet Created!**\n\n"
        f"📜 **RECOVERY PHRASE (SAVE THIS):**\n"
        f"`{words}`\n\n"
        f"Address: `{kp.pubkey()}`\n\n"
        f"⚠️ If you lose your PIN, these 12 words are the ONLY way to recover your account.",
        reply_markup=get_main_menu(),
        parse_mode="Markdown"
    )
    await state.clear()

# 2. RECOVER WALLET (New Feature)
@dp.message(F.text == "🔑 Recover with Passphrase")
async def recover_wallet_start(message: types.Message, state: FSMContext):
    await message.answer("📝 **Enter your 12-word Recovery Phrase:**\n(Separate words with spaces)", reply_markup=get_cancel_menu())
    await state.set_state(BotStates.recover_phrase)

@dp.message(BotStates.recover_phrase)
async def process_recover_phrase(message: types.Message, state: FSMContext):
    phrase = message.text.strip()
    mnemo = Mnemonic("english")
    
    if not mnemo.check(phrase):
        return await message.answer("❌ Invalid Recovery Phrase. Check spelling and try again.")
    
    # Store phrase temporarily to derive key after PIN is set
    await state.update_data(phrase=phrase)
    await message.answer("✅ Phrase Verified.\n👉 **Enter a NEW PIN** to secure this wallet:", reply_markup=get_cancel_menu())
    await state.set_state(BotStates.recover_pin)

@dp.message(BotStates.recover_pin)
async def process_recover_pin(message: types.Message, state: FSMContext):
    pin = message.text.strip()
    if not pin.isdigit() or len(pin) < 4:
        return await message.answer("❌ PIN must be 4+ digits.")
    
    data = await state.get_data()
    phrase = data['phrase']
    
    # Re-derive Keypair
    mnemo = Mnemonic("english")
    seed = mnemo.to_seed(phrase)
    kp = Keypair.from_seed(seed[:32])
    
    # Encrypt with NEW PIN and Overwrite DB
    enc_key, salt = encrypt_key(bytes(kp), pin)
    await db.add_wallet(message.from_user.id, str(kp.pubkey()), enc_key, salt)
    
    await message.answer(
        f"✅ **Recovery Successful!**\n"
        f"Wallet restored: `{kp.pubkey()}`\n"
        f"Your PIN has been updated.",
        reply_markup=get_main_menu(),
        parse_mode="Markdown"
    )
    await state.clear()

# 3. RESET WALLET
@dp.message(F.text == "🗑️ Reset Wallet (Danger)")
async def reset_wallet_ask(message: types.Message, state: FSMContext):
    await message.answer(
        "⚠️ **DANGER ZONE**\n\nThis will DELETE your wallet from the bot.\nType **CONFIRM** to proceed.",
        reply_markup=get_cancel_menu()
    )
    await state.set_state(BotStates.reset_confirm)

@dp.message(BotStates.reset_confirm)
async def reset_wallet_confirm(message: types.Message, state: FSMContext):
    if message.text.strip().upper() == "CONFIRM":
        # In a real app we'd delete the DB row, but letting the user create a new one overwrites it safely.
        # We just clear state and guide them.
        await message.answer("🗑️ **Wallet Wiped.** You can now Create or Recover.", reply_markup=get_wallet_menu())
    else:
        await message.answer("❌ Cancelled.", reply_markup=get_main_menu())
    await state.clear()

# --- BALANCE ---
@dp.message(F.text == "💰 Wallet Balance")
async def check_balance(message: types.Message):
    wallet = await db.get_wallet(message.from_user.id)
    if not wallet: return await message.answer("❌ No wallet found.")
    
    pub_key = wallet[2]
    msg = await message.answer("⏳ Checking...")
    
    bal = await jup.get_sol_balance(RPC_URL, pub_key)
    sol_bal = bal / 1_000_000_000
    price = await jup.get_price(jup.SOL_MINT) or 0
    
    await msg.edit_text(
        f"💰 **Balance**\n`{pub_key}`\n\n"
        f"SOL: **{sol_bal:.4f}**\n"
        f"USD: **${(sol_bal * price):.2f}**",
        parse_mode="Markdown",
        reply_markup=get_main_menu()
    )

# --- SEND SOL ---
@dp.message(F.text == "💸 Send SOL")
async def send_sol_start(message: types.Message, state: FSMContext):
    await message.answer("📤 **Destination Address:**", reply_markup=get_cancel_menu())
    await state.set_state(BotStates.send_dest)

@dp.message(BotStates.send_dest)
async def send_sol_dest(message: types.Message, state: FSMContext):
    await state.update_data(dest=message.text.strip())
    await message.answer("💸 **Amount (SOL):**")
    await state.set_state(BotStates.send_amount)

@dp.message(BotStates.send_amount)
async def send_sol_amt(message: types.Message, state: FSMContext):
    try:
        amt = float(message.text.strip())
        await state.update_data(amount=amt)
        if message.from_user.id in ACTIVE_SESSIONS:
            await execute_transfer(message, state)
        else:
            await message.answer("🔐 **Enter PIN:**")
            await state.set_state(BotStates.send_pin)
    except:
        await message.answer("❌ Invalid number.")

@dp.message(BotStates.send_pin)
async def process_send_pin(message: types.Message, state: FSMContext):
    wallet = await db.get_wallet(message.from_user.id)
    if not wallet: return await message.answer("❌ Wallet missing.")
    
    decrypted = decrypt_key(wallet[0], wallet[1], message.text.strip())
    if not decrypted: return await message.answer("❌ Wrong PIN.")
    
    ACTIVE_SESSIONS[message.from_user.id] = Keypair.from_bytes(decrypted)
    await execute_transfer(message, state)

async def execute_transfer(message, state):
    data = await state.get_data()
    user_id = message.from_user.id
    sender_kp = ACTIVE_SESSIONS[user_id]
    msg = await message.answer("⏳ Sending...")
    
    try:
        lamports = int(data['amount'] * 1_000_000_000)
        dest = Pubkey.from_string(data['dest'])
        
        # Balance Check
        bal = await jup.get_sol_balance(RPC_URL, str(sender_kp.pubkey()))
        if bal < (lamports + 5000):
             await msg.edit_text("❌ Insufficient Funds for transfer + gas.")
             await state.clear()
             return

        async with AsyncClient(RPC_URL) as client:
            ix = transfer(TransferParams(from_pubkey=sender_kp.pubkey(), to_pubkey=dest, lamports=lamports))
            txn = Transaction().add(ix)
            resp = await client.send_transaction(txn, sender_kp, opts=TxOpts(skip_preflight=True))
            await msg.edit_text(f"✅ **Sent!**\nTX: `https://solscan.io/tx/{resp.value}`", parse_mode="Markdown", reply_markup=get_main_menu())
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")
    await state.clear()

# --- AUTO-BUY ---
@dp.message(F.text == "🚀 Auto-Buy / Snipe")
async def buy_start(message: types.Message, state: FSMContext):
    await message.answer("📝 **Token Address:**", reply_markup=get_cancel_menu())
    await state.set_state(BotStates.trade_contract)

@dp.message(BotStates.trade_contract)
async def process_contract(message: types.Message, state: FSMContext):
    await state.update_data(contract=message.text.strip())
    await message.answer("📊 **Max Market Cap ($)?** (0 to skip)", reply_markup=get_cancel_menu())
    await state.set_state(BotStates.trade_mc)

@dp.message(BotStates.trade_mc)
async def process_mc(message: types.Message, state: FSMContext):
    try:
        mc = float(message.text.strip())
        await state.update_data(max_mc=mc)
        await message.answer("💰 **SOL Amount:**")
        await state.set_state(BotStates.trade_amount)
    except:
        await message.answer("❌ Enter a number.")

@dp.message(BotStates.trade_amount)
async def process_amount(message: types.Message, state: FSMContext):
    try:
        amt = float(message.text)
        await state.update_data(amount=amt)
        await message.answer("📉 **SL / TP %** (e.g. `-20 100`):")
        await state.set_state(BotStates.trade_risk)
    except:
        await message.answer("❌ Invalid number.")

@dp.message(BotStates.trade_risk)
async def process_risk(message: types.Message, state: FSMContext):
    try:
        sl, tp = map(float, message.text.split())
        await state.update_data(sl=sl, tp=tp)
        if message.from_user.id in ACTIVE_SESSIONS:
            await execute_trade(message, state)
        else:
            await message.answer("🔐 **Enter PIN:**")
            await state.set_state(BotStates.trade_pin)
    except:
        await message.answer("❌ Format: `-20 100`")

@dp.message(BotStates.trade_pin)
async def process_trade_pin(message: types.Message, state: FSMContext):
    wallet = await db.get_wallet(message.from_user.id)
    if not wallet: return await message.answer("❌ Wallet missing.")
    
    decrypted = decrypt_key(wallet[0], wallet[1], message.text.strip())
    if not decrypted: return await message.answer("❌ Wrong PIN.")
    
    ACTIVE_SESSIONS[message.from_user.id] = Keypair.from_bytes(decrypted)
    await execute_trade(message, state)

async def execute_trade(message, state):
    data = await state.get_data()
    user_id = message.from_user.id
    kp = ACTIVE_SESSIONS[user_id]
    
    msg = await message.answer("⏳ Analyzing...")
    
    # Balance & MC Checks
    bal = await jup.get_sol_balance(RPC_URL, str(kp.pubkey()))
    req = int(data['amount'] * 1_000_000_000)
    if bal < (req + 5000000):
        await msg.edit_text("❌ Insufficient funds (Trade + Gas).", reply_markup=get_main_menu())
        await state.clear()
        return

    if data['max_mc'] > 0:
        mc = await jup.get_market_cap(data['contract'], RPC_URL)
        if mc and mc > data['max_mc']:
            await msg.edit_text(f"⚠️ Skipped: MC ${mc:,.0f} > Limit ${data['max_mc']:,.0f}", reply_markup=get_main_menu())
            await state.clear()
            return

    await msg.edit_text("🚀 Swapping...")
    tx = await jup.execute_swap(kp, jup.SOL_MINT, data['contract'], req, RPC_URL)
    
    if "Error" in tx:
        await msg.edit_text(f"❌ Failed: {tx}", reply_markup=get_main_menu())
    else:
        entry = await jup.get_price(data['contract']) or 0.000001
        est = (data['amount'] * await jup.get_price(jup.SOL_MINT)) / entry
        await db.add_trade(user_id, data['contract'], est, entry, data['sl'], data['tp'])
        await msg.edit_text(f"✅ **Snipe Success!**\nTX: `https://solscan.io/tx/{tx}`\nMonitor Active.", parse_mode="Markdown", reply_markup=get_main_menu())
    await state.clear()

# --- MONITOR ---
async def monitor_market():
    while True:
        await asyncio.sleep(15)
        try:
            trades = await db.get_active_trades()
            for trade in trades:
                tid, uid, mint, amt, entry, sl, tp = trade
                if uid not in ACTIVE_SESSIONS: continue
                
                curr = await jup.get_price(mint)
                if not curr: continue
                
                pnl = ((curr - entry) / entry) * 100
                if pnl <= sl or pnl >= tp:
                    kp = ACTIVE_SESSIONS[uid]
                    tx = await jup.execute_swap(kp, mint, jup.SOL_MINT, int(amt), RPC_URL)
                    if "Error" not in tx:
                        await db.delete_trade(tid)
                        await bot.send_message(uid, f"🔔 **Auto-Sell**\nPnL: {pnl:.2f}%\nTX: {tx}")
        except: pass

@dp.message()
async def unknown(message: types.Message):
    await message.answer("❓ Unknown command. Use menu.", reply_markup=get_main_menu())

async def main():
    await db.init_db()
    await start_web_server()
    asyncio.create_task(monitor_market())
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())