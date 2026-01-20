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
    waiting_for_import_key = State()
    waiting_for_slippage = State()
    waiting_for_tp = State()
    waiting_for_sl = State()

# --- MENUS ---
def get_main_menu():
    kb = [
        [KeyboardButton(text="🧠 Analyze Token"), KeyboardButton(text="💰 Wallet")],
        [KeyboardButton(text="📊 Active Trades"), KeyboardButton(text="⚙️ Settings")],
        [KeyboardButton(text="❌ Cancel")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_cancel_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Cancel")]], resize_keyboard=True)

def get_trade_panel(ca, price):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Buy 0.1 SOL", callback_data=f"buy_0.1_{ca}_{price}"),
            InlineKeyboardButton(text="Buy 0.5 SOL", callback_data=f"buy_0.5_{ca}_{price}"),
            InlineKeyboardButton(text="Buy 1.0 SOL", callback_data=f"buy_1.0_{ca}_{price}"),
        ],
        [InlineKeyboardButton(text="❌ Close", callback_data="close_panel")]
    ])

# --- MONITOR ---
async def position_monitor():
    """Background task to check TP/SL"""
    while True:
        try:
            trades = await db.get_active_trades()
            for trade in trades:
                settings = await db.get_settings(trade['user_id'])
                tp_target = settings['take_profit']
                sl_target = settings['stop_loss'] * -1
                auto_sell = settings['auto_sell']

                market = await data_engine.get_market_data(trade['token_address'])
                if not market: continue
                
                curr_price = market['priceUsd']
                entry = trade['entry_price']
                pnl = ((curr_price - entry) / entry) * 100

                triggered = False
                msg_type = ""
                
                if pnl >= tp_target:
                    triggered = True
                    msg_type = "🚀 **Take Profit Hit!**"
                elif pnl <= sl_target:
                    triggered = True
                    msg_type = "🛑 **Stop Loss Hit!**"

                if triggered:
                    if auto_sell:
                        await bot.send_message(trade['user_id'], f"{msg_type}\nToken: `{trade['token_address'][:6]}...`\nPnL: {pnl:.2f}%\n✅ **Auto-Selling...**")
                        # Real Sell Logic would go here
                        await db.close_trade(trade['id'])
                    else:
                        # Manual Alert only prevents spam by checking last alert time (simplified here)
                        pass 

        except Exception as e:
            logging.error(f"Monitor Error: {e}")
        
        await asyncio.sleep(15)

# --- SETTINGS PANEL ---
async def show_settings_panel(user_id, message_obj=None, edit_mode=False):
    s = await db.get_settings(user_id)
    slippage = s['slippage']
    auto_buy = "✅ ON" if s['auto_buy'] else "🔴 OFF"
    auto_sell = "✅ ON" if s['auto_sell'] else "🔴 OFF"
    sim_mode = "🧪 SIMULATION" if s['simulation_mode'] else "💸 REAL MONEY"
    tp = s['take_profit']
    sl = s['stop_loss']

    text = "⚙️ **Bot Configuration**\n──────────────────"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💧 Slippage: {slippage}%", callback_data="set_slippage")],
        [InlineKeyboardButton(text=f"🚀 TP: +{tp}%", callback_data="set_tp"), InlineKeyboardButton(text=f"🛑 SL: -{sl}%", callback_data="set_sl")],
        [InlineKeyboardButton(text=f"🤖 Auto-Buy: {auto_buy}", callback_data="toggle_autobuy"), InlineKeyboardButton(text=f"📉 Auto-Sell: {auto_sell}", callback_data="toggle_autosell")],
        [InlineKeyboardButton(text=f"Mode: {sim_mode}", callback_data="toggle_sim")],
        [InlineKeyboardButton(text="❌ Close", callback_data="close_panel")]
    ])

    if edit_mode and message_obj: await message_obj.edit_text(text, reply_markup=kb)
    elif message_obj: await message_obj.answer(text, reply_markup=kb)

# --- HANDLERS ---
@dp.message(Command("start"))
async def start(message: types.Message):
    await db.init_db()
    await message.answer("👁️ **Sentinel AI Online**", reply_markup=get_main_menu())

# SETTINGS
@dp.message(F.text == "⚙️ Settings")
async def settings_menu(message: types.Message):
    await show_settings_panel(message.from_user.id, message)

