import asyncio
import logging
import os
import sys
import config 
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
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

def get_trade_panel(balance_sol):
    """
    Short Callback Data to fit Telegram 64-byte limit.
    """
    qtr = balance_sol * 0.25
    half = balance_sol * 0.50
    max_amt = max(0, balance_sol - 0.01)

    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"25% ({qtr:.3f})", callback_data="buy_25"),
            InlineKeyboardButton(text=f"50% ({half:.3f})", callback_data="buy_50")
        ],
        [
            InlineKeyboardButton(text=f"Max ({max_amt:.3f})", callback_data="buy_max"),
            InlineKeyboardButton(text="❌ Close", callback_data="close_panel")
        ]
    ])

def get_risk_panel():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚠️ Trading Blocked", callback_data="blocked")],
        [InlineKeyboardButton(text="🔙 Main Menu", callback_data="main_menu")]
    ])

# --- MONITOR ---
async def position_monitor():
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

                if pnl >= tp_target or pnl <= sl_target:
                    msg = "🚀 TP Hit!" if pnl > 0 else "🛑 SL Hit!"
                    if auto_sell:
                        await bot.send_message(trade['user_id'], f"{msg} PnL: {pnl:.2f}% (Auto-Sold)")
                        await db.close_trade(trade['id'])
        except Exception as e:
            logging.error(f"Monitor: {e}")
        await asyncio.sleep(15)

# --- SETTINGS PANEL ---
async def show_settings_panel(user_id, message_obj=None, edit_mode=False):
    s = await db.get_settings(user_id)
    text = "⚙️ **Configuration**"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💧 Slip: {s['slippage']}%", callback_data="set_slippage")],
        [InlineKeyboardButton(text=f"🚀 TP: {s['take_profit']}%", callback_data="set_tp"), InlineKeyboardButton(text=f"🛑 SL: {s['stop_loss']}%", callback_data="set_sl")],
        [InlineKeyboardButton(text=f"🤖 Buy: {'ON' if s['auto_buy'] else 'OFF'}", callback_data="toggle_autobuy"), InlineKeyboardButton(text=f"📉 Sell: {'ON' if s['auto_sell'] else 'OFF'}", callback_data="toggle_autosell")],
        [InlineKeyboardButton(text=f"Mode: {'🧪 SIM' if s['simulation_mode'] else '💸 REAL'}", callback_data="toggle_sim")],
        [InlineKeyboardButton(text="🔙 Menu", callback_data="main_menu")]
    ])
    if edit_mode: await message_obj.edit_text(text, reply_markup=kb)
    else: await message_obj.answer(text, reply_markup=kb)

# --- GLOBAL HANDLERS ---
@dp.message(Command("start"), StateFilter("*"))
async def start(m: types.Message, state: FSMContext):
    await state.clear()
    await db.init_db()
    await m.answer("👁️ **Sentinel AI**\nReady.", reply_markup=get_main_menu())

