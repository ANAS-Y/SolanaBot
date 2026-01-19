import asyncio
import logging
import os
import sys
import config 
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiohttp import web

# Import custom modules
import database as db
import data_engine
import sentinel_ai
import jupiter as jup

# --- LOGGING ---
logging.basicConfig(level=logging.INFO)

# --- CONFIG CHECK ---
if not config.BOT_TOKEN:
    logging.critical("CRITICAL: BOT_TOKEN is missing in Render Environment!")
    sys.exit(1)

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()

# --- WEB SERVER ---
async def health_check(request):
    return web.Response(text="Sentinel AI is running", status=200)

async def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"Web Server started on port {port}")

# --- STATES ---
class BotStates(StatesGroup):
    waiting_for_token = State()

# --- MENUS ---
def get_launch_menu():
    kb = [[KeyboardButton(text="🚀 Launch App")]]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_main_menu():
    kb = [
        [KeyboardButton(text="🧠 AI Analysis"), KeyboardButton(text="🛡️ Safety Check")],
        [KeyboardButton(text="💰 My Wallet"), KeyboardButton(text="📊 Active Trades")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_cancel_menu():
    kb = [[KeyboardButton(text="❌ Cancel Operation")]]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# --- HELPER: SAFE EDIT (FIXED) ---
async def safe_edit(message: types.Message, text: str):
    """
    Prevents 'Message Not Modified' errors by checking content first.
    """
    if message.text == text:
        return # Skip if text is identical
    try:
        await message.edit_text(text)
    except Exception as e:
        logging.warning(f"UI Update Skipped: {e}")

# --- START FLOW ---
@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    await db.init_db()
    welcome = (
        "Sentinel AI\n"
        "──────────────────\n"
        "Your autonomous crypto trading agent.\n"
        "Tap below to begin."
    )
    await message.answer(welcome, reply_markup=get_launch_menu())

@dp.message(F.text == "🚀 Launch App")
async def launch_app(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Main Menu\n──────────────────", reply_markup=get_main_menu())

@dp.message(F.text == "❌ Cancel Operation")
async def cancel_op(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Operation cancelled.", reply_markup=get_main_menu())

# --- FEATURE: AI ANALYSIS ---
@dp.message(F.text == "🧠 AI Analysis")
async def analyze_start(message: types.Message, state: FSMContext):
    await message.answer(
        "New Analysis\n──────────────────\nPaste Token Address:", 
        reply_markup=get_cancel_menu()
    )
    await state.set_state(BotStates.waiting_for_token)

@dp.message(BotStates.waiting_for_token)
async def analyze_process(message: types.Message, state: FSMContext):
    ca = message.text.strip()
    if len(ca) < 30 or " " in ca:
        await message.answer("Invalid address.", reply_markup=get_cancel_menu())
        return

    status = await message.answer("🔎 Scanning...", reply_markup=get_cancel_menu())

    # 1. Safety
    await safe_edit(status, "🛡️ Checking Safety...")
    safety_verdict, safety_reason = await data_engine.get_rugcheck_report(ca)
    
    if safety_verdict == "UNSAFE":
        await safe_edit(status, f"⛔ BLOCKED: Unsafe\nReason: {safety_reason}")
        await state.clear()
        await message.answer("Menu:", reply_markup=get_main_menu())
        return

    # 2. Market Data
    await safe_edit(status, "📊 Fetching Data...")
    market_data = await data_engine.get_market_data(ca)
    if not market_data:
        await safe_edit(status, "❌ Market Data Unavailable")
        await state.clear()
        return

    # 3. AI Analysis
    await safe_edit(status, "🧠 AI Analyzing...")
    decision, reason = await sentinel_ai.analyze_token(ca, safety_verdict, market_data)

    # 4. Result
    emoji = "🟢" if decision == "BUY" else "🟡" if decision == "WAIT" else "🔴"
    result = (
        f"{emoji} Verdict: {decision}\n"
        f"──────────────────\n"
        f"Liquidity: ${market_data['liquidity']:,.0f}\n"
        f"AI: {reason}"
    )
    await safe_edit(status, result)
    await message.answer("Menu:", reply_markup=get_main_menu())
    await state.clear()

# --- WALLET ---
@dp.message(F.text == "💰 My Wallet")
async def check_wallet(message: types.Message):
    wallet = await db.get_wallet(message.from_user.id)
    if not wallet:
        await message.answer("No wallet found. Go to Settings.", reply_markup=get_main_menu())
        return
    
    msg = await message.answer("Checking...")
    try:
        bal = await jup.get_sol_balance(config.RPC_URL, wallet[2])
        await safe_edit(msg, f"Wallet\n──────────────────\nBalance: {bal/1e9:.4f} SOL")
    except:
        await safe_edit(msg, "Error checking balance.")

# --- MAIN ---
async def main():
    await start_web_server()
    await db.init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())