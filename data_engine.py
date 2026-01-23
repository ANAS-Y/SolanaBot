import aiohttp
import logging
import asyncio

# APIs
RUGCHECK_API = "https://api.rugcheck.xyz/v1/tokens/{}/report"
DEX_API = "https://api.dexscreener.com/latest/dex/tokens/{}"
JUP_PRICE_API = "https://api.jup.ag/price/v2?ids=So11111111111111111111111111111111111111112"
CG_PRICE_API = "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd"

# Global Cache to prevent flickering $0
LAST_KNOWN_PRICE = 150.0 

async def get_sol_price():
    """
    Fetches current SOL price with multiple fallbacks.
    Never returns 0.0 or None.
    """
    global LAST_KNOWN_PRICE
    
    # Try 1: Jupiter
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(JUP_PRICE_API, timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    price = float(data['data']['So11111111111111111111111111111111111111112']['price'])
                    LAST_KNOWN_PRICE = price
                    return price
    except:
        pass
    
    # Try 2: CoinGecko
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(CG_PRICE_API, timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    price = float(data['solana']['usd'])
                    LAST_KNOWN_PRICE = price
                    return price
    except:
        pass
        
    # Final Fallback: Return last known good price
    return LAST_KNOWN_PRICE

async def get_market_data(ca):
    """Fetches Token Market Data with DNS Safety"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(DEX_API.format(ca), timeout=8) as resp:
                if resp.status != 200: return None
                data = await resp.json()
                if not data.get("pairs"): return None
                pair = data["pairs"][0]
                
                base = pair.get("baseToken", {})
                txns = pair.get("txns", {}).get("m5", {})

                return {
                    "priceUsd": float(pair.get("priceUsd", 0)),
                    "liquidity": pair.get("liquidity", {}).get("usd", 0),
                    "volume_5m": pair.get("volume", {}).get("m5", 0),
                    "fdv": pair.get("fdv", 0),
                    "name": base.get("name", "Unknown"),
                    "symbol": base.get("symbol", "UNK"),
                    "pairAddress": pair.get("pairAddress"),
                    "txns_5m_buys": txns.get("buys", 0),
                    "txns_5m_sells": txns.get("sells", 0)
                }
    except Exception as e:
        logging.error(f"Market Data Error: {e}")
        return None

async def get_rugcheck_report(ca):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(RUGCHECK_API.format(ca), timeout=8) as resp:
                if resp.status != 200: return "UNKNOWN", "⚠️ Check Failed", 0, 0
                
                data = await resp.json()
                score = data.get("score", 0)
                risks = data.get("risks", [])
                
                risk_level = "SAFE"
                if score > 2000: risk_level = "DANGER"
                elif score > 500: risk_level = "WARNING"
                
                top_holders = data.get("topHolders", [])
                total_pct = sum(float(h.get("pct", 0)) for h in top_holders[:10])
                
                details = f"Risk Score: {score}\n"
                if risks:
                    details += "<b>Risks Found:</b>\n"
                    for r in risks[:2]:
                        details += f"- {r.get('name')}\n"
                details += f"<b>Top 10 Holders:</b> {total_pct:.1f}%"
                
                return risk_level, details, score, total_pct
    except:
        return "UNKNOWN", "⚠️ Check Failed", 0, 0