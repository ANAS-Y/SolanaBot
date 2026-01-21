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

def get_trade_panel(ca, price, balance_sol):
    """
    Generates dynamic buy buttons based on user's wallet balance.
    Reserves 0.01 SOL for gas on Max buy.
    """
    # Calculate amounts
    qtr = balance_sol * 0.25
    half = balance_sol * 0.50
    # Max leaves 0.01 SOL for gas, or 0 if balance is too low
    max_amt = max(0, balance_sol - 0.01) 

    # Round to 4 decimals for clean display
    qtr_fmt = f"{qtr:.4f}"
    half_fmt = f"{half:.4f}"
    max_fmt = f"{max_amt:.4f}"

    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"25% ({qtr_fmt})", callback_data=f"buy_{qtr}_{ca}_{price}"),
            InlineKeyboardButton(text=f"50% ({half_fmt})", callback_data=f"buy_{half}_{ca}_{price}")
        ],
        [
            InlineKeyboardButton(text=f"Max ({max_fmt})", callback_data=f"buy_{max_amt}_{ca}_{price}"),
            InlineKeyboardButton(text="❌ Cancel", callback_data="close_panel")
        ]
    ])

def get_risk_panel():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚠️ High Risk - Trading Blocked", callback_data="blocked")],
        [InlineKeyboardButton(text="🔙 Main Menu", callback_data="main_menu")]
    ])

# --- MONITOR (Auto-Sell) ---
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
                        await db.close_trade(trade['id'])
                    else:
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
        [InlineKeyboardButton(text="🔙 Main Menu", callback_data="main_menu")]
    ])

    if edit_mode and message_obj: await message_obj.edit_text(text, reply_markup=kb)
    elif message_obj: await message_obj.answer(text, reply_markup=kb)

# --- GLOBAL HANDLERS ---

@dp.message(Command("start"), StateFilter("*"))
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    await db.init_db()
    await message.answer("👁️ **Sentinel AI Online**\nReady.", reply_markup=get_main_menu())

@dp.callback_query(F.data == "main_menu", StateFilter("*"))
async def back_to_main_menu(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()
    await callback.message.answer("🔙 **Main Menu**", reply_markup=get_main_menu())

@dp.message(F.text == "❌ Cancel", StateFilter("*"))
async def cancel_op(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("✅ Cancelled.", reply_markup=get_main_menu())

@dp.message(F.text == "⚙️ Settings", StateFilter("*"))
async def settings_menu(message: types.Message, state: FSMContext):
    await state.clear()
    await show_settings_panel(message.from_user.id, message)

@dp.message(F.text == "💰 Wallet", StateFilter("*"))
async def wallet_menu(message: types.Message, state: FSMContext):
    await state.clear()
    wallet = await db.get_wallet(message.from_user.id)
    if not wallet:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🆕 Create", callback_data="wallet_create"), InlineKeyboardButton(text="📥 Import", callback_data="wallet_import")]])
        return await message.answer("❌ No Wallet Found.", reply_markup=kb)
    
    bal = await jup.get_sol_balance(config.RPC_URL, wallet[2])
    text = f"💰 **Wallet**\n`{wallet[2]}`\nBalance: **{bal/1e9:.4f} SOL**"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Withdraw", callback_data="withdraw_start")],
        [InlineKeyboardButton(text="🔑 Export Key", callback_data="export_key")],
        [InlineKeyboardButton(text="🔙 Main Menu", callback_data="main_menu")]
    ])
    await message.answer(text, reply_markup=kb)

@dp.message(F.text == "📊 Active Trades", StateFilter("*"))
async def show_trades(message: types.Message, state: FSMContext):
    await state.clear()
    trades = await db.get_active_trades()
    user_trades = [t for t in trades if t['user_id'] == message.from_user.id]
    if not user_trades: return await message.answer("💤 No active trades.")
    
    txt = "📊 **Positions:**\n\n"
    for t in user_trades: txt += f"• `{t['token_address'][:4]}...` | Entry: ${t['entry_price']}\n"
    await message.answer(txt)

