import httpx
import logging
import json
import config

# We use the direct REST API endpoint for Gemini 1.5 Flash (Fast & Free)
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={}"

async def analyze_token(ca, safety_status, market_data):
    """
    Sends data to Gemini AI via Direct REST API (Bypassing the broken SDK).
    """
    if not config.GEMINI_API_KEY:
        return "WAIT", "⚠️ Gemini API Key missing."

    # 1. HARD RULES (Pre-Filter)
    if safety_status == "UNSAFE":
        return "AVOID", "⛔ RugCheck failed."
    
    if market_data['liquidity'] < 5000:
        return "AVOID", "💧 Liquidity too low (<$5k)."

    # 2. PREPARE DATA
    prompt_text = f"""
    You are Sentinel AI, a crypto scalper.
    Analyze this Solana token:
    - Contract: {ca}
    - Safety: {safety_status}
    - Liquidity: ${market_data['liquidity']:,.2f}
    - Volume (5m): ${market_data['volume_5m']:,.2f}
    - Buys/Sells (5m): {market_data['txns_5m_buys']}/{market_data['txns_5m_sells']}
    - FDV: ${market_data['fdv']:,.2f}

    RULES:
    - UNSAFE Safety -> AVOID.
    - Vol (5m) < $500 -> WAIT.
    - Sells > Buys (2x) -> WAIT.
    - High Vol + More Buys -> BUY.

    OUTPUT FORMAT:
    Return a single sentence starting with BUY, WAIT, or AVOID, followed by the reason.
    Example: "BUY because volume is high and momentum is positive."
    """

    payload = {
        "contents": [{
            "parts": [{"text": prompt_text}]
        }]
    }

    # 3. SEND DIRECT REQUEST
    try:
        url = GEMINI_URL.format(config.GEMINI_API_KEY)
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=10)
            
            if resp.status_code != 200:
                logging.error(f"Gemini API Error {resp.status_code}: {resp.text}")
                return "WAIT", f"Google API Error: {resp.status_code}"

            data = resp.json()
            # Extract text from complex JSON response
            try:
                text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            except (KeyError, IndexError):
                return "WAIT", "AI returned empty response."

            # Parse Decision
            if text.startswith("BUY"): return "BUY", text[3:].strip("- :")
            if text.startswith("AVOID"): return "AVOID", text[5:].strip("- :")
            if text.startswith("WAIT"): return "WAIT", text[4:].strip("- :")
            
            return "WAIT", text

    except Exception as e:
        logging.error(f"Gemini Connection Error: {e}")
        return "WAIT", "AI Unreachable"