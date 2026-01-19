import httpx
import logging
import json
import config

# --- CONFIGURATION ---
# We use 'gemini-pro' because it is the STABLE model for the v1beta endpoint.
# This fixes the 404 error you are seeing.
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={}"

async def analyze_token(ca, safety_status, market_data):
    """
    Sends data to Gemini Pro via Direct REST API.
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
        
        # We use a 30-second timeout to give the AI enough time to think
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=30.0)
            
            if resp.status_code != 200:
                logging.error(f"Gemini API Error {resp.status_code}: {resp.text}")
                return "WAIT", f"Google API Error: {resp.status_code}"

            data = resp.json()
            
            # Robust Parsing Logic
            try:
                if "candidates" in data and data["candidates"]:
                    content = data["candidates"][0].get("content", {})
                    parts = content.get("parts", [])
                    if parts:
                        text = parts[0].get("text", "").strip()
                    else:
                        return "WAIT", "AI returned empty text."
                else:
                    return "WAIT", "AI returned no candidates."
            except Exception as e:
                logging.error(f"Parsing Error: {e} | Data: {str(data)[:200]}")
                return "WAIT", "AI Response Parse Error."

            # Parse Decision (Case Insensitive)
            upper_text = text.upper()
            if upper_text.startswith("BUY"): return "BUY", text[3:].strip("- :")
            if upper_text.startswith("AVOID"): return "AVOID", text[5:].strip("- :")
            if upper_text.startswith("WAIT"): return "WAIT", text[4:].strip("- :")
            
            # Fallback
            return "WAIT", text[:100]

    except Exception as e:
        logging.error(f"Gemini Connection Error: {e}")
        return "WAIT", "AI Unreachable"