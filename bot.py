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
    waiting_for_custom_buy = State()

# --- MENUS ---
def get_main_menu():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🧠 Analyze Token"), KeyboardButton(text="💰 Wallet")],
        [KeyboardButton(text="📊 Active Trades"), KeyboardButton(text="⚙️ Settings")],
        [KeyboardButton(text="❌ Cancel")]
    ], resize_keyboard=True)

def get_cancel_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Cancel")]], resize_keyboard=True)

def get_trade_panel(balance_sol, sol_price):
    qtr = balance_sol * 0.25
    half = balance_sol * 0.50
    max_amt = max(0, balance_sol - 0.01)

    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"25% (${qtr*sol_price:.0f})", callback_data="buy_25"),
            InlineKeyboardButton(text=f"50% (${half*sol_price:.0f})", callback_data="buy_50")
        ],
        [
            InlineKeyboardButton(text=f"Max (${max_amt*sol_price:.0f})", callback_data="buy_max"),
            InlineKeyboardButton(text="⌨️ Custom Amount", callback_data="buy_custom")
        ],
        [InlineKeyboardButton(text="❌ Close", callback_data="close_panel")]
    ])

def get_risk_panel():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚠️ Trading Blocked", callback_data="blocked")],
        [InlineKeyboardButton(text="🔙 Main Menu", callback_data="main_menu")]
    ])

# --- BACKGROUND MONITOR (Missing Function Restored) ---
async def position_monitor():
    """
    Checks active trades for TP/SL and Auto-Sells if enabled.
    This runs forever in the background.
    """
    while True:
        try:
            trades = await db.get_active_trades()
            for trade in trades:
                # 1. Get User Settings
                settings = await db.get_settings(trade['user_id'])
                tp_target = settings['take_profit']
                sl_target = settings['stop_loss'] * -1 # Make negative
                auto_sell = settings['auto_sell']

                # 2. Get Live Market Data
                market = await data_engine.get_market_data(trade['token_address'])
                if not market: continue
                
                curr_price = market['priceUsd']
                entry = trade['entry_price']
                
                # 3. Calculate PnL
                if entry > 0:
                    pnl = ((curr_price - entry) / entry) * 100
                else:
                    pnl = 0.0

                # 4. Check Triggers
                triggered = False
                msg_type = ""
                
                if pnl >= tp_target:
                    triggered = True
                    msg_type = "🚀 <b>Take Profit Hit!</b>"
                elif pnl <= sl_target:
                    triggered = True
                    msg_type = "🛑 <b>Stop Loss Hit!</b>"

                # 5. Action
                if triggered:
                    if auto_sell:
                        await bot.send_message(
                            trade['user_id'], 
                            f"{msg_type}\n"
                            f"<b>Token:</b> <code>{market['name']}</code>\n"
                            f"<b>PnL:</b> {pnl:.2f}%\n"
                            f"✅ <i>Auto-Selling...</i>",
                            parse_mode="HTML"
                        )
                        # Execute Sell Logic Here (Future Integration)
                        await db.close_trade(trade['id'])
                    else:
                        # Optional: Alert user manually (Logic to prevent spamming needed)
                        pass 

        except Exception as e:
            logging.error(f"Monitor Error: {e}")
        
        # Wait 15 seconds before next check
        await asyncio.sleep(15)

# --- GLOBAL HANDLERS ---
@dp.message(Command("start"), StateFilter("*"))
async def start(m: types.Message, state: FSMContext):
    await state.clear()
    await db.init_db()
    await m.answer("👁️ <b>Sentinel AI Online</b>\nSystem Ready.", reply_markup=get_main_menu(), parse_mode="HTML")

