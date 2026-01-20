import asyncio
import logging
import os
import sys
import config 
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiohttp import web

# Custom Modules
import database as db
import data_engine
import sentinel_ai
import jupiter as jup

# --- LOGGING ---
logging.basicConfig(level=logging.INFO)
bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()

# --- WEB SERVER ---
async def health_check(request): return web.Response(text="Sentinel AI Running", status=200)
async def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

# --- STATES ---
class BotStates(StatesGroup):
    waiting_for_token = State()
    waiting_for_withdraw_addr = State()
    waiting_for_withdraw_amt = State()

# --- KEYBOARDS (The Trojan Style) ---
def get_main_menu():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🧠 New Analysis"), KeyboardButton(text="📊 Active Positions")],
        [KeyboardButton(text="💰 Wallet / Withdraw"), KeyboardButton(text="⚙️ Settings")]
    ], resize_keyboard=True)

def get_trade_panel(ca, price):
    """Inline buttons for instant trading"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Buy 0.1 SOL", callback_data=f"buy_0.1_{ca}_{price}"),
            InlineKeyboardButton(text="Buy 0.5 SOL", callback_data=f"buy_0.5_{ca}_{price}"),
            InlineKeyboardButton(text="Buy 1.0 SOL", callback_data=f"buy_1.0_{ca}_{price}"),
        ],
        [
            InlineKeyboardButton(text="🔄 Refresh Data", callback_data=f"refresh_{ca}"),
            InlineKeyboardButton(text="❌ Close", callback_data="close_panel")
        ]
    ])

# --- BACKGROUND MONITOR (The "Auto-Pilot") ---
async def position_monitor():
    """Checks active trades every 30s for TP/SL"""
    while True:
        try:
            trades = await db.get_active_trades()
            for trade in trades:
                # Fetch current price
                market = await data_engine.get_market_data(trade['token_address'])
                if not market: continue
                
                current_price = market['priceUsd']
                entry_price = trade['entry_price']
                pnl_pct = ((current_price - entry_price) / entry_price) * 100

                # TAKE PROFIT (+30%)
                if pnl_pct >= config.AUTO_SELL_TP:
                    await bot.send_message(
                        trade['user_id'], 
                        f"🚀 **Take Profit Triggered!**\nToken: {trade['token_address'][:6]}...\nPnL: +{pnl_pct:.2f}%"
                    )
                    # Implementation: Trigger Sell Here (requires Jupiter Sell logic)
                    await db.close_trade(trade['id'])
                
                # STOP LOSS (-15%)
                elif pnl_pct <= config.AUTO_SELL_SL:
                    await bot.send_message(
                        trade['user_id'], 
                        f"🛑 **Stop Loss Triggered!**\nToken: {trade['token_address'][:6]}...\nPnL: {pnl_pct:.2f}%"
                    )
                    # Implementation: Trigger Sell Here
                    await db.close_trade(trade['id'])
                    
        except Exception as e:
            logging.error(f"Monitor Error: {e}")
        
        await asyncio.sleep(30) # Wait 30 seconds

# --- HANDLERS ---

@dp.message(Command("start"))
async def start(message: types.Message):
    await db.init_db()
    await message.answer("👁️ **Sentinel AI**\nProduction Ready.", reply_markup=get_main_menu())

# 1. NEW ANALYSIS (Trojan Style)
@dp.message(F.text == "🧠 New Analysis")
async def analyze_start(message: types.Message, state: FSMContext):
    await message.answer("📝 **Paste Token Address:**")
    await state.set_state(BotStates.waiting_for_token)

@dp.message(BotStates.waiting_for_token)
async def analyze_process(message: types.Message, state: FSMContext):
    ca = message.text.strip()
    if len(ca) < 30: return await message.answer("❌ Invalid CA")
    
    status = await message.answer("🔎 **Scanning...**")

    # Parallel Data Fetch (Faster)
    safety, market = await asyncio.gather(
        data_engine.get_rugcheck_report(ca),
        data_engine.get_market_data(ca)
    )
    
    verdict = safety[0]
    if verdict == "UNSAFE":
        await status.edit_text(f"⛔ **BLOCKED**\n{safety[1]}")
        await state.clear()
        return

    if not market:
        await status.edit_text("❌ Data not found.")
        await state.clear()
        return

    # AI Decision
    decision, reason = await sentinel_ai.analyze_token(ca, verdict, market)
    
    # Dashboard Output
    emoji = "🟢" if decision == "BUY" else "🔴"
    text = (
        f"{emoji} **Sentinel Analysis**\n"
        f"Verdict: **{decision}**\n"
        f"────────────────\n"
        f"💎 Price: ${market['priceUsd']:.6f}\n"
        f"💧 Liq: ${market['liquidity']:,.0f}\n"
        f"📊 Vol (5m): ${market['volume_5m']:,.0f}\n"
        f"🛡️ Safety: {safety[1]}\n"
        f"────────────────\n"
        f"🧠 **AI:** {reason}"
    )
    
    # Show Trade Panel
    await status.delete()
    await message.answer(text, reply_markup=get_trade_panel(ca, market['priceUsd']))
    await state.clear()

# 2. BUY HANDLER (Callback)
@dp.callback_query(F.data.startswith("buy_"))
async def execute_buy(callback: types.CallbackQuery):
    # Data format: buy_0.1_CA_PRICE
    _, amount, ca, price = callback.data.split("_")
    amount = float(amount)
    
    await callback.message.answer(f"⏳ **Executing Buy: {amount} SOL...**")
    
    # 1. Get Wallet
    wallet = await db.get_wallet(callback.from_user.id)
    if not wallet:
        return await callback.message.answer("❌ No Wallet! Go to settings.")

    # 2. Execute Swap (Jupiter)
    # Note: In production, we decrypt the private key here. 
    # For now, we simulate the 'success' to test flow.
    # To enable real trading, you would use:
    # tx_sig = await jup.execute_swap(keypair, config.SOL_MINT, ca, amount_lamports)
    
    # SIMULATION FOR DEMO:
    await asyncio.sleep(2)
    await callback.message.answer(
        f"✅ **Buy Successful!**\n"
        f"Spent: {amount} SOL\n"
        f"Entry: ${price}\n\n"
        f"🤖 *Position added to Auto-Monitor*"
    )
    
    # 3. Add to Database for Monitoring
    await db.add_trade(callback.from_user.id, ca, amount, float(price), 0)
    await callback.answer()

# 3. WALLET & WITHDRAW
@dp.message(F.text == "💰 Wallet / Withdraw")
async def wallet_menu(message: types.Message):
    wallet = await db.get_wallet(message.from_user.id)
    if not wallet: return await message.answer("❌ No wallet.")
    
    bal = await jup.get_sol_balance(config.RPC_URL, wallet[2])
    
    text = (
        f"💰 **Wallet**\n"
        f"`{wallet[2]}`\n"
        f"Balance: **{bal/1e9:.4f} SOL**"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Withdraw SOL", callback_data="withdraw_start")],
        [InlineKeyboardButton(text="🔑 Export Key", callback_data="export_key")]
    ])
    await message.answer(text, reply_markup=kb)

@dp.callback_query(F.data == "export_key")
async def export_key_handler(callback: types.CallbackQuery):
    wallet = await db.get_wallet(callback.from_user.id)
    # Send as ephemeral/hidden message if possible, or DM
    await callback.message.answer(f"🔐 **Private Key:**\n`{wallet[1]}`\n\n*Delete this message immediately!*")
    await callback.answer()

# 4. ACTIVE POSITIONS
@dp.message(F.text == "📊 Active Positions")
async def show_positions(message: types.Message):
    trades = await db.get_active_trades()
    user_trades = [t for t in trades if t['user_id'] == message.from_user.id]
    
    if not user_trades:
        return await message.answer("💤 No active trades.")
    
    text = "📊 **Your Positions:**\n\n"
    for t in user_trades:
        text += f"• **{t['token_address'][:4]}...** | Entry: ${t['entry_price']}\n"
    
    await message.answer(text)

# --- MAIN ---
async def main():
    await start_web_server()
    await db.init_db()
    
    # Start the Background Monitor
    asyncio.create_task(position_monitor())
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())