import os
import sys
from dotenv import load_dotenv

load_dotenv()

# --- SECRETS & KEYS ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")

# --- VALIDATION ---
missing = []
if not BOT_TOKEN: missing.append("BOT_TOKEN")
if not GEMINI_API_KEY: missing.append("GEMINI_API_KEY")
if not DATABASE_URL: missing.append("DATABASE_URL")

if missing:
    print("------------------------------------------------------")
    print("❌ CRITICAL ERROR: MISSING ENVIRONMENT VARIABLES")
    print(f"   Please add the following keys to Render Environment: {', '.join(missing)}")
    print("------------------------------------------------------")
    # Stop execution immediately so logs show the error clearly
    sys.exit(1)

# --- APIs ---
RUGCHECK_API = "https://api.rugcheck.xyz/v1/tokens/{}/report"
DEXSCREENER_API = "https://api.dexscreener.com/latest/dex/tokens/{}"
RPC_URL = os.getenv("RPC_URL", "https://api.mainnet-beta.solana.com")

# --- TRADING SETTINGS ---
SIMULATION_MODE = True  # Set False for real money
AUTO_SELL_TP = 30.0     # +30% Take Profit
AUTO_SELL_SL = -15.0    # -15% Stop Loss