@dp.callback_query(F.data == "main_menu", StateFilter("*"))
async def menu_cb(c: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.delete()
    await c.message.answer("🔙 <b>Main Menu</b>", reply_markup=get_main_menu(), parse_mode="HTML")

@dp.message(F.text == "❌ Cancel", StateFilter("*"))
async def cancel(m: types.Message, state: FSMContext):
    await state.clear()
    await m.answer("✅ Operation Cancelled.", reply_markup=get_main_menu())

@dp.callback_query(F.data == "close_panel")
async def close(c: types.CallbackQuery): await c.message.delete()

# --- WALLET MENU ---
@dp.message(F.text == "💰 Wallet", StateFilter("*"))
async def wallet_menu(m: types.Message, state: FSMContext):
    await state.clear()
    w = await db.get_wallet(m.from_user.id)
    
    if not w:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🆕 Create New", callback_data="wallet_create")],
            [InlineKeyboardButton(text="📥 Import Key", callback_data="wallet_import")]
        ])
        return await m.answer("❌ <b>No Wallet Found</b>\nConnect a wallet to begin.", reply_markup=kb, parse_mode="HTML")
    
    msg = await m.answer("⏳ <i>Syncing Blockchain...</i>", parse_mode="HTML")
    
    bal_lamports = await jup.get_sol_balance(config.RPC_URL, w[2])
    bal_sol = bal_lamports / 1e9
    sol_price = await data_engine.get_sol_price()
    
    info = (
        f"💰 <b>Wallet Dashboard</b>\n"
        f"──────────────────\n"
        f"<b>Address:</b> <code>{w[2]}</code>\n\n"
        f"<b>Balance:</b> {bal_sol:.4f} SOL\n"
        f"<b>Value:</b>   ${(bal_sol * sol_price):.2f} USD\n"
        f"──────────────────"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Withdraw", callback_data="withdraw_start"), InlineKeyboardButton(text="🔑 View Key", callback_data="export_key")],
        [InlineKeyboardButton(text="🔄 Refresh", callback_data="refresh_wallet"), InlineKeyboardButton(text="🔙 Menu", callback_data="main_menu")]
    ])
    
    await msg.delete()
    await m.answer(info, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data == "refresh_wallet")
async def refresh_wallet(c: types.CallbackQuery):
    await wallet_menu(c.message, None) 
    await c.answer("Refreshed")

# --- ACTIVE TRADES ---
@dp.message(F.text == "📊 Active Trades", StateFilter("*"))
async def active_trades(m: types.Message):
    trades = await db.get_active_trades()
    user_trades = [t for t in trades if t['user_id'] == m.from_user.id]
    
    if not user_trades:
        return await m.answer("💤 <b>No Active Positions.</b>", parse_mode="HTML")
    
    status = await m.answer("⏳ <i>Fetching Live Prices...</i>", parse_mode="HTML")
    sol_price = await data_engine.get_sol_price()
    
    text = "📊 <b>Active Portfolio</b>\n──────────────────\n"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    
    for t in user_trades:
        market = await data_engine.get_market_data(t['token_address'])
        if not market: continue
        
        invested_sol = t['amount_sol']
        invested_usd = invested_sol * sol_price
        curr_price = market['priceUsd']
        entry_price = t['entry_price']
        
        if entry_price > 0:
            pnl_pct = ((curr_price - entry_price) / entry_price) * 100
        else:
            pnl_pct = 0.0
        
        emoji = "🟢" if pnl_pct >= 0 else "🔴"
        
        # Format Market Cap (FDV)
        mcap = market['fdv']
        if mcap > 1_000_000: mcap_str = f"${mcap/1_000_000:.1f}M"
        elif mcap > 1_000: mcap_str = f"${mcap/1_000:.1f}K"
        else: mcap_str = f"${mcap:.0f}"

        text += (
            f"🔹 <b>{market['name']}</b> ({market['symbol']})\n"
            f"   Invested: {invested_sol:.2f} SOL (${invested_usd:.0f})\n"
            f"   PnL:      {emoji} {pnl_pct:+.2f}%\n"
            f"   MCap:     {mcap_str}\n"
            f"──────────────────\n"
        )
        
        kb.inline_keyboard.append([
            InlineKeyboardButton(text=f"Sell {market['symbol']}", callback_data=f"sell_manual_{t['id']}")
        ])
    
    kb.inline_keyboard.append([InlineKeyboardButton(text="🔙 Menu", callback_data="main_menu")])
    
    await status.delete()
    await m.answer(text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("sell_manual_"))
async def manual_sell(c: types.CallbackQuery):
    trade_id = int(c.data.split("_")[2])
    await db.close_trade(trade_id)
    await c.message.edit_text("✅ <b>Position Sold/Closed.</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Menu", callback_data="main_menu")]]))

