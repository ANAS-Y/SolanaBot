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

import database as db
import data_engine
import sentinel_ai
import jupiter as jup

# --- LOGGING ---
logging.basicConfig(level=logging.INFO)

if not config.BOT_TOKEN:
    sys.exit("CRITICAL: BOT_TOKEN is missing.")

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()

# --- WEB SERVER ---
async def health_check(request):
    return web.Response(text="Sentinel AI Running", status=200)

async def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"Web Server started on {port}")

# --- STATES ---
class BotStates(StatesGroup):
    waiting_for_token = State()

# --- MENUS ---
def get_main_menu():
    kb = [
        [KeyboardButton(text="🧠 Analyze Token"), KeyboardButton(text="💰 Balance")],
        [KeyboardButton(text="🛡️ Safety Check"), KeyboardButton(text="❌ Cancel")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# --- ROBUST UI UPDATER ---
async def update_status(message: types.Message, old_msg: types.Message, text: str):
    """
    Deletes the old status message and sends a new one.
    This fixes the 'Message can't be edited' error permanently.
    """
    if old_msg:
        try:
            await old_msg.delete()
        except:
            pass # If already deleted, ignore
    
    # Send new message and return it so we can delete it next time
    return await message.answer(text)

# --- START ---
@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    await db.init_db()
    await message.answer(
        "👁️ **Sentinel AI Online**\n\n"
        "Tap **🧠 Analyze Token** to begin.",
        reply_markup=get_main_menu()
    )

# --- ANALYZE FLOW ---
@dp.message(F.text == "🧠 Analyze Token")
async def analyze_start(message: types.Message, state: FSMContext):
    await message.answer("📝 **Paste Contract Address (CA):**", reply_markup=get_main_menu())
    await state.set_state(BotStates.waiting_for_token)

@dp.message(BotStates.waiting_for_token)
async def analyze_process(message: types.Message, state: FSMContext):
    ca = message.text.strip()
    if len(ca) < 30 or " " in ca:
        await message.answer("❌ Invalid Address. Try again.")
        return

    # 1. Start Status
    status_msg = await message.answer("🔎 **Sentinel AI Started...**")

    # 2. Safety Check
    status_msg = await update_status(message, status_msg, "🛡️ **Checking RugCheck Database...**")
    safety_verdict, safety_reason = await data_engine.get_rugcheck_report(ca)
    
    if safety_verdict == "UNSAFE":
        await update_status(message, status_msg, f"⛔ **BLOCKED**\n\nReason: {safety_reason}")
        await state.clear()
        return

    # 3. Market Data
    status_msg = await update_status(message, status_msg, "📊 **Fetching DexScreener Data...**")
    market_data = await data_engine.get_market_data(ca)
    if not market_data:
        await update_status(message, status_msg, "❌ **Error:** Market data not found.")
        await state.clear()
        return

    # 4. AI Analysis
    status_msg = await update_status(message, status_msg, "🧠 **Gemini AI is Thinking...**")
    decision, reason = await sentinel_ai.analyze_token(ca, safety_verdict, market_data)

    # 5. Final Report
    emoji = "🟢" if decision == "BUY" else "🟡" if decision == "WAIT" else "🔴"
    report = (
        f"{emoji} **Verdict: {decision}**\n"
        f"──────────────────\n"
        f"🛡️ Safety: {safety_reason}\n"
        f"💧 Liquidity: ${market_data['liquidity']:,.0f}\n"
        f"🧠 AI Logic: {reason}"
    )
    
    # Send final result cleanly
    await status_msg.delete()
    await message.answer(report, reply_markup=get_main_menu())
    await state.clear()

# --- WALLET ---
@dp.message(F.text == "💰 Balance")
async def check_balance(message: types.Message):
    wallet = await db.get_wallet(message.from_user.id)
    if not wallet: return await message.answer("❌ No wallet.")
    
    msg = await message.answer("⏳ Checking chain...")
    try:
        bal = await jup.get_sol_balance(config.RPC_URL, wallet[2])
        await msg.delete()
        await message.answer(f"💰 **Balance:** {bal/1e9:.4f} SOL")
    except:
        await msg.edit_text("❌ Network Error")

# --- MAIN ---
async def main():
    await start_web_server()
    await db.init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())