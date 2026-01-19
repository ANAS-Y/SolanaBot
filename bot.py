import asyncio
import logging
import config
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

# Import our custom modules
import database as db
import data_engine
import sentinel_ai
import jupiter as jup # Re-using your existing wallet logic

logging.basicConfig(level=logging.INFO)
bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()

# --- MENUS ---
def get_main_menu():
    kb = [
        [KeyboardButton(text="🧠 Analyze Token (AI)"), KeyboardButton(text="💰 Wallet Balance")],
        [KeyboardButton(text="⚙️ Settings"), KeyboardButton(text="📊 Active Positions")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# --- START ---
@dp.message(Command("start"))
async def start(message: types.Message):
    await db.init_db()
    await message.answer(
        "👁️ **Sentinel AI Connected**\n\n"
        "I am an autonomous agent powered by Google Gemini.\n"
        "I filter scams via RugCheck and trade only high-quality setups.",
        reply_markup=get_main_menu()
    )

# --- AI ANALYSIS AGENT ---
@dp.message(F.text == "🧠 Analyze Token (AI)")
async def analyze_ask(message: types.Message):
    await message.answer("📝 **Paste the Token Contract Address (CA):**")

@dp.message(lambda x: len(x.text) > 30 and " " not in x.text) # Simple CA detection
async def run_sentinel_agent(message: types.Message):
    ca = message.text.strip()
    status_msg = await message.answer(f"🔎 **Sentinel AI is analyzing...**\n`{ca}`")

    # 1. SAFETY CHECK (The Eyes)
    await status_msg.edit_text("🛡️ Checking Safety (RugCheck.xyz)...")
    safety_verdict, safety_reason = await data_engine.get_rugcheck_report(ca)
    
    if safety_verdict == "UNSAFE":
        await status_msg.edit_text(
            f"⛔ **BLOCKED BY SENTINEL**\n\n"
            f"**Reason:** {safety_reason}\n"
            f"**Action:** Filtered out before market analysis."
        )
        return

    # 2. MARKET DATA (The Data)
    await status_msg.edit_text("📊 Fetching Market Data (DexScreener)...")
    market_data = await data_engine.get_market_data(ca)
    if not market_data:
        await status_msg.edit_text("❌ Error fetching market data.")
        return

    # 3. AI DECISION (The Brain)
    await status_msg.edit_text("🧠 Gemini AI is thinking...")
    decision, reason = await sentinel_ai.analyze_token(ca, safety_verdict, market_data)

    # 4. REPORT
    emoji = "🟢" if decision == "BUY" else "🟡" if decision == "WAIT" else "🔴"
    
    await status_msg.edit_text(
        f"{emoji} **Sentinel AI Decision: {decision}**\n\n"
        f"**Safety:** {safety_reason}\n"
        f"**Liquidity:** ${market_data['liquidity']:,.0f}\n"
        f"**Vol (5m):** ${market_data['volume_5m']:,.0f}\n\n"
        f"**AI Reasoning:**\n_{reason}_"
    )

    # 5. EXECUTION (The Hands)
    if decision == "BUY":
        if config.SIMULATION_MODE:
            await message.answer(
                f"🧪 **SIMULATION MODE**\n"
                f"Simulating Buy of 0.1 SOL on {ca}...\n"
                f"Entry Price: ${market_data['priceUsd']}\n"
                f"Tracking for TP (+30%) or SL (-15%)..."
            )
            # Add to DB "active_trades" with a flag is_sim=True
        else:
            # Here we would trigger the Real Swap using jupiter.execute_swap
            # This requires the user's PIN to decrypt the key, so we would
            # likely ask for PIN confirmation here.
            await message.answer("💰 **Real Trade Signal!** Enter PIN to execute.")

# --- WALLET & SETTINGS (Harmonized from previous work) ---
@dp.message(F.text == "💰 Wallet Balance")
async def check_balance(message: types.Message):
    wallet = await db.get_wallet(message.from_user.id)
    if not wallet: return await message.answer("❌ No wallet found. Go to Settings.")
    
    pub_key = wallet[2]
    sol_bal = await jup.get_sol_balance(config.RPC_URL, pub_key)
    
    await message.answer(f"💰 **Balance:** {sol_bal/1e9:.4f} SOL\n`{pub_key}`")

# --- MAIN ---
async def main():
    await db.init_db()
    # Start background tasks here if needed
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())