# --- ANALYZE & BUY ---
@dp.message(F.text == "🧠 Analyze Token", StateFilter("*"))
async def analyze_start(m: types.Message, state: FSMContext):
    await state.clear()
    await m.answer("📝 <b>Paste Token Address (CA):</b>", reply_markup=get_cancel_kb(), parse_mode="HTML")
    await state.set_state(BotStates.waiting_for_token)

@dp.message(BotStates.waiting_for_token)
async def analyze_process(m: types.Message, state: FSMContext):
    ca = m.text.strip()
    if len(ca) < 30: return await m.answer("❌ Invalid Address.")

    status = await m.answer("🔎 <i>Scanning Token Security...</i>", parse_mode="HTML")
    
    verdict, details, risk_score, holder_pct = await data_engine.get_rugcheck_report(ca)
    market = await data_engine.get_market_data(ca)
    sol_price = await data_engine.get_sol_price()
    
    if not market:
        await status.delete()
        await m.answer("❌ <b>Data Unavailable</b>\nToken might be too new.", parse_mode="HTML")
        return

    if verdict == "DANGER" or risk_score > 5000 or holder_pct > 60:
        await status.delete()
        await m.answer(f"⛔ <b>TRADING BLOCKED</b>\nReason: High Risk.\n\n{details}", parse_mode="HTML", reply_markup=get_risk_panel())
        return

    ai_verdict, ai_reason = await sentinel_ai.analyze_token(ca, verdict, market)
    
    w = await db.get_wallet(m.from_user.id)
    bal_sol = 0.0
    if w:
        bal_sol = (await jup.get_sol_balance(config.RPC_URL, w[2])) / 1e9
    
    await state.update_data(active_token=ca, active_price=market['priceUsd'], balance=bal_sol, sol_price=sol_price)
    
    s = await db.get_settings(m.from_user.id)
    await status.delete()

    if s['auto_buy']:
        await m.answer(
            f"✅ <b>Safe - Auto Buy Active</b>\nToken: <code>{market['name']}</code>\n👇 <b>Select Investment Amount:</b>",
            reply_markup=get_trade_panel(bal_sol, sol_price),
            parse_mode="HTML"
        )
    else:
        emoji = "🟢" if ai_verdict == "BUY" else "🟡"
        report = (
            f"{emoji} <b>Analysis Report</b>\n"
            f"──────────────────\n"
            f"<b>Token:</b> {market['name']} ({market['symbol']})\n"
            f"<b>Price:</b> ${market['priceUsd']:.6f}\n"
            f"<b>MCap:</b>  ${market['fdv']:,.0f}\n"
            f"──────────────────\n"
            f"🛡️ <b>Security:</b>\n{details}\n\n"
            f"🧠 <b>AI Verdict:</b> {ai_reason}\n"
            f"──────────────────\n"
            f"👇 <b>Select Action:</b>"
        )
        await m.answer(report, reply_markup=get_trade_panel(bal_sol, sol_price), parse_mode="HTML")

# --- BUY EXECUTION ---
@dp.callback_query(F.data.startswith("buy_"))
async def buy_handler(c: types.CallbackQuery, state: FSMContext):
    mode = c.data.split("_")[1]
    if mode == "custom":
        await c.message.answer("⌨️ <b>Enter Amount:</b>\nExample: <code>0.5</code> (SOL) or <code>$50</code> (USD)", parse_mode="HTML", reply_markup=get_cancel_kb())
        await state.set_state(BotStates.waiting_for_custom_buy)
        await c.answer()
        return

    data = await state.get_data()
    bal = data.get("balance", 0.0)
    
    amt = 0.0
    if mode == "25": amt = bal * 0.25
    elif mode == "50": amt = bal * 0.50
    elif mode == "max": amt = max(0, bal - 0.01)
    
    await execute_trade(c.message, state, amt)
    await c.answer()

@dp.message(BotStates.waiting_for_custom_buy)
async def custom_buy_process(m: types.Message, state: FSMContext):
    text = m.text.strip()
    data = await state.get_data()
    sol_price = data.get("sol_price", 0)
    try:
        if text.startswith("$"):
            usd = float(text.replace("$", ""))
            sol = usd / sol_price
        else:
            sol = float(text)
        await execute_trade(m, state, sol)
    except: await m.answer("❌ Invalid Amount.", parse_mode="HTML")

