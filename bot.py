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
    port = int(os.environ.get("PORT", 10000))
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"🌍 Web Server started on port {port}")

# --- MENUS ---
def get_main_menu():
    kb = [
        [KeyboardButton(text="🧠 Analyze Token (AI)"), KeyboardButton(text="💰 Wallet Balance")],
        [KeyboardButton(text="⚙️ Settings"), KeyboardButton(text="📊 Active Positions")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# --- COMMAND HANDLERS ---
@dp.message(Command("start"))
async def start(message: types.Message):
    await db.init_db()
    await message.answer(
        "👁️ **Sentinel AI Online**\n\n"
        "I am your autonomous crypto agent.\n"
        "I analyze Solana tokens using Google Gemini AI and check for scams via RugCheck.",
        reply_markup=get_main_menu()
    )

@dp.message(Command("help"))
async def help_command(message: types.Message):
    await message.answer(
        "**Sentinel AI Help**\n\n"
        "1. Click **🧠 Analyze Token**\n"
        "2. Paste a Solana Contract Address (CA)\n"
        "3. Wait for the AI Verdict (Buy/Wait/Avoid)\n\n"
        "Commands:\n"
        "/start - Restart Bot\n"
        "/status - Check active trades",
        reply_markup=get_main_menu()
    )

# --- AI ANALYSIS FLOW ---
@dp.message(F.text == "🧠 Analyze Token (AI)")
async def analyze_ask(message: types.Message):
    await message.answer("📝 **Paste the Token Contract Address (CA) now:**")

# This filter detects Solana Addresses (Long strings with no spaces)
@dp.message(lambda x: len(x.text) > 30 and " " not in x.text)
async def run_sentinel_agent(message: types.Message):
    ca = message.text.strip()
    status_msg = await message.answer(f"🔎 **Sentinel AI is analyzing...**\n`{ca}`")

    # 1. Safety Check
    try:
        await status_msg.edit_text("🛡️ Checking Safety (RugCheck.xyz)...")
        safety_verdict, safety_reason = await data_engine.get_rugcheck_report(ca)
        
        if safety_verdict == "UNSAFE":
            await status_msg.edit_text(f"⛔ **BLOCKED**\nReason: {safety_reason}")
            return
    except Exception as e:
        await status_msg.edit_text(f"⚠️ Error during Safety Check: {e}")
        return

    # 2. Market Data
    try:
        await status_msg.edit_text("📊 Fetching Market Data...")
        market_data = await data_engine.get_market_data(ca)
        if not market_data:
            await status_msg.edit_text("❌ Error fetching market data. Token might be too new.")
            return
    except Exception as e:
        await status_msg.edit_text(f"⚠️ Error during Market Data fetch: {e}")
        return

    # 3. AI Analysis
    try:
        await status_msg.edit_text("🧠 Gemini AI is thinking...")
        decision, reason = await sentinel_ai.analyze_token(ca, safety_verdict, market_data)

        emoji = "🟢" if decision == "BUY" else "🟡" if decision == "WAIT" else "🔴"
        await status_msg.edit_text(
            f"{emoji} **Decision: {decision}**\n\n"
            f"**Safety:** {safety_reason}\n"
            f"**Liquidity:** ${market_data['liquidity']:,.0f}\n"
            f"**AI Reasoning:**\n_{reason}_"
        )
    except Exception as e:
        await status_msg.edit_text(f"⚠️ AI Error: {e}")

# --- WALLET HANDLERS ---
@dp.message(F.text == "💰 Wallet Balance")
async def check_balance(message: types.Message):
    wallet = await db.get_wallet(message.from_user.id)
    if not wallet: 
        return await message.answer("❌ No wallet found. Please create one.")
    
    pub_key = wallet[2]
    try:
        sol_bal = await jup.get_sol_balance(config.RPC_URL, pub_key)
        await message.answer(f"💰 **Balance:** {sol_bal/1e9:.4f} SOL")
    except Exception as e:
        await message.answer(f"⚠️ Balance Error: {e}")

# --- CATCH-ALL HANDLER (The Fix for "Not Responding") ---
@dp.message()
async def catch_all(message: types.Message):
    """
    This catches ANY message that didn't match the rules above.
    """
    await message.answer(
        "❓ I didn't understand that.\n"
        "Please select an option from the menu or paste a valid Contract Address.",
        reply_markup=get_main_menu()
    )

# --- MAIN ENTRY ---
async def main():
    await start_web_server()
    await db.init_db()
    # Force drop old updates to stop the bot from processing stale messages
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())