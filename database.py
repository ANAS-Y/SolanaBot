import aiosqlite
import logging
import key_manager

DB_NAME = "sentinel.db"

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        # User Wallet Table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS wallets (
                user_id INTEGER PRIMARY KEY,
                encrypted_private_key TEXT,
                public_key TEXT
            )
        """)
        
        # Active Trades
        await db.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                token_address TEXT,
                amount_sol REAL,
                entry_price REAL,
                token_amount REAL,
                status TEXT DEFAULT 'OPEN',
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Settings Table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                user_id INTEGER PRIMARY KEY,
                slippage REAL DEFAULT 1.0,
                auto_buy BOOLEAN DEFAULT 0,
                auto_sell BOOLEAN DEFAULT 1, 
                simulation_mode BOOLEAN DEFAULT 1,
                take_profit REAL DEFAULT 30.0,
                stop_loss REAL DEFAULT 15.0
            )
        """)
        
        # Migrations (Fixed Syntax)
        try: 
            await db.execute("ALTER TABLE settings ADD COLUMN take_profit REAL DEFAULT 30.0")
        except Exception: pass

        try: 
            await db.execute("ALTER TABLE settings ADD COLUMN stop_loss REAL DEFAULT 15.0")
        except Exception: pass

        try: 
            await db.execute("ALTER TABLE settings ADD COLUMN auto_sell BOOLEAN DEFAULT 1")
        except Exception: pass

        await db.commit()

# --- SETTINGS OPS ---
async def get_settings(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM settings WHERE user_id = ?", (user_id,)) as cursor:
            res = await cursor.fetchone()
            if not res:
                await db.execute("""
                    INSERT INTO settings (user_id, slippage, auto_buy, auto_sell, simulation_mode, take_profit, stop_loss) 
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (user_id, 1.0, 0, 1, 1, 30.0, 15.0))
                await db.commit()
                async with db.execute("SELECT * FROM settings WHERE user_id = ?", (user_id,)) as cursor2:
                    return await cursor2.fetchone()
            return res

async def update_setting(user_id, column, value):
    async with aiosqlite.connect(DB_NAME) as db:
        allowed = ["slippage", "auto_buy", "auto_sell", "simulation_mode", "take_profit", "stop_loss"]
        if column not in allowed: return
        await db.execute(f"UPDATE settings SET {column} = ? WHERE user_id = ?", (value, user_id))
        await db.commit()

# --- WALLET OPS ---
async def get_wallet(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT * FROM wallets WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                try:
                    decrypted_pk = key_manager.decrypt_key(row[1])
                    return (row[0], decrypted_pk, row[2])
                except Exception:
                    return None 
            return None

async def add_wallet(user_id, priv, pub):
    encrypted_pk = key_manager.encrypt_key(priv)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR REPLACE INTO wallets (user_id, encrypted_private_key, public_key) VALUES (?, ?, ?)", 
                         (user_id, encrypted_pk, pub))
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
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM trades WHERE status = 'OPEN'") as cursor:
            return await cursor.fetchall()

async def close_trade(trade_id):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE trades SET status = 'CLOSED' WHERE id = ?", (trade_id,))
        await db.commit()