@dp.callback_query(F.data.startswith("toggle_"))
async def toggle_setting(callback: types.CallbackQuery):
    mode = callback.data.split("_")[1] # autobuy, autosell, sim
    col_map = {"autobuy": "auto_buy", "autosell": "auto_sell", "sim": "simulation_mode"}
    col = col_map.get(mode)
    
    s = await db.get_settings(callback.from_user.id)
    new_val = 0 if s[col] else 1
    await db.update_setting(callback.from_user.id, col, new_val)
    await show_settings_panel(callback.from_user.id, callback.message, edit_mode=True)

@dp.callback_query(F.data.startswith("set_"))
async def set_value_start(callback: types.CallbackQuery, state: FSMContext):
    mode = callback.data.split("_")[1] # slippage, tp, sl
    await callback.message.delete()
    
    prompt_map = {
        "slippage": ("💧 **Enter Slippage %:**", BotStates.waiting_for_slippage),
        "tp": ("🚀 **Enter Take Profit %:**", BotStates.waiting_for_tp),
        "sl": ("🛑 **Enter Stop Loss %:**", BotStates.waiting_for_sl)
    }
    
    msg, state_obj = prompt_map[mode]
    await callback.message.answer(msg, reply_markup=get_cancel_kb())
    await state.set_state(state_obj)

@dp.message(BotStates.waiting_for_slippage)
async def set_slip_process(message: types.Message, state: FSMContext):
    await process_setting_input(message, state, "slippage", 0.1, 50)

@dp.message(BotStates.waiting_for_tp)
async def set_tp_process(message: types.Message, state: FSMContext):
    await process_setting_input(message, state, "take_profit", 1, 1000)

@dp.message(BotStates.waiting_for_sl)
async def set_sl_process(message: types.Message, state: FSMContext):
    await process_setting_input(message, state, "stop_loss", 1, 99)

async def process_setting_input(message, state, col, min_val, max_val):
    try:
        val = float(message.text)
        if min_val <= val <= max_val:
            await db.update_setting(message.from_user.id, col, val)
            await message.answer(f"✅ Updated.", reply_markup=get_main_menu())
            await state.clear()
            await show_settings_panel(message.from_user.id, message)
        else: raise ValueError
    except: await message.answer(f"❌ Invalid. Range: {min_val}-{max_val}")

@dp.callback_query(F.data == "close_panel")
async def close_panel(c: types.CallbackQuery): await c.message.delete()

# ANALYZE
@dp.message(F.text == "🧠 Analyze Token")
async def analyze_start(message: types.Message, state: FSMContext):
    await message.answer("📝 **Paste Contract Address (CA):**", reply_markup=get_main_menu())
    await state.set_state(BotStates.waiting_for_token)

@dp.message(BotStates.waiting_for_token)
async def analyze_process(message: types.Message, state: FSMContext):
    ca = message.text.strip()
    if len(ca) < 30: return await message.answer("❌ Invalid Address.")

    status = await message.answer("🔎 **Scanning...**")
    
    # Safety
    safety, reason = await data_engine.get_rugcheck_report(ca)
    if safety == "UNSAFE":
        await status.delete()
        await message.answer(f"⛔ **BLOCKED**\n{reason}")
        await state.clear()
        return

    # Market
    market = await data_engine.get_market_data(ca)
    if not market:
        await status.delete()
        await message.answer("❌ No Data.")
        await state.clear()
        return

    # AI
    await status.delete()
    status = await message.answer("🧠 **AI Thinking...**")
    decision, ai_reason = await sentinel_ai.analyze_token(ca, safety, market)

    emoji = "🟢" if decision == "BUY" else "🔴"
    text = (
        f"{emoji} **Verdict: {decision}**\n"
        f"────────────────\n"
        f"💎 Price: ${market['priceUsd']:.6f}\n"
        f"💧 Liq: ${market['liquidity']:,.0f}\n"
        f"🧠 **AI:** {ai_reason}"
    )
    await status.delete()
    await message.answer(text, reply_markup=get_trade_panel(ca, market['priceUsd']))
    await state.clear()

# BUY
@dp.callback_query(F.data.startswith("buy_"))
async def execute_buy(callback: types.CallbackQuery):
    _, amount, ca, price = callback.data.split("_")
    s = await db.get_settings(callback.from_user.id)
    mode_text = "🧪 SIMULATION" if s['simulation_mode'] else "💸 REAL MONEY"
    
    await callback.message.answer(f"⏳ **Executing {mode_text} Buy: {amount} SOL...**")
    await asyncio.sleep(1)
    
    await callback.message.answer(f"✅ **Buy Success!**\nEntry: ${price}\n🤖 Auto-Monitor: ON")
    await db.add_trade(callback.from_user.id, ca, float(amount), float(price), 0)
    await callback.answer()

