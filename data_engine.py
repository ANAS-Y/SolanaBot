import aiohttp
import logging

# RugCheck Public API
RUGCHECK_API = "https://api.rugcheck.xyz/v1/tokens/{}/report"
# DexScreener API
DEX_API = "https://api.dexscreener.com/latest/dex/tokens/{}"

async def get_market_data(ca):
    """
    Fetches Price, Liquidity, Volume, and Transaction Counts from DexScreener.
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(DEX_API.format(ca)) as resp:
                if resp.status != 200: return None
                data = await resp.json()
                
                if not data.get("pairs"): return None
                pair = data["pairs"][0]
                
                # Extract Transaction Counts (The missing piece)
                txns = pair.get("txns", {})
                m5_txns = txns.get("m5", {})
                
                return {
                    "priceUsd": float(pair.get("priceUsd", 0)),
                    "liquidity": pair.get("liquidity", {}).get("usd", 0),
                    "volume_5m": pair.get("volume", {}).get("m5", 0),
                    "fdv": pair.get("fdv", 0),
                    "pairAddress": pair.get("pairAddress"),
                    # These were missing and causing the KeyError:
                    "txns_5m_buys": m5_txns.get("buys", 0),
                    "txns_5m_sells": m5_txns.get("sells", 0)
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
                
                score = data.get("score", 0)
                risks = data.get("risks", [])
                
                # Verdict Logic
                risk_level = "SAFE"
                if score > 2000: risk_level = "DANGER"
                elif score > 500: risk_level = "WARNING"
                
                # Holder Distribution
                top_holders = data.get("topHolders", [])
                total_pct = sum(float(h.get("pct", 0)) for h in top_holders[:10])
                
                # Detailed Report
                details = f"Risk Score: {score}\n"
                if risks:
                    details += "⚠️ **Risks:**\n"
                    for r in risks[:3]:
                        details += f"- {r.get('name')}\n"
                
                details += f"👥 **Top 10 Holders:** {total_pct:.1f}%"
                
                return risk_level, details, score, total_pct

    except Exception as e:
        logging.error(f"RugCheck Error: {e}")
        return "UNKNOWN", "⚠️ Check Failed", 0, 0