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

if not BOT_TOKEN: logging.error("❌ ERROR: Missing BOT_TOKEN")

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

logging.basicConfig(level=logging.INFO)

# --- WEB SERVER ---
async def handle_ping(request): return web.Response(text="Bot is running!")
async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()

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
    except: return None

# --- MENUS ---
def get_start_button():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🚀 Launch ProDex Bot")]], resize_keyboard=True)

def get_main_menu():
    kb = [
        [KeyboardButton(text="💰 Wallet Balance"), KeyboardButton(text="💸 Send SOL")],
        [KeyboardButton(text="🚀 Auto-Buy / Snipe"), KeyboardButton(text="⚙️ Settings / Wallet")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_cancel_menu():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Cancel Operation")]], resize_keyboard=True)

def get_wallet_menu():
    kb = [
        [KeyboardButton(text="🔐 Create New Wallet"), KeyboardButton(text="📥 Import Wallet")],
        [KeyboardButton(text="🔑 Recover (Passphrase)"), KeyboardButton(text="🗑️ Reset Wallet (Danger)")],
        [KeyboardButton(text="⬅️ Back to Main Menu")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# --- STATES ---
class BotStates(StatesGroup):
    create_pin = State()
    
    # Import
    import_key = State()
    import_pin = State()
    
    # Reset
    reset_pin = State()
    reset_confirm = State()
    
    # Recover
    recover_phrase = State()
    recover_pin = State()
    
    # Trading
    trade_contract = State()
    trade_mc = State()
    trade_amount = State()
    trade_risk = State()
    trade_pin = State()
    
    send_dest = State()
    send_amount = State()
    send_pin = State()

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- CANCEL ---
@dp.message(F.text == "❌ Cancel Operation")
@dp.message(Command("cancel"))
async def cancel_handler(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("🚫 Cancelled.", reply_markup=get_main_menu())

# --- START FLOW ---
@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("👋 **Welcome to ProDex**\n\nClick below to access the main menu.", reply_markup=get_start_button())

@dp.message(F.text == "🚀 Launch ProDex Bot")
async def launch_menu(message: types.Message):
    await message.answer("✅ **Main Menu**", reply_markup=get_main_menu())

# --- SETTINGS MENU ---
@dp.message(F.text == "⚙️ Settings / Wallet")
async def settings_menu(message: types.Message):
    await message.answer("⚙️ **Wallet Options**", reply_markup=get_wallet_menu())

@dp.message(F.text == "⬅️ Back to Main Menu")
async def back_to_main(message: types.Message):
    await message.answer("🔙 Main Menu", reply_markup=get_main_menu())

# 1. CREATE WALLET
@dp.message(F.text == "🔐 Create New Wallet")
async def create_wallet_check(message: types.Message, state: FSMContext):
    if await db.get_wallet(message.from_user.id):
        return await message.answer("⚠️ Wallet exists. Use 'Reset' to wipe it first.", reply_markup=get_wallet_menu())
    await message.answer("👉 Set a **6-digit PIN**:", reply_markup=get_cancel_menu())
    await state.set_state(BotStates.create_pin)

@dp.message(BotStates.create_pin)
async def process_create_pin(message: types.Message, state: FSMContext):
    pin = message.text.strip()
    if not pin.isdigit() or len(pin) < 4: return await message.answer("❌ PIN must be 4+ digits.")
    
    mnemo = Mnemonic("english")
    words = mnemo.generate(strength=128)
    seed = mnemo.to_seed(words)
    kp = Keypair.from_seed(seed[:32])
    
    enc_key, salt = encrypt_key(bytes(kp), pin)
    await db.add_wallet(message.from_user.id, str(kp.pubkey()), enc_key, salt)
    
    await message.answer(
        f"✅ **Created!**\nAddress: `{kp.pubkey()}`\n\n📜 **SAVE THIS PHRASE:**\n`{words}`", 
        parse_mode="Markdown", reply_markup=get_main_menu()
    )
    await state.clear()

# 2. IMPORT WALLET
@dp.message(F.text == "📥 Import Wallet")
async def import_wallet_start(message: types.Message, state: FSMContext):
    if await db.get_wallet(message.from_user.id):
        return await message.answer("⚠️ Wallet exists. Reset first.", reply_markup=get_wallet_menu())
    await message.answer("🔐 **Paste your Private Key (Base58):**", reply_markup=get_cancel_menu())
    await state.set_state(BotStates.import_key)

@dp.message(BotStates.import_key)
async def process_import_key(message: types.Message, state: FSMContext):
    try:
        pk_str = message.text.strip()
        kp = Keypair.from_base58_string(pk_str)
        await state.update_data(kp=kp)
        await message.answer("✅ Key Valid.\n👉 **Set a PIN** to secure it:", reply_markup=get_cancel_menu())
        await state.set_state(BotStates.import_pin)
    except:
        await message.answer("❌ Invalid Key. Try again.")

@dp.message(BotStates.import_pin)
async def process_import_pin(message: types.Message, state: FSMContext):
    pin = message.text.strip()
    if not pin.isdigit() or len(pin) < 4: return await message.answer("❌ PIN must be 4+ digits.")
    
    data = await state.get_data()
    kp = data['kp']
    enc_key, salt = encrypt_key(bytes(kp), pin)
    await db.add_wallet(message.from_user.id, str(kp.pubkey()), enc_key, salt)
    
    await message.answer(f"✅ **Imported!**\nAddress: `{kp.pubkey()}`", parse_mode="Markdown", reply_markup=get_main_menu())
    await state.clear()

# 3. SECURE RESET (Wipe)
@dp.message(F.text == "🗑️ Reset Wallet (Danger)")
async def reset_ask_pin(message: types.Message, state: FSMContext):
    await message.answer("🔒 **Enter PIN to confirm deletion:**\n(Or type 'LOST' if you forgot it)", reply_markup=get_cancel_menu())
    await state.set_state(BotStates.reset_pin)

@dp.message(BotStates.reset_pin)
async def process_reset_pin(message: types.Message, state: FSMContext):
    text = message.text.strip()
    wallet = await db.get_wallet(message.from_user.id)
    
    if text == "LOST":
        await message.answer("⚠️ **FORCED WIPE**\nType **CONFIRM** to delete without PIN.", reply_markup=get_cancel_menu())
        await state.set_state(BotStates.reset_confirm)
        return

    if not wallet: 
        await message.answer("❌ No wallet to delete.", reply_markup=get_wallet_menu())
        return await state.clear()

    decrypted = decrypt_key(wallet[0], wallet[1], text)
    if decrypted:
        await db.delete_wallet(message.from_user.id)
        await message.answer("🗑️ **Wallet Deleted Successfully.**", reply_markup=get_wallet_menu())
        await state.clear()
    else:
        await message.answer("❌ Wrong PIN. Try again or type 'LOST'.")

@dp.message(BotStates.reset_confirm)
async def process_reset_force(message: types.Message, state: FSMContext):
    if message.text.strip().upper() == "CONFIRM":
        await db.delete_wallet(message.from_user.id)
        await message.answer("🗑️ **Wallet Force Deleted.**", reply_markup=get_wallet_menu())
    else:
        await message.answer("❌ Cancelled.", reply_markup=get_main_menu())
    await state.clear()

# --- BALANCE (FIXED) ---
@dp.message(F.text == "💰 Wallet Balance")
async def check_balance(message: types.Message):
    wallet = await db.get_wallet(message.from_user.id)
    if not wallet: return await message.answer("❌ No wallet found.")
    
    pub_key = wallet[2]
    # Send temporary message
    msg = await message.answer("⏳ Checking Blockchain...")
    
    try:
        # Check Balance
        bal_lamports = await jup.get_sol_balance(RPC_URL, pub_key)
        sol_bal = bal_lamports / 1_000_000_000
        
        # Check Price
        price = await jup.get_price(jup.SOL_MINT)
        usd_val = (sol_bal * price) if price else 0.0
        
        # FIX: Delete loader, send NEW message to attach Main Menu
        await msg.delete()
        await message.answer(
            f"💰 **Wallet Balance**\n"
            f"`{pub_key}`\n\n"
            f"SOL: **{sol_bal:.4f}**\n"
            f"USD: **${usd_val:.2f}**",
            parse_mode="Markdown", reply_markup=get_main_menu()
        )
    except Exception as e:
        # Even on error, delete loader and show error
        await msg.delete()
        await message.answer(f"❌ Error fetching balance: {str(e)}", reply_markup=get_main_menu())

# --- RECOVERY (Passphrase) ---
@dp.message(F.text == "🔑 Recover (Passphrase)")
async def recover_start(message: types.Message, state: FSMContext):
    await message.answer("📝 **Paste 12-word Phrase:**", reply_markup=get_cancel_menu())
    await state.set_state(BotStates.recover_phrase)

@dp.message(BotStates.recover_phrase)
async def recover_phrase(message: types.Message, state: FSMContext):
    mnemo = Mnemonic("english")
    phrase = message.text.strip()
    if not mnemo.check(phrase): return await message.answer("❌ Invalid Phrase.")
    
    await state.update_data(phrase=phrase)
    await message.answer("✅ Valid. Enter **NEW PIN**:", reply_markup=get_cancel_menu())
    await state.set_state(BotStates.recover_pin)

@dp.message(BotStates.recover_pin)
async def recover_pin(message: types.Message, state: FSMContext):
    pin = message.text.strip()
    if not pin.isdigit() or len(pin) < 4: return await message.answer("❌ PIN must be 4+ digits.")
    
    data = await state.get_data()
    kp = Keypair.from_seed(Mnemonic("english").to_seed(data['phrase'])[:32])
    
    enc_key, salt = encrypt_key(bytes(kp), pin)
    await db.add_wallet(message.from_user.id, str(kp.pubkey()), enc_key, salt)
    
    await message.answer(f"✅ **Recovered!**\nAddress: `{kp.pubkey()}`", parse_mode="Markdown", reply_markup=get_main_menu())
    await state.clear()

# --- SEND SOL (FIXED) ---
@dp.message(F.text == "💸 Send SOL")
async def send_start(message: types.Message, state: FSMContext):
    await message.answer("📤 **Destination:**", reply_markup=get_cancel_menu())
    await state.set_state(BotStates.send_dest)

@dp.message(BotStates.send_dest)
async def send_dest(message: types.Message, state: FSMContext):
    await state.update_data(dest=message.text.strip())
    await message.answer("💸 **Amount:**")
    await state.set_state(BotStates.send_amount)

@dp.message(BotStates.send_amount)
async def send_amt(message: types.Message, state: FSMContext):
    try:
        await state.update_data(amount=float(message.text))
        if message.from_user.id in ACTIVE_SESSIONS: await execute_transfer(message, state)
        else:
            await message.answer("🔐 **PIN:**")
            await state.set_state(BotStates.send_pin)
    except: await message.answer("❌ Invalid number.")

@dp.message(BotStates.send_pin)
async def send_pin_proc(message: types.Message, state: FSMContext):
    wallet = await db.get_wallet(message.from_user.id)
    decrypted = decrypt_key(wallet[0], wallet[1], message.text.strip()) if wallet else None
    if not decrypted: return await message.answer("❌ Wrong PIN.")
    ACTIVE_SESSIONS[message.from_user.id] = Keypair.from_bytes(decrypted)
    await execute_transfer(message, state)

async def execute_transfer(message, state):
    data = await state.get_data()
    sender = ACTIVE_SESSIONS[message.from_user.id]
    msg = await message.answer("⏳ Sending...")
    try:
        async with AsyncClient(RPC_URL) as client:
            lamports = int(data['amount'] * 1_000_000_000)
            ix = transfer(TransferParams(from_pubkey=sender.pubkey(), to_pubkey=Pubkey.from_string(data['dest']), lamports=lamports))
            tx = await client.send_transaction(Transaction().add(ix), sender, opts=TxOpts(skip_preflight=True))
            
            # FIX: Delete loader, send new
            await msg.delete()
            await message.answer(f"✅ **Sent:** `https://solscan.io/tx/{tx.value}`", parse_mode="Markdown", reply_markup=get_main_menu())
    except Exception as e:
        await msg.delete()
        await message.answer(f"❌ Error: {e}", reply_markup=get_main_menu())
    await state.clear()

# --- AUTO-BUY (FIXED) ---
@dp.message(F.text == "🚀 Auto-Buy / Snipe")
async def buy_start(message: types.Message, state: FSMContext):
    await message.answer("📝 **Token Address:**", reply_markup=get_cancel_menu())
    await state.set_state(BotStates.trade_contract)

@dp.message(BotStates.trade_contract)
async def buy_contract(message: types.Message, state: FSMContext):
    await state.update_data(contract=message.text.strip())
    await message.answer("📊 **Max MC ($)?** (0 to skip)")
    await state.set_state(BotStates.trade_mc)

@dp.message(BotStates.trade_mc)
async def buy_mc(message: types.Message, state: FSMContext):
    try:
        await state.update_data(max_mc=float(message.text))
        await message.answer("💰 **SOL Amount:**")
        await state.set_state(BotStates.trade_amount)
    except: await message.answer("❌ Number only.")

@dp.message(BotStates.trade_amount)
async def buy_amt(message: types.Message, state: FSMContext):
    try:
        await state.update_data(amount=float(message.text))
        await message.answer("📉 **SL / TP %** (e.g. `-20 100`):")
        await state.set_state(BotStates.trade_risk)
    except: await message.answer("❌ Number only.")

@dp.message(BotStates.trade_risk)
async def buy_risk(message: types.Message, state: FSMContext):
    try:
        sl, tp = map(float, message.text.split())
        await state.update_data(sl=sl, tp=tp)
        if message.from_user.id in ACTIVE_SESSIONS: await execute_trade(message, state)
        else:
            await message.answer("🔐 **PIN:**")
            await state.set_state(BotStates.trade_pin)
    except: await message.answer("❌ Format: -20 100")

@dp.message(BotStates.trade_pin)
async def buy_pin(message: types.Message, state: FSMContext):
    wallet = await db.get_wallet(message.from_user.id)
    decrypted = decrypt_key(wallet[0], wallet[1], message.text.strip()) if wallet else None
    if not decrypted: return await message.answer("❌ Wrong PIN.")
    ACTIVE_SESSIONS[message.from_user.id] = Keypair.from_bytes(decrypted)
    await execute_trade(message, state)

async def execute_trade(message, state):
    data = await state.get_data()
    user_id = message.from_user.id
    kp = ACTIVE_SESSIONS[user_id]
    msg = await message.answer("⏳ Processing...")
    
    # 1. MC Check
    if data['max_mc'] > 0:
        mc = await jup.get_market_cap(data['contract'], RPC_URL)
        if mc and mc > data['max_mc']:
            await msg.delete()
            await message.answer(f"⚠️ MC too high: ${mc:,.0f}", reply_markup=get_main_menu())
            return await state.clear()
            
    # 2. Swap
    tx = await jup.execute_swap(kp, jup.SOL_MINT, data['contract'], int(data['amount']*1e9), RPC_URL)
    
    await msg.delete() # FIX: Clear loader
    if "Error" in tx:
        await message.answer(f"❌ {tx}", reply_markup=get_main_menu())
    else: 
        await db.add_trade(user_id, data['contract'], 0, 0, data['sl'], data['tp'])
        await message.answer(f"✅ **Success:** `https://solscan.io/tx/{tx}`", parse_mode="Markdown", reply_markup=get_main_menu())
    await state.clear()

# --- BACKGROUND MONITOR ---
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
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())