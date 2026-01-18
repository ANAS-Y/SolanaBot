import asyncio
import logging
import os
import sys
import base64
from aiohttp import web
from dotenv import load_dotenv

# --- RENDER CONFIGURATION ---
# Render gives us a PORT environment variable. We must listen on it.
PORT = int(os.environ.get("PORT", 8080))

# Load Env
load_dotenv()
# On Render, we set these secrets in the dashboard, so we don't need a .env file there.
BOT_TOKEN = os.getenv("BOT_TOKEN")
# Use a public RPC as fallback if the private one isn't set
RPC_URL = os.getenv("RPC_URL", "https://api.mainnet-beta.solana.com")

if not BOT_TOKEN:
    print("❌ ERROR: Missing BOT_TOKEN")
    # Don't exit here on Render, or it crashes. Just log it.

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from solders.keypair import Keypair 
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import database as db
import jupiter as jup

# --- LOGGING ---
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

# --- BOT HANDLERS ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
class BotStates(StatesGroup):
    create_pin = State()
    trade_contract = State()
    trade_amount = State()
    trade_risk = State()
    trade_pin = State()

@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer("🚀 **Solana Sniper Bot (Render Edition)**\n\n/create_wallet - Setup\n/buy - Auto-Trade")

@dp.message(Command("create_wallet"))
async def create_wallet(message: types.Message, state: FSMContext):
    if await db.get_wallet(message.from_user.id):
        return await message.answer("⚠️ Wallet already exists.")
    await message.answer("🔐 Set a 6-digit PIN:")
    await state.set_state(BotStates.create_pin)

@dp.message(BotStates.create_pin)
async def process_create_pin(message: types.Message, state: FSMContext):
    pin = message.text.strip()
    kp = Keypair()
    enc_key, salt = encrypt_key(bytes(kp), pin)
    await db.add_wallet(message.from_user.id, str(kp.pubkey()), enc_key, salt)
    await message.answer(f"✅ **Wallet Created**\nAddress: `{kp.pubkey()}`\n\nSend SOL here to trade.", parse_mode="Markdown")
    await state.clear()

@dp.message(Command("buy"))
async def buy_start(message: types.Message, state: FSMContext):
    await message.answer("📝 Paste Token Contract Address:")
    await state.set_state(BotStates.trade_contract)

@dp.message(BotStates.trade_contract)
async def process_contract(message: types.Message, state: FSMContext):
    await state.update_data(contract=message.text.strip())
    await message.answer("💰 Amount of SOL to buy:")
    await state.set_state(BotStates.trade_amount)

@dp.message(BotStates.trade_amount)
async def process_amount(message: types.Message, state: FSMContext):
    await state.update_data(amount=float(message.text))
    await message.answer("📊 Set Stop Loss % and Take Profit % (e.g., -20 50):")
    await state.set_state(BotStates.trade_risk)

@dp.message(BotStates.trade_risk)
async def process_risk(message: types.Message, state: FSMContext):
    sl, tp = map(float, message.text.split())
    await state.update_data(sl=sl, tp=tp)
    if message.from_user.id in ACTIVE_SESSIONS:
        await execute_trade(message, state)
    else:
        await message.answer("🔐 Enter PIN to authorize trade:")
        await state.set_state(BotStates.trade_pin)

@dp.message(BotStates.trade_pin)
async def process_pin(message: types.Message, state: FSMContext):
    # 1. Try to get the wallet from the DB
    wallet = await db.get_wallet(message.from_user.id)

    # 2. SAFETY CHECK: If wallet is missing (Render wiped DB), stop here.
    if not wallet:
        await message.answer("⚠️ **Wallet Not Found!**\n\n"
                             "Since you are on Render Free Tier, the database resets on every restart.\n"
                             "Please create a new wallet using /create_wallet")
        await state.clear()
        return

    # 3. If wallet exists, proceed to decrypt
    decrypted = decrypt_key(wallet[0], wallet[1], message.text.strip())
    
    if not decrypted: 
        return await message.answer("❌ Wrong PIN.")
    
    ACTIVE_SESSIONS[message.from_user.id] = Keypair.from_bytes(decrypted)
    await execute_trade(message, state)

async def execute_trade(message, state):
    data = await state.get_data()
    user = message.from_user.id
    kp = ACTIVE_SESSIONS[user]
    msg = await message.answer("⏳ Swapping...")
    lamports = data['amount'] * 1_000_000_000
    tx = await jup.execute_swap(kp, jup.SOL_MINT, data['contract'], lamports, RPC_URL)
    if "Error" in tx:
        await msg.edit_text(f"❌ Failed: {tx}")
    else:
        entry = await jup.get_price(data['contract'])
        # Estimate tokens (simplification)
        est_tokens = (data['amount'] * await jup.get_price(jup.SOL_MINT)) / (entry if entry else 1)
        await db.add_trade(user, data['contract'], est_tokens, entry, data['sl'], data['tp'])
        await msg.edit_text(f"✅ **Bought!**\nTX: `https://solscan.io/tx/{tx}`\nMonitor Active.", parse_mode="Markdown")
    await state.clear()

async def monitor_market():
    while True:
        await asyncio.sleep(10)
        trades = await db.get_active_trades()
        for trade in trades:
            tid, uid, mint, amt, entry, sl, tp = trade
            if uid not in ACTIVE_SESSIONS: continue
            curr_price = await jup.get_price(mint)
            if not curr_price: continue
            pnl = ((curr_price - entry) / entry) * 100
            if pnl <= sl or pnl >= tp:
                kp = ACTIVE_SESSIONS[uid]
                tx = await jup.execute_swap(kp, mint, jup.SOL_MINT, int(amt), RPC_URL)
                if "Error" not in tx:
                    await db.delete_trade(tid)
                    await bot.send_message(uid, f"🔔 **Auto-Sell**\nReason: {pnl:.2f}%\nTX: {tx}")

async def main():
    await db.init_db()
    # 1. Start the Dummy Web Server (For Render)
    await start_web_server()
    # 2. Start the Background Monitor
    asyncio.create_task(monitor_market())
    # 3. Start the Telegram Bot
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())