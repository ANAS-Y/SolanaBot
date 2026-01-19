import os
from dotenv import load_dotenv

load_dotenv()

# Secrets
BOT_TOKEN = os.getenv("8529158400:AAGXihFeJ7imqju-c2Q_cYxiRmu0PP_GYsI")
GEMINI_API_KEY = os.getenv("AIzaSyBIjr13PFLDJWHO3dsmJtQLXbGu9zr60_I") # Get this from aistudio.google.com
DATABASE_URL = os.getenv("postgresql://neondb_owner:npg_Qoz2bkRrUL7j@ep-fancy-brook-ahsds3be-pooler.c-3.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require")

# APIs
RUGCHECK_API = "https://api.rugcheck.xyz/v1/tokens/{}/report"
DEXSCREENER_API = "https://api.dexscreener.com/latest/dex/tokens/{}"

# Trading Settings
SIMULATION_MODE = True  # Set to False to enable Real Trading
AUTO_SELL_TP = 30.0     # +30% Take Profit
AUTO_SELL_SL = -15.0    # -15% Stop Loss