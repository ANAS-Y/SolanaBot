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
        [KeyboardButton(text="🛡️ Safety Check"), KeyboardButton(text="📊 Active Trades")],
        [KeyboardButton(text="❌ Cancel")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# --- ROBUST UI UPDATER ---
async def update_status(message: types.Message, old_msg: types.Message, text: str):
    """Deletes old status and sends new one to prevent Edit errors."""
    if old_msg:
        try: await old_msg.delete()
        except: pass
    return await message.answer(text)

# --- START ---
@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    await db.init_db()
    await message.answer(
        "👁️ **Sentinel AI Online**\n\n"
        "Ready to trade. Select an option below.",
        reply_markup=get_main_menu()
    )

# --- 1. ANALYZE FLOW ---
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

    status_msg = await message.answer("🔎 **Sentinel AI Started...**")

    # Safety
    status_msg = await update_status(message, status_msg, "🛡️ **Checking RugCheck...**")
    safety_verdict, safety_reason = await data_engine.get_rugcheck_report(ca)
    
    if safety_verdict == "UNSAFE":
        await update_status(message, status_msg, f"⛔ **BLOCKED**\n\nReason: {safety_reason}")
        await state.clear()
        return

    # Market
    status_msg = await update_status(message, status_msg, "📊 **Fetching DexScreener...**")
    market_data = await data_engine.get_market_data(ca)
    if not market_data:
        await update_status(message, status_msg, "❌ **Error:** Market data not found.")
        await state.clear()
        return

    # AI
    status_msg = await update_status(message, status_msg, "🧠 **Gemini AI Thinking...**")
    decision, reason = await sentinel_ai.analyze_token(ca, safety_verdict, market_data)

    # Report
    emoji = "🟢" if decision == "BUY" else "🟡" if decision == "WAIT" else "🔴"
    report = (
        f"{emoji} **Verdict: {decision}**\n"
        f"──────────────────\n"
        f"🛡️ Safety: {safety_reason}\n"
        f"💧 Liquidity: ${market_data['liquidity']:,.0f}\n"
        f"🧠 Logic: {reason}"
    )
    await status_msg.delete()
    await message.answer(report, reply_markup=get_main_menu())
    await state.clear()

# --- 2. WALLET BALANCE ---
@dp.message(F.text == "💰 Balance")
async def check_balance(message: types.Message):
    wallet = await db.get_wallet(message.from_user.id)
    if not wallet: return await message.answer("❌ No wallet found.")
    
    msg = await message.answer("⏳ Checking chain...")
    try:
        bal = await jup.get_sol_balance(config.RPC_URL, wallet[2])
        await msg.delete()
        await message.answer(f"💰 **Balance:** {bal/1e9:.4f} SOL")
    except:
        await msg.delete()
        await message.answer("❌ Network Error")

# --- 3. SAFETY CHECK ONLY (New Handler) ---
@dp.message(F.text == "🛡️ Safety Check")
async def safety_only_start(message: types.Message, state: FSMContext):
    # Reuse the same state, but we will add logic to skip AI
    await message.answer("🛡️ **Paste CA for Safety Scan:**", reply_markup=get_main_menu())
    await state.set_state("waiting_for_safety_token")

@dp.message(F.text, F.state == "waiting_for_safety_token") # Custom state string
async def safety_only_process(message: types.Message, state: FSMContext):
    ca = message.text.strip()
    if len(ca) < 30: return await message.answer("❌ Invalid CA")
    
    msg = await message.answer("🛡️ Scanning RugCheck...")
    verdict, reason = await data_engine.get_rugcheck_report(ca)
    
    emoji = "✅" if verdict == "SAFE" else "⛔"
    await msg.delete()
    await message.answer(f"{emoji} **Result:** {verdict}\n\n{reason}")
    await state.clear()

# --- 4. ACTIVE TRADES (Placeholder) ---
@dp.message(F.text == "📊 Active Trades")
async def active_trades(message: types.Message):
    # In the future, this will query your DB for open positions
    await message.answer("📊 **No Active Trades running.**")

# --- 5. CANCEL (New Handler) ---
@dp.message(F.text == "❌ Cancel")
async def cancel_op(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("✅ Operation Cancelled.", reply_markup=get_main_menu())

# --- MAIN ---
async def main():
    await start_web_server()
    await db.init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())