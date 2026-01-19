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

# --- WEB SERVER (Render Requirement) ---
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

# --- MENUS (Beautiful UI) ---

def get_launch_menu():
    """The initial Start Button"""
    kb = [[KeyboardButton(text="🚀 Launch App")]]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_main_menu():
    """The Main Dashboard - Clean Labels"""
    kb = [
        [KeyboardButton(text="🧠 AI Analysis"), KeyboardButton(text="🛡️ Safety Check")],
        [KeyboardButton(text="💰 My Wallet"), KeyboardButton(text="📊 Active Trades")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_cancel_menu():
    """Universal Cancel Button"""
    kb = [[KeyboardButton(text="❌ Cancel Operation")]]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# --- HELPER: SAFE EDIT ---
async def safe_edit(message: types.Message, text: str):
    """
    Safely edits a message. If the text is the same (Telegram Error),
    it ignores the error and continues.
    """
    try:
        await message.edit_text(text)
    except Exception as e:
        # Ignore "message is not modified" errors
        if "message is not modified" not in str(e):
            logging.warning(f"Edit Error: {e}")

# --- START FLOW ---

@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    await db.init_db()
    
    welcome_text = (
        "Sentinel AI\n"
        "──────────────────\n"
        "Your autonomous crypto trading agent.\n"
        "Powered by Google Gemini & RugCheck.\n\n"
        "Tap the button below to begin."
    )
    await message.answer(welcome_text, reply_markup=get_launch_menu())

@dp.message(F.text == "🚀 Launch App")
async def launch_app(message: types.Message, state: FSMContext):
    await state.clear()
    text = (
        "Main Menu\n"
        "──────────────────\n"
        "Select a tool to proceed:"
    )
    await message.answer(text, reply_markup=get_main_menu())

# --- CANCEL HANDLER (Universal) ---
@dp.message(F.text == "❌ Cancel Operation")
async def cancel_op(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Operation cancelled.", reply_markup=get_main_menu())

# --- FEATURE: AI ANALYSIS ---

@dp.message(F.text == "🧠 AI Analysis")
async def analyze_start(message: types.Message, state: FSMContext):
    text = (
        "New Analysis\n"
        "──────────────────\n"
        "Paste the Token Contract Address below.\n\n"
        "Hint: It is a long string of random letters."
    )
    await message.answer(text, reply_markup=get_cancel_menu())
    await state.set_state(BotStates.waiting_for_token)

@dp.message(BotStates.waiting_for_token)
async def analyze_process(message: types.Message, state: FSMContext):
    ca = message.text.strip()
    
    # Basic Validation
    if len(ca) < 30 or " " in ca:
        await message.answer("Invalid address format. Please try again.", reply_markup=get_cancel_menu())
        return

    # Status Message
    status = await message.answer("🔎 Scanning network...", reply_markup=get_cancel_menu())

    # 1. Safety Check
    await safe_edit(status, "🛡️ Checking RugCheck safety database...")
    safety_verdict, safety_reason = await data_engine.get_rugcheck_report(ca)
    
    if safety_verdict == "UNSAFE":
        await safe_edit(status,
            f"⛔ BLOCKED: Unsafe Token\n"
            f"──────────────────\n"
            f"Reason: {safety_reason}\n\n"
            f"Analysis stopped to protect funds."
        )
        await state.clear()
        await message.answer("Select an option:", reply_markup=get_main_menu())
        return

    # 2. Market Data
    await safe_edit(status, "📊 Fetching live market data...")
    market_data = await data_engine.get_market_data(ca)
    if not market_data:
        await safe_edit(status, "❌ Error: Could not fetch market data. The token might be too new.")
        await state.clear()
        await message.answer("Select an option:", reply_markup=get_main_menu())
        return

    # 3. AI Analysis
    await safe_edit(status, "🧠 Gemini AI is analyzing price action...")
    decision, reason = await sentinel_ai.analyze_token(ca, safety_verdict, market_data)

    # 4. Final Output (Clean Formatting)
    emoji = "🟢" if decision == "BUY" else "🟡" if decision == "WAIT" else "🔴"
    
    result_text = (
        f"{emoji} Verdict: {decision}\n"
        f"──────────────────\n"
        f"Safety: {safety_reason}\n"
        f"Liquidity: ${market_data['liquidity']:,.0f}\n"
        f"Volume (5m): ${market_data['volume_5m']:,.0f}\n\n"
        f"AI Reasoning:\n"
        f"{reason}"
    )
    
    await safe_edit(status, result_text)
    await message.answer("Select an option:", reply_markup=get_main_menu())
    await state.clear()

# --- FEATURE: WALLET ---

@dp.message(F.text == "💰 My Wallet")
async def check_wallet(message: types.Message):
    wallet = await db.get_wallet(message.from_user.id)
    if not wallet:
        await message.answer(
            "No wallet connected.\n"
            "Go to ⚙️ Settings to create or import one.",
            reply_markup=get_main_menu()
        )
        return
    
    pub_key = wallet[2]
    msg = await message.answer("Checking balance...")
    
    try:
        sol_bal = await jup.get_sol_balance(config.RPC_URL, pub_key)
        sol_fmt = sol_bal / 1_000_000_000
        
        text = (
            "Wallet Status\n"
            "──────────────────\n"
            f"Address: {pub_key[:4]}...{pub_key[-4:]}\n"
            f"Balance: {sol_fmt:.4f} SOL"
        )
        await safe_edit(msg, text)
    except Exception as e:
        await safe_edit(msg, f"Error checking balance: {e}")

# --- CATCH ALL ---
@dp.message()
async def unknown_command(message: types.Message):
    if message.chat.type == "private":
        await message.answer(
            "I didn't understand that command.\n"
            "Please use the menu buttons below.",
            reply_markup=get_main_menu()
        )

# --- MAIN ---
async def main():
    await start_web_server()
    await db.init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())