@dp.callback_query(F.data == "main_menu", StateFilter("*"))
async def menu_cb(c: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.delete()
    await c.message.answer("🔙 **Menu**", reply_markup=get_main_menu())

@dp.message(F.text == "❌ Cancel", StateFilter("*"))
async def cancel(m: types.Message, state: FSMContext):
    await state.clear()
    await m.answer("Cancelled.", reply_markup=get_main_menu())

@dp.message(F.text == "⚙️ Settings", StateFilter("*"))
async def settings(m: types.Message): await show_settings_panel(m.from_user.id, m)

@dp.message(F.text == "💰 Wallet", StateFilter("*"))
async def wallet(m: types.Message):
    w = await db.get_wallet(m.from_user.id)
    if not w:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Create", callback_data="wallet_create"), InlineKeyboardButton(text="Import", callback_data="wallet_import")]])
        return await m.answer("❌ No Wallet.", reply_markup=kb)
    bal = await jup.get_sol_balance(config.RPC_URL, w[2])
    text = f"💰 **Wallet**\n`{w[2]}`\nBal: **{bal/1e9:.4f} SOL**"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Withdraw", callback_data="withdraw_start"), InlineKeyboardButton(text="Key", callback_data="export_key")]])
    await m.answer(text, reply_markup=kb)

# --- ANALYZE ---
@dp.message(F.text == "🧠 Analyze Token", StateFilter("*"))
async def analyze_start(m: types.Message, state: FSMContext):
    await state.clear()
    await m.answer("📝 **Paste CA:**", reply_markup=get_cancel_kb())
    await state.set_state(BotStates.waiting_for_token)

@dp.message(BotStates.waiting_for_token)
async def analyze_process(m: types.Message, state: FSMContext):
    ca = m.text.strip()
    if len(ca) < 30: return await m.answer("❌ Invalid.")
    
    status = await m.answer("🔎 **Scanning...**")
    
    verdict, details, risk, holder = await data_engine.get_rugcheck_report(ca)
    market = await data_engine.get_market_data(ca)
    
    if not market:
        await status.delete()
        await m.answer("❌ No Data.")
        return

    if verdict == "DANGER" or risk > 5000 or holder > 60:
        await status.delete()
        await m.answer(f"⛔ **BLOCKED**\n{details}", reply_markup=get_risk_panel())
        return

    ai_verdict, ai_reason = await sentinel_ai.analyze_token(ca, verdict, market)
    
    # STORE DATA IN STATE (This fixes the button crash)
    wallet = await db.get_wallet(m.from_user.id)
    bal = 0.0
    if wallet:
        bal_lamports = await jup.get_sol_balance(config.RPC_URL, wallet[2])
        bal = bal_lamports / 1e9
    
    # Save CA and Price to context for the Buy button to use later
    await state.update_data(active_token=ca, active_price=market['priceUsd'], balance=bal)

    # Check Auto-Buy
    s = await db.get_settings(m.from_user.id)
    await status.delete()

    if s['auto_buy']:
        await m.answer(f"✅ **Safe - Auto Buy Active**\nToken: `{ca}`\n👇 Select Amount:", reply_markup=get_trade_panel(bal))
    else:
        text = (f"🟢 **Verdict: {ai_verdict}**\nPrice: ${market['priceUsd']:.6f}\n🛡️ {details}\n🧠 {ai_reason}")
        await m.answer(text, reply_markup=get_trade_panel(bal))

# --- BUY EXECUTION ---
@dp.callback_query(F.data.startswith("buy_"))
async def execute_buy(c: types.CallbackQuery, state: FSMContext):
    # Retrieve data from state
    data = await state.get_data()
    ca = data.get("active_token")
    price = data.get("active_price")
    bal = data.get("balance", 0.0)

    if not ca:
        return await c.answer("⚠️ Session Expired. Please analyze again.", show_alert=True)

    mode = c.data.split("_")[1] # 25, 50, max
    
    amount = 0.0
    if mode == "25": amount = bal * 0.25
    elif mode == "50": amount = bal * 0.50
    elif mode == "max": amount = max(0, bal - 0.01)

    if amount <= 0: return await c.answer("❌ Insufficient Funds", show_alert=True)

    s = await db.get_settings(c.from_user.id)
    sim_txt = "🧪 SIMULATION" if s['simulation_mode'] else "💸 REAL MONEY"
    
    await c.message.edit_text(f"⏳ **Executing {sim_txt} Buy...**\nAmount: {amount:.4f} SOL")
    await asyncio.sleep(1)
    
    await db.add_trade(c.from_user.id, ca, amount, price, 0)
    await c.message.edit_text(f"✅ **Buy Success!**\nEntry: ${price}\n🤖 Auto-Monitor: ON", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Menu", callback_data="main_menu")]]))

@dp.callback_query(F.data == "close_panel")
async def close(c: types.CallbackQuery): await c.message.delete()

# --- OTHER CALLBACKS ---
@dp.callback_query(F.data.startswith("toggle_"))
async def toggle(c: types.CallbackQuery):
    mode = c.data.split("_")[1]
    col = {"autobuy": "auto_buy", "autosell": "auto_sell", "sim": "simulation_mode"}[mode]
    s = await db.get_settings(c.from_user.id)
    await db.update_setting(c.from_user.id, col, 0 if s[col] else 1)
    await show_settings_panel(c.from_user.id, c.message, edit_mode=True)

@dp.callback_query(F.data.startswith("set_"))
async def set_val_start(c: types.CallbackQuery, state: FSMContext):
    mode = c.data.split("_")[1]
    states = {"slippage": BotStates.waiting_for_slippage, "tp": BotStates.waiting_for_tp, "sl": BotStates.waiting_for_sl}
    await c.message.answer(f"Enter Value for {mode.upper()}:", reply_markup=get_cancel_kb())
    await state.set_state(states[mode])

@dp.message(BotStates.waiting_for_slippage)
async def set_slip(m: types.Message, state: FSMContext): await save_setting(m, state, "slippage", 0.1, 50)
@dp.message(BotStates.waiting_for_tp)
async def set_tp(m: types.Message, state: FSMContext): await save_setting(m, state, "take_profit", 1, 1000)
@dp.message(BotStates.waiting_for_sl)
async def set_sl(m: types.Message, state: FSMContext): await save_setting(m, state, "stop_loss", 1, 99)

async def save_setting(m, state, col, min_v, max_v):
    try:
        val = float(m.text)
        if min_v <= val <= max_v:
            await db.update_setting(m.from_user.id, col, val)
            await m.answer("✅ Saved.", reply_markup=get_main_menu())
            await state.clear()
        else: raise ValueError
    except: await m.answer("❌ Invalid.")

@dp.callback_query(F.data == "wallet_create")
async def w_create(c: types.CallbackQuery):
    priv, pub = jup.create_new_wallet()
    await db.add_wallet(c.from_user.id, priv, pub)
    await c.message.edit_text(f"✅ Created!\n`{pub}`")

@dp.callback_query(F.data == "wallet_import")
async def w_import(c: types.CallbackQuery, state: FSMContext):
    await c.message.answer("📥 **Paste Key:**", reply_markup=get_cancel_kb())
    await state.set_state(BotStates.waiting_for_import_key)

@dp.message(BotStates.waiting_for_import_key)
async def w_save(m: types.Message, state: FSMContext):
    kp = jup.get_keypair_from_input(m.text.strip())
    if not kp: return await m.answer("❌ Invalid.")
    import base58
    await db.add_wallet(m.from_user.id, base58.b58encode(bytes(kp)).decode('utf-8'), str(kp.pubkey()))
    try: await m.delete() 
    except: pass
    await m.answer("✅ Imported.", reply_markup=get_main_menu())
    await state.clear()

@dp.callback_query(F.data == "export_key")
async def export(c: types.CallbackQuery):
    w = await db.get_wallet(c.from_user.id)
    await c.message.answer(f"🔐 `{w[1]}`\n🔴 DELETE NOW!")
    await c.answer()

@dp.callback_query(F.data == "withdraw_start")
async def with_start(c: types.CallbackQuery, state: FSMContext):
    await c.message.answer("💸 **Amount:**", reply_markup=get_cancel_kb())
    await state.set_state(BotStates.waiting_for_withdraw_amt)

@dp.message(BotStates.waiting_for_withdraw_amt)
async def with_amt(m: types.Message, state: FSMContext):
    try:
        await state.update_data(amt=float(m.text))
        await m.answer("Cb **Address:**", reply_markup=get_cancel_kb())
        await state.set_state(BotStates.waiting_for_withdraw_addr)
    except: await m.answer("❌ Invalid.")

@dp.message(BotStates.waiting_for_withdraw_addr)
async def with_exec(m: types.Message, state: FSMContext):
    d = await state.get_data()
    w = await db.get_wallet(m.from_user.id)
    res, sig = await jup.transfer_sol(w[1], m.text.strip(), d['amt'])
    await m.answer(f"✅ Sent: `{sig}`" if res else f"❌ Error: {sig}", reply_markup=get_main_menu())
    await state.clear()

@dp.message()
async def unknown(m: types.Message):
    if m.chat.type == "private": await m.answer("❓ Unknown command.", reply_markup=get_main_menu())

async def main():
    await start_web_server()
    await db.init_db()
    asyncio.create_task(position_monitor())
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__": asyncio.run(main())