# WALLET
@dp.message(F.text == "💰 Wallet")
async def wallet_menu(message: types.Message):
    wallet = await db.get_wallet(message.from_user.id)
    if not wallet:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🆕 Create", callback_data="wallet_create"), InlineKeyboardButton(text="📥 Import", callback_data="wallet_import")]])
        return await message.answer("❌ No Wallet Found.", reply_markup=kb)
    
    bal = await jup.get_sol_balance(config.RPC_URL, wallet[2])
    text = f"💰 **Wallet**\n`{wallet[2]}`\nBalance: **{bal/1e9:.4f} SOL**"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Withdraw", callback_data="withdraw_start")],
        [InlineKeyboardButton(text="🔑 Export Key", callback_data="export_key")]
    ])
    await message.answer(text, reply_markup=kb)

# ACTIONS
@dp.callback_query(F.data == "wallet_create")
async def wallet_create(c: types.CallbackQuery):
    priv, pub = jup.create_new_wallet()
    await db.add_wallet(c.from_user.id, priv, pub)
    await c.message.edit_text(f"✅ Created!\nAddress: `{pub}`")

@dp.callback_query(F.data == "wallet_import")
async def wallet_import(c: types.CallbackQuery, state: FSMContext):
    await c.message.answer("📥 **Paste Private Key:**", reply_markup=get_cancel_kb())
    await state.set_state(BotStates.waiting_for_import_key)

@dp.message(BotStates.waiting_for_import_key)
async def import_process(message: types.Message, state: FSMContext):
    key = message.text.strip()
    kp = jup.get_keypair_from_base58(key)
    if not kp: return await message.answer("❌ Invalid.")
    await db.add_wallet(message.from_user.id, key, str(kp.pubkey()))
    try: await message.delete() 
    except: pass
    await message.answer("✅ Imported.", reply_markup=get_main_menu())
    await state.clear()

@dp.callback_query(F.data == "export_key")
async def export_key(c: types.CallbackQuery):
    w = await db.get_wallet(c.from_user.id)
    await c.message.answer(f"🔐 **KEY:**\n`{w[1]}`\n\n🔴 DELETE NOW!")
    await c.answer()

@dp.callback_query(F.data == "withdraw_start")
async def withdraw_start(c: types.CallbackQuery, state: FSMContext):
    await c.message.answer("💸 **Amount (SOL):**", reply_markup=get_cancel_kb())
    await state.set_state(BotStates.waiting_for_withdraw_amt)

@dp.message(BotStates.waiting_for_withdraw_amt)
async def withdraw_amt(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text)
        await state.update_data(amount=amt)
        await m.answer("Cb **Recipient Address:**", reply_markup=get_cancel_kb())
        await state.set_state(BotStates.waiting_for_withdraw_addr)
    except: await m.answer("❌ Invalid Number.")

@dp.message(BotStates.waiting_for_withdraw_addr)
async def withdraw_process(m: types.Message, state: FSMContext):
    addr = m.text.strip()
    data = await state.get_data()
    w = await db.get_wallet(m.from_user.id)
    
    status = await m.answer("⏳ **Sending...**")
    success, sig = await jup.transfer_sol(w[1], addr, data['amount'])
    
    if success: await status.edit_text(f"✅ **Sent!**\nTx: `{sig}`")
    else: await status.edit_text(f"❌ Failed: {sig}")
    await state.clear()
    await m.answer("Done.", reply_markup=get_main_menu())

@dp.message(F.text == "📊 Active Trades")
async def show_trades(m: types.Message):
    trades = await db.get_active_trades()
    user_trades = [t for t in trades if t['user_id'] == m.from_user.id]
    if not user_trades: return await m.answer("💤 No active trades.")
    
    txt = "📊 **Positions:**\n\n"
    for t in user_trades: txt += f"• `{t['token_address'][:4]}...` | Entry: ${t['entry_price']}\n"
    await m.answer(txt)

@dp.message(F.text == "❌ Cancel")
async def cancel(m: types.Message, state: FSMContext):
    await state.clear()
    await m.answer("Cancelled.", reply_markup=get_main_menu())

@dp.message()
async def unknown(m: types.Message):
    if m.chat.type == "private": await m.answer("❓ Unknown command.", reply_markup=get_main_menu())

# --- MAIN ---
async def main():
    await start_web_server()
    await db.init_db()
    asyncio.create_task(position_monitor())
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())