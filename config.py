import os
import sys
from dotenv import load_dotenv

load_dotenv()

# --- SECRETS & KEYS ---
BOT_TOKEN = os.getenv("8529158400:AAGXihFeJ7imqju-c2Q_cYxiRmu0PP_GYsI")
GEMINI_API_KEY = os.getenv("AIzaSyBIjr13PFLDJWHO3dsmJtQLXbGu9zr60_I") # Get this from aistudio.google.com
DATABASE_URL = os.getenv("postgresql://neondb_owner:npg_Qoz2bkRrUL7j@ep-fancy-brook-ahsds3be-pooler.c-3.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require")


# --- VALIDATION ---
missing = []
if not BOT_TOKEN: missing.append("8529158400:AAGXihFeJ7imqju-c2Q_cYxiRmu0PP_GYsI")
if not GEMINI_API_KEY: missing.append("AIzaSyBIjr13PFLDJWHO3dsmJtQLXbGu9zr60_I")
if not DATABASE_URL: missing.append("postgresql://neondb_owner:npg_Qoz2bkRrUL7j@ep-fancy-brook-ahsds3be-pooler.c-3.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require")

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