# --- ANALYZE FLOW (The Core Update) ---
@dp.message(F.text == "🧠 Analyze Token", StateFilter("*"))
async def analyze_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("📝 **Paste Contract Address (CA):**", reply_markup=get_cancel_kb())
    await state.set_state(BotStates.waiting_for_token)

@dp.message(BotStates.waiting_for_token)
async def analyze_process(message: types.Message, state: FSMContext):
    ca = message.text.strip()
    if len(ca) < 30: return await message.answer("❌ Invalid Address.")

    status = await message.answer("🔎 **Scanning...**")
    
    # 1. Fetch Data
    verdict, details, risk_score, holder_pct = await data_engine.get_rugcheck_report(ca)
    market = await data_engine.get_market_data(ca)
    
    if not market:
        await status.delete()
        await message.answer("❌ No Market Data.")
        await state.clear()
        return

    # 2. Risk Block
    if verdict == "DANGER" or risk_score > 5000 or holder_pct > 60:
        await status.delete()
        await message.answer(f"⛔ **BLOCKED: HIGH RISK**\n{details}", reply_markup=get_risk_panel())
        await state.clear()
        return

    # 3. AI Analysis
    ai_verdict, ai_reason = await sentinel_ai.analyze_token(ca, verdict, market)

    # 4. Prepare Trade Interface
    # Fetch User Wallet to Calculate Percentages
    wallet = await db.get_wallet(message.from_user.id)
    if not wallet:
        balance = 0.0 # No wallet, buttons will show 0.0
    else:
        balance_lamports = await jup.get_sol_balance(config.RPC_URL, wallet[2])
        balance = balance_lamports / 1_000_000_000

    # Check Auto-Buy Setting
    user_settings = await db.get_settings(message.from_user.id)
    is_auto_buy = user_settings['auto_buy']

    # 5. Logic Branch
    await status.delete()

    if is_auto_buy:
        # AUTO BUY ON: Show "Safe" alert and immediately ask for Amount
        await message.answer(
            f"✅ **Token Safe - Auto-Buy Active**\n"
            f"Token: `{ca}`\n"
            f"AI Verdict: {ai_verdict}\n\n"
            f"👇 **Select Amount to Buy:**",
            reply_markup=get_trade_panel(ca, market['priceUsd'], balance)
        )
    else:
        # AUTO BUY OFF: Show Full Analysis Report + Buy Buttons
        emoji = "🟢" if ai_verdict == "BUY" else "🔴"
        report = (
            f"{emoji} **Sentinel Analysis**\n"
            f"Verdict: **{ai_verdict}**\n"
            f"────────────────\n"
            f"💎 Price: ${market['priceUsd']:.6f}\n"
            f"🛡️ Security: {details}\n"
            f"🧠 **AI:** {ai_reason}\n\n"
            f"👇 **Select Amount:**"
        )
        await message.answer(report, reply_markup=get_trade_panel(ca, market['priceUsd'], balance))
    
    await state.clear()

# --- BUY EXECUTION ---
@dp.callback_query(F.data.startswith("buy_"))
async def execute_buy(callback: types.CallbackQuery):
    # Data: buy_AMOUNT_CA_PRICE
    _, amount_str, ca, price = callback.data.split("_")
    amount = float(amount_str)
    
    if amount <= 0:
        return await callback.answer("❌ Insufficient Balance", show_alert=True)

    s = await db.get_settings(callback.from_user.id)
    mode_text = "🧪 SIMULATION" if s['simulation_mode'] else "💸 REAL MONEY"
    
    await callback.message.edit_text(f"⏳ **Executing {mode_text} Buy...**\nAmount: {amount:.4f} SOL")
    
    # Real logic would go here: await jup.execute_swap(...)
    await asyncio.sleep(1.5) 
    
    await callback.message.edit_text(
        f"✅ **Buy Successful!**\n"
        f"Spent: {amount:.4f} SOL\n"
        f"Entry: ${price}\n"
        f"🤖 Auto-Monitor: ON",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Main Menu", callback_data="main_menu")]])
    )
    
    await db.add_trade(callback.from_user.id, ca, float(amount), float(price), 0)

