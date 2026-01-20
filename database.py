import aiosqlite
import logging

DB_NAME = "sentinel.db"

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        # User Wallet Table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS wallets (
                user_id INTEGER PRIMARY KEY,
                private_key TEXT,
                public_key TEXT
            )
        """)
        
        # Active Trades Table (For the Auto-Sell Monitor)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                token_address TEXT,
                amount_sol REAL,
                entry_price REAL,
                token_amount REAL,
                status TEXT DEFAULT 'OPEN', -- OPEN, CLOSED
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # User Settings
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                user_id INTEGER PRIMARY KEY,
                slippage REAL DEFAULT 1.0,
                auto_buy BOOLEAN DEFAULT 0
            )
        """)
        await db.commit()

# --- WALLET OPS ---
async def get_wallet(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT * FROM wallets WHERE user_id = ?", (user_id,)) as cursor:
            return await cursor.fetchone()

async def add_wallet(user_id, priv, pub):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR REPLACE INTO wallets (user_id, private_key, public_key) VALUES (?, ?, ?)", 
                         (user_id, priv, pub))
        await db.commit()

# --- TRADE OPS ---
async def add_trade(user_id, ca, sol_amt, entry_price, token_amt):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT INTO trades (user_id, token_address, amount_sol, entry_price, token_amount)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, ca, sol_amt, entry_price, token_amt))
        await db.commit()

async def get_active_trades():
    """Fetches all OPEN trades for the background monitor"""
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM trades WHERE status = 'OPEN'") as cursor:
            return await cursor.fetchall()

async def close_trade(trade_id):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE trades SET status = 'CLOSED' WHERE id = ?", (trade_id,))
        await db.commit()

# --- SETTINGS OPS ---
async def get_settings(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT * FROM settings WHERE user_id = ?", (user_id,)) as cursor:
            res = await cursor.fetchone()
            if not res:
                # Default settings
                return (user_id, 1.0, 0) # 1% slippage, Auto-Buy OFF
            return res