import httpx
import logging
import json
import asyncio
import config

# Endpoints
MODELS_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models?key={}"
GENERATE_BASE = "https://generativelanguage.googleapis.com/v1beta/{}:generateContent?key={}"

# Cache the working model
CACHED_MODEL_NAME = None

async def get_best_model():
    """
    Finds the best model, prioritizing High-Rate-Limit models (Flash).
    """
    global CACHED_MODEL_NAME
    if CACHED_MODEL_NAME: return CACHED_MODEL_NAME

    try:
        url = MODELS_ENDPOINT.format(config.GEMINI_API_KEY)
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10)
            
            if resp.status_code == 200:
                data = resp.json()
                candidates = []
                for m in data.get('models', []):
                    if "generateContent" in m.get("supportedGenerationMethods", []):
                        candidates.append(m['name'])
                
                # PRIORITY 1: Gemini 1.5 Flash (Fastest, Best Rate Limits)
                for name in candidates:
                    if "gemini-1.5-flash" in name and "exp" not in name:
                        logging.info(f"🧠 Selected High-Speed Model: {name}")
                        CACHED_MODEL_NAME = name
                        return name
                
                # PRIORITY 2: Gemini 1.5 Pro
                for name in candidates:
                    if "gemini-1.5-pro" in name:
                        CACHED_MODEL_NAME = name
                        return name

                # Fallback
                if candidates:
                    CACHED_MODEL_NAME = candidates[0]
                    return candidates[0]

    except Exception as e:
        logging.error(f"Model Discovery Failed: {e}")
    
    # Absolute Fallback (This usually works on standard keys)
    return "models/gemini-1.5-flash"

async def analyze_token(ca, safety_status, market_data):
    """
    Analyzes token with Automatic Retries for 429 Errors.
    """
    if not config.GEMINI_API_KEY:
        return "WAIT", "⚠️ Gemini API Key missing."

    # 1. HARD RULES
    if safety_status == "UNSAFE": return "AVOID", "⛔ RugCheck failed."
    if market_data['liquidity'] < 5000: return "AVOID", "💧 Liquidity too low (<$5k)."

    # 2. PREPARE DATA
    prompt_text = f"""
    Act as a crypto scalper. Analyze this Solana token:
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

    Output a single sentence starting with BUY, WAIT, or AVOID.
    """

    payload = {"contents": [{"parts": [{"text": prompt_text}]}]}

    # 3. SEND REQUEST WITH RETRY LOGIC (The Fix)
    model_name = await get_best_model()
    url = GENERATE_BASE.format(model_name, config.GEMINI_API_KEY)

    async with httpx.AsyncClient() as client:
        # Try up to 3 times
        for attempt in range(1, 4):
            try:
                resp = await client.post(url, json=payload, timeout=30.0)
                
                # SUCCESS
                if resp.status_code == 200:
                    data = resp.json()
                    try:
                        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                        # Parse
                        upper = text.upper()
                        if upper.startswith("BUY"): return "BUY", text[3:].strip("- :")
                        if upper.startswith("AVOID"): return "AVOID", text[5:].strip("- :")
                        return "WAIT", text[:100]
                    except:
                        return "WAIT", "AI Parsing Error"

                # HANDLING 429 (RATE LIMIT)
                elif resp.status_code == 429:
                    wait_time = 2 ** attempt # 2s, 4s, 8s
                    logging.warning(f"⚠️ AI Rate Limit (429). Retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                    continue # Try again
                
                # OTHER ERRORS
                else:
                    logging.error(f"Gemini API Error {resp.status_code}: {resp.text}")
                    return "WAIT", f"AI Error: {resp.status_code}"

            except Exception as e:
                logging.error(f"Connection Error: {e}")
                await asyncio.sleep(1)

    return "WAIT", "⚠️ AI Busy (Rate Limited)"