async def execute_trade(message_obj, state, amount_sol):
    data = await state.get_data()
    ca = data.get("active_token")
    price = data.get("active_price")
    sol_price = data.get("sol_price", 0)
    
    if amount_sol <= 0: return await message_obj.answer("❌ Insufficient Funds.")

    user_id = message_obj.from_user.id
    s = await db.get_settings(user_id)
    mode_text = "🧪 SIMULATION" if s['simulation_mode'] else "💸 REAL"
    
    usd_val = amount_sol * sol_price
    msg = await message_obj.answer(f"⏳ <b>Executing {mode_text} Buy...</b>\nAmount: {amount_sol:.4f} SOL (${usd_val:.2f})", parse_mode="HTML")
    await asyncio.sleep(1) 
    
    await db.add_trade(user_id, ca, amount_sol, price, 0)
    
    await msg.edit_text(
        f"✅ <b>Buy Successful!</b>\n──────────────────\n<b>Invested:</b> {amount_sol:.4f} SOL (${usd_val:.2f})\n<b>Entry:</b> ${price:.6f}\n🤖 <b>Auto-Monitor:</b> ON",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Menu", callback_data="main_menu")]])
    )
    await state.clear()

# --- SETTINGS, KEY MGMT, ETC ---
@dp.message(F.text == "⚙️ Settings", StateFilter("*"))
async def settings(m: types.Message): await show_settings_panel(m.from_user.id, m)

async def show_settings_panel(user_id, message_obj=None, edit_mode=False):
    s = await db.get_settings(user_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💧 Slippage: {s['slippage']}%", callback_data="set_slippage")],
        [InlineKeyboardButton(text=f"🚀 TP: +{s['take_profit']}%", callback_data="set_tp"), InlineKeyboardButton(text=f"🛑 SL: -{s['stop_loss']}%", callback_data="set_sl")],
        [InlineKeyboardButton(text=f"🤖 Buy: {'ON' if s['auto_buy'] else 'OFF'}", callback_data="toggle_autobuy"), InlineKeyboardButton(text=f"📉 Sell: {'ON' if s['auto_sell'] else 'OFF'}", callback_data="toggle_autosell")],
        [InlineKeyboardButton(text=f"Mode: {'🧪 SIM' if s['simulation_mode'] else '💸 REAL'}", callback_data="toggle_sim")],
        [InlineKeyboardButton(text="🔙 Menu", callback_data="main_menu")]
    ])
    text = "⚙️ <b>Configuration</b>"
    if edit_mode: await message_obj.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else: await message_obj.answer(text, reply_markup=kb, parse_mode="HTML")

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
    await c.message.delete()
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
    await c.message.edit_text(f"✅ Created!\nAddress: <code>{pub}</code>", parse_mode="HTML")

@dp.callback_query(F.data == "wallet_import")
async def w_import(c: types.CallbackQuery, state: FSMContext):
    await c.message.answer("📥 <b>Paste Key:</b>", reply_markup=get_cancel_kb(), parse_mode="HTML")
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
    await c.message.answer(f"🔐 <code>{w[1]}</code>\n🔴 DELETE NOW!", parse_mode="HTML")
    await c.answer()

@dp.callback_query(F.data == "withdraw_start")
async def with_start(c: types.CallbackQuery, state: FSMContext):
    await c.message.answer("💸 <b>Amount:</b>", reply_markup=get_cancel_kb(), parse_mode="HTML")
    await state.set_state(BotStates.waiting_for_withdraw_amt)

@dp.message(BotStates.waiting_for_withdraw_amt)
async def with_amt(m: types.Message, state: FSMContext):
    try:
        await state.update_data(amt=float(m.text))
        await m.answer("Cb <b>Address:</b>", reply_markup=get_cancel_kb(), parse_mode="HTML")
        await state.set_state(BotStates.waiting_for_withdraw_addr)
    except: await m.answer("❌ Invalid.")

@dp.message(BotStates.waiting_for_withdraw_addr)
async def with_exec(m: types.Message, state: FSMContext):
    d = await state.get_data()
    w = await db.get_wallet(m.from_user.id)
    res, sig = await jup.transfer_sol(w[1], m.text.strip(), d['amt'])
    await m.answer(f"✅ Sent: <code>{sig}</code>" if res else f"❌ Error: {sig}", reply_markup=get_main_menu(), parse_mode="HTML")
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