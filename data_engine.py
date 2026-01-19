import httpx
import logging
import config

async def get_rugcheck_report(ca: str):
    """
    Step A: Queries RugCheck.xyz to detect scams/rugs.
    Returns: 'SAFE' or 'UNSAFE' along with a reason.
    """
    url = config.RUGCHECK_API.format(ca)
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10)
            
            if resp.status_code != 200:
                return "UNSAFE", f"API Error {resp.status_code}"

            data = resp.json()
            score = data.get("score", 5000) # Default to high risk if missing
            risks = data.get("risks", [])

            # 1. Critical Rule: Mint Authority
            # If Mint Authority is still enabled, the dev can print infinite tokens.
            for risk in risks:
                if risk.get("name") == "Mint Authority" and risk.get("level") == "danger":
                    return "UNSAFE", "❌ Danger: Mint Authority is Enabled!"

            # 2. Score Rule
            # RugCheck scores: 0 (Good) to ~10000 (Bad). 
            # We use a strict threshold of 2000.
            if score > 2000:
                return "UNSAFE", f"❌ Risk Score too high: {score}"

            return "SAFE", f"✅ Score: {score} (Clean)"

    except Exception as e:
        logging.error(f"RugCheck Error: {e}")
        # FAILSAFE: If security check fails, assume UNSAFE to protect funds.
        return "UNSAFE", "⚠️ Security Check Failed (Network Error)"

async def get_market_data(ca: str):
    """
    Step B: Fetches live price, liquidity, and volume from DexScreener.
    """
    url = config.DEXSCREENER_API.format(ca)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if not data.get("pairs"): return None
                
                # Get the main pair (usually the first one)
                pair = data["pairs"][0]
                return {
                    "priceUsd": float(pair.get("priceUsd", 0)),
                    "liquidity": pair.get("liquidity", {}).get("usd", 0),
                    "volume_5m": pair.get("volume", {}).get("m5", 0),
                    "volume_1h": pair.get("volume", {}).get("h1", 0),
                    "txns_5m_buys": pair.get("txns", {}).get("m5", {}).get("buys", 0),
                    "txns_5m_sells": pair.get("txns", {}).get("m5", {}).get("sells", 0),
                    "fdv": pair.get("fdv", 0)
                }
    except Exception as e:
        logging.error(f"DexScreener Error: {e}")
        return None