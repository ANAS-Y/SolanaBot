import os
import asyncpg
import logging

DB_URL = os.getenv("DATABASE_URL")

async def init_db():
    if not DB_URL:
        logging.error("❌ DATABASE_URL is missing!")
        return

    try:
        conn = await asyncpg.connect(DB_URL)
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS wallets (
                user_id BIGINT PRIMARY KEY,
                public_key TEXT,
                encrypted_priv_key BYTEA,
                salt BYTEA
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS active_trades (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                token_mint TEXT,
                amount_tokens REAL,
                entry_price_usd REAL,
                stop_loss_pct REAL,
                take_profit_pct REAL
            )
        ''')
        await conn.close()
        logging.info("✅ Database ready.")
    except Exception as e:
        logging.error(f"❌ DB Init Error: {e}")

async def add_wallet(user_id, pub_key, enc_key, salt):
    conn = await asyncpg.connect(DB_URL)
    try:
        await conn.execute('''
            INSERT INTO wallets (user_id, public_key, encrypted_priv_key, salt)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id) DO UPDATE 
            SET public_key = $2, encrypted_priv_key = $3, salt = $4
        ''', user_id, pub_key, enc_key, salt)
    finally:
        await conn.close()

async def get_wallet(user_id):
    conn = await asyncpg.connect(DB_URL)
    try:
        row = await conn.fetchrow('SELECT encrypted_priv_key, salt, public_key FROM wallets WHERE user_id = $1', user_id)
        if row:
            return (row['encrypted_priv_key'], row['salt'], row['public_key'])
        return None
    finally:
        await conn.close()

# --- NEW FUNCTION TO FIX "ALREADY EXISTS" BUG ---
async def delete_wallet(user_id):
    conn = await asyncpg.connect(DB_URL)
    try:
        await conn.execute('DELETE FROM wallets WHERE user_id = $1', user_id)
    finally:
        await conn.close()

async def add_trade(user_id, mint, amount, entry, sl, tp):
    conn = await asyncpg.connect(DB_URL)
    try:
        await conn.execute('''
            INSERT INTO active_trades (user_id, token_mint, amount_tokens, entry_price_usd, stop_loss_pct, take_profit_pct) 
            VALUES ($1, $2, $3, $4, $5, $6)
        ''', user_id, mint, amount, entry, sl, tp)
    finally:
        await conn.close()

async def get_active_trades():
    conn = await asyncpg.connect(DB_URL)
    try:
        rows = await conn.fetch('SELECT * FROM active_trades')
        return [(r['id'], r['user_id'], r['token_mint'], r['amount_tokens'], r['entry_price_usd'], r['stop_loss_pct'], r['take_profit_pct']) for r in rows]
    finally:
        await conn.close()

async def delete_trade(trade_id):
    conn = await asyncpg.connect(DB_URL)
    try:
        await conn.execute('DELETE FROM active_trades WHERE id = $1', trade_id)
    finally:
        await conn.close()