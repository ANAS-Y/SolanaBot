import aiohttp
import logging

# RugCheck Public API (No key needed for basic read, but rate-limited)
RUGCHECK_API = "https://api.rugcheck.xyz/v1/tokens/{}/report"
DEX_API = "https://api.dexscreener.com/latest/dex/tokens/{}"

async def get_market_data(ca):
    """Fetches Price, Liquidity, and Volume from DexScreener"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(DEX_API.format(ca)) as resp:
                if resp.status != 200: return None
                data = await resp.json()
                
                if not data.get("pairs"): return None
                pair = data["pairs"][0]
                
                return {
                    "priceUsd": float(pair.get("priceUsd", 0)),
                    "liquidity": pair.get("liquidity", {}).get("usd", 0),
                    "volume_5m": pair.get("volume", {}).get("m5", 0),
                    "fdv": pair.get("fdv", 0),
                    "pairAddress": pair.get("pairAddress")
                }
    except Exception as e:
        logging.error(f"Market Data Error: {e}")
        return None

async def get_rugcheck_report(ca):
    """
    Fetches Security Report from RugCheck.xyz
    Returns: (Verdict, Details_String, Risk_Score, Top10_Holders_Pct)
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(RUGCHECK_API.format(ca)) as resp:
                if resp.status != 200:
                    return "UNKNOWN", "⚠️ Security Check Failed (API Error)", 0, 0
                
                data = await resp.json()
                
                # Extract Risk Score (0-10000 usually)
                score = data.get("score", 0)
                
                # Extract Risks
                risks = data.get("risks", [])
                risk_level = "SAFE"
                risk_msg = "✅ Token looks clean."
                
                # Determine Verdict
                if score > 2000: # RugCheck arbitrary threshold for "Danger"
                    risk_level = "DANGER"
                    risk_msg = "⛔ High Risk Detected!"
                elif score > 500:
                    risk_level = "WARNING"
                    risk_msg = "wm Caution Advised."
                
                # Holder Distribution
                top_holders = data.get("topHolders", [])
                total_pct = sum(float(h.get("pct", 0)) for h in top_holders[:10])
                
                # Create Report String
                details = f"Risk Score: {score}\n"
                if risks:
                    details += "⚠️ **Risks:**\n"
                    for r in risks[:3]: # Show top 3 risks
                        details += f"- {r.get('name')}: {r.get('description')}\n"
                
                details += f"👥 **Top 10 Holders:** {total_pct:.1f}% Supply"
                
                return risk_level, details, score, total_pct

    except Exception as e:
        logging.error(f"RugCheck Error: {e}")
        return "UNKNOWN", "⚠️ Check Failed", 0, 0