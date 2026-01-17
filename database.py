import aiosqlite
import logging

DB_NAME = "secure_bot.db"

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''CREATE TABLE IF NOT EXISTS wallets
                            (user_id INTEGER PRIMARY KEY, 
                             public_key TEXT, 
                             encrypted_priv_key BLOB, 
                             salt BLOB)''')
        
        await db.execute('''CREATE TABLE IF NOT EXISTS active_trades
                            (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                             user_id INTEGER, 
                             token_mint TEXT, 
                             amount_tokens REAL, 
                             entry_price_usd REAL, 
                             stop_loss_pct REAL, 
                             take_profit_pct REAL)''')
        await db.commit()

async def add_wallet(user_id, pub_key, enc_key, salt):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT INTO wallets VALUES (?, ?, ?, ?)", 
                         (user_id, pub_key, enc_key, salt))
        await db.commit()

async def get_wallet(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT encrypted_priv_key, salt, public_key FROM wallets WHERE user_id=?", (user_id,)) as cursor:
            return await cursor.fetchone()

async def add_trade(user_id, mint, amount, entry, sl, tp):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""INSERT INTO active_trades 
                            (user_id, token_mint, amount_tokens, entry_price_usd, stop_loss_pct, take_profit_pct) 
                            VALUES (?, ?, ?, ?, ?, ?)""", 
                            (user_id, mint, amount, entry, sl, tp))
        await db.commit()

async def get_active_trades():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT * FROM active_trades") as cursor:
            return await cursor.fetchall()

async def delete_trade(trade_id):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM active_trades WHERE id=?", (trade_id,))
        await db.commit()