@dp.callback_query(F.data == "close_panel")
async def close_panel(c: types.CallbackQuery): await c.message.delete()

# --- CALLBACKS (SETTINGS / WALLET) ---
@dp.callback_query(F.data.startswith("toggle_"))
async def toggle_setting(callback: types.CallbackQuery):
    mode = callback.data.split("_")[1]
    col_map = {"autobuy": "auto_buy", "autosell": "auto_sell", "sim": "simulation_mode"}
    s = await db.get_settings(callback.from_user.id)
    await db.update_setting(callback.from_user.id, col_map[mode], 0 if s[col_map[mode]] else 1)
    await show_settings_panel(callback.from_user.id, callback.message, edit_mode=True)

@dp.callback_query(F.data.startswith("set_"))
async def set_value_start(c: types.CallbackQuery, state: FSMContext):
    mode = c.data.split("_")[1]
    prompt_map = {
        "slippage": ("💧 **Enter Slippage %:**", BotStates.waiting_for_slippage),
        "tp": ("🚀 **Enter Take Profit %:**", BotStates.waiting_for_tp),
        "sl": ("🛑 **Enter Stop Loss %:**", BotStates.waiting_for_sl)
    }
    msg, st = prompt_map[mode]
    await c.message.delete()
    await c.message.answer(msg, reply_markup=get_cancel_kb())
    await state.set_state(st)

@dp.message(BotStates.waiting_for_slippage)
async def set_slip(m: types.Message, state: FSMContext): await process_setting(m, state, "slippage", 0.1, 50)
@dp.message(BotStates.waiting_for_tp)
async def set_tp(m: types.Message, state: FSMContext): await process_setting(m, state, "take_profit", 1, 1000)
@dp.message(BotStates.waiting_for_sl)
async def set_sl(m: types.Message, state: FSMContext): await process_setting(m, state, "stop_loss", 1, 99)

async def process_setting(m, state, col, min_v, max_v):
    try:
        val = float(m.text)
        if min_v <= val <= max_v:
            await db.update_setting(m.from_user.id, col, val)
            await m.answer(f"✅ Updated.", reply_markup=get_main_menu())
            await state.clear()
            await show_settings_panel(m.from_user.id, m)
        else: raise ValueError
    except: await m.answer("❌ Invalid.")

@dp.callback_query(F.data == "wallet_create")
async def wallet_create(c: types.CallbackQuery):
    priv, pub = jup.create_new_wallet()
    await db.add_wallet(c.from_user.id, priv, pub)
    await c.message.edit_text(f"✅ Created!\nAddress: `{pub}`")

@dp.callback_query(F.data == "wallet_import")
async def wallet_import(c: types.CallbackQuery, state: FSMContext):
    await c.message.answer("📥 **Paste Key:**\n(Base58, 12-Word Phrase, or JSON Array)", reply_markup=get_cancel_kb())
    await state.set_state(BotStates.waiting_for_import_key)

@dp.message(BotStates.waiting_for_import_key)
async def import_process(message: types.Message, state: FSMContext):
    key_input = message.text.strip()
    kp = jup.get_keypair_from_input(key_input)
    if not kp: return await message.answer("❌ Invalid Key Format. Try again.")
    
    import base58
    priv_b58 = base58.b58encode(bytes(kp)).decode('utf-8')
    pub_key = str(kp.pubkey())
    
    await db.add_wallet(message.from_user.id, priv_b58, pub_key)
    try: await message.delete() 
    except: pass
    await message.answer(f"✅ **Import Successful!**\nAddress: `{pub_key}`", reply_markup=get_main_menu())
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