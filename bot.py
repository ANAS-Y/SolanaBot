import asyncio
import logging
import os
import sys
import config 
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
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
    logging.critical("❌ BOT_TOKEN is missing! Check Render Environment Variables.")
    sys.exit(1)

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()

# --- WEB SERVER (REQUIRED FOR RENDER) ---
async def health_check(request):
    return web.Response(text="Sentinel AI is running", status=200)

async def start_web_server():
    """Starts a dummy web server to satisfy Render's port binding requirement."""
    port = int(os.environ.get("PORT", 10000))  # Default to 10000
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"🌍 Web Server started on port {port}")

# --- BOT LOGIC (Same as before) ---
def get_main_menu():
    kb = [
        [KeyboardButton(text="🧠 Analyze Token (AI)"), KeyboardButton(text="💰 Wallet Balance")],
        [KeyboardButton(text="⚙️ Settings"), KeyboardButton(text="📊 Active Positions")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

@dp.message(Command("start"))
async def start(message: types.Message):
    await db.init_db()
    await message.answer(
        "👁️ **Sentinel AI Connected**\n\n"
        "I am an autonomous agent powered by Google Gemini.\n"
        "I filter scams via RugCheck and trade only high-quality setups.",
        reply_markup=get_main_menu()
    )

@dp.message(F.text == "🧠 Analyze Token (AI)")
async def analyze_ask(message: types.Message):
    await message.answer("📝 **Paste the Token Contract Address (CA):**")

@dp.message(lambda x: len(x.text) > 30 and " " not in x.text)
async def run_sentinel_agent(message: types.Message):
    ca = message.text.strip()
    status_msg = await message.answer(f"🔎 **Sentinel AI is analyzing...**\n`{ca}`")

    # 1. Safety
    await status_msg.edit_text("🛡️ Checking Safety (RugCheck.xyz)...")
    safety_verdict, safety_reason = await data_engine.get_rugcheck_report(ca)
    if safety_verdict == "UNSAFE":
        await status_msg.edit_text(f"⛔ **BLOCKED**\nReason: {safety_reason}")
        return

    # 2. Market Data
    await status_msg.edit_text("📊 Fetching Market Data...")
    market_data = await data_engine.get_market_data(ca)
    if not market_data:
        await status_msg.edit_text("❌ Error fetching market data.")
        return

    # 3. AI Analysis
    await status_msg.edit_text("🧠 Gemini AI is thinking...")
    decision, reason = await sentinel_ai.analyze_token(ca, safety_verdict, market_data)

    emoji = "🟢" if decision == "BUY" else "🟡" if decision == "WAIT" else "🔴"
    await status_msg.edit_text(
        f"{emoji} **Decision: {decision}**\n\n"
        f"**Safety:** {safety_reason}\n"
        f"**Liquidity:** ${market_data['liquidity']:,.0f}\n"
        f"**AI Reasoning:**\n_{reason}_"
    )

@dp.message(F.text == "💰 Wallet Balance")
async def check_balance(message: types.Message):
    wallet = await db.get_wallet(message.from_user.id)
    if not wallet: return await message.answer("❌ No wallet found.")
    pub_key = wallet[2]
    sol_bal = await jup.get_sol_balance(config.RPC_URL, pub_key)
    await message.answer(f"💰 **Balance:** {sol_bal/1e9:.4f} SOL")

# --- MAIN ENTRY POINT ---
async def main():
    # 1. Start Web Server FIRST (Crucial for Render)
    await start_web_server()
    
    # 2. Initialize DB
    await db.init_db()
    
    # 3. Start Bot
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())