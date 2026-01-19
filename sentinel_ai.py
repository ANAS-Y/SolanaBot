import httpx
import logging
import json
import config

# Endpoint to discover available models
MODELS_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models?key={}"
GENERATE_BASE = "https://generativelanguage.googleapis.com/v1beta/{}:generateContent?key={}"

CACHED_MODEL_NAME = None

async def get_best_model():
    """
    Dynamically asks Google for the best available model.
    """
    global CACHED_MODEL_NAME
    if CACHED_MODEL_NAME: return CACHED_MODEL_NAME

    try:
        url = MODELS_ENDPOINT.format(config.GEMINI_API_KEY)
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10)
            
            # Catch Leaked/Invalid Key Errors during model discovery
            if resp.status_code == 403:
                logging.error("❌ GEMINI KEY REVOKED (Leaked/Invalid)")
                return None
            
            if resp.status_code != 200:
                logging.error(f"Failed to list models: {resp.status_code}")
                return "models/gemini-1.5-flash"

            data = resp.json()
            candidates = []
            for m in data.get('models', []):
                if "generateContent" in m.get("supportedGenerationMethods", []):
                    candidates.append(m['name'])

            best_pick = None
            for name in candidates:
                if "gemini-2.0" in name: best_pick = name; break
                if "gemini-1.5-flash" in name: best_pick = name; break
            
            if not best_pick and candidates: best_pick = candidates[0]

            if best_pick:
                logging.info(f"🧠 Sentinel AI selected model: {best_pick}")
                CACHED_MODEL_NAME = best_pick
                return best_pick
            
    except Exception as e:
        logging.error(f"Model Discovery Error: {e}")
    
    return "models/gemini-1.5-flash"

async def analyze_token(ca, safety_status, market_data):
    """
    Analyzes token using the dynamically resolved model.
    """
    if not config.GEMINI_API_KEY:
        return "WAIT", "⚠️ Gemini API Key missing."

    # 1. HARD RULES
    if safety_status == "UNSAFE": return "AVOID", "⛔ RugCheck failed."
    if market_data['liquidity'] < 5000: return "AVOID", "💧 Liquidity too low (<$5k)."

    # 2. PROMPT
    prompt_text = f"""
    You are Sentinel AI. Analyze this Solana token.
    Contract: {ca}
    Safety: {safety_status}
    Liquidity: ${market_data['liquidity']:,.2f}
    Volume (5m): ${market_data['volume_5m']:,.2f}
    Buys/Sells: {market_data['txns_5m_buys']}/{market_data['txns_5m_sells']}
    FDV: ${market_data['fdv']:,.2f}

    RULES:
    - UNSAFE Safety -> AVOID.
    - Vol (5m) < $500 -> WAIT.
    - Sells > Buys (2x) -> WAIT.
    - High Vol + More Buys -> BUY.

    Output a single sentence starting with BUY, WAIT, or AVOID.
    """

    payload = {"contents": [{"parts": [{"text": prompt_text}]}]}

    # 3. SEND REQUEST
    try:
        model_name = await get_best_model()
        if not model_name:
            return "WAIT", "⚠️ Critical: API Key Blocked by Google."

        url = GENERATE_BASE.format(model_name, config.GEMINI_API_KEY)
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=30.0)
            
            # HANDLE BLOCKED KEYS GRACEFULLY
            if resp.status_code == 403:
                return "WAIT", "⚠️ API Key Blocked (Create new one in Google AI Studio)."
            
            if resp.status_code != 200:
                if resp.status_code == 404: 
                    global CACHED_MODEL_NAME
                    CACHED_MODEL_NAME = None 
                return "WAIT", f"AI Error: {resp.status_code}"

            data = resp.json()
            try:
                text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            except:
                return "WAIT", "AI Parsing Error."

            upper = text.upper()
            if upper.startswith("BUY"): return "BUY", text[3:].strip("- :")
            if upper.startswith("AVOID"): return "AVOID", text[5:].strip("- :")
            if upper.startswith("WAIT"): return "WAIT", text[4:].strip("- :")
            return "WAIT", text[:100]

    except Exception as e:
        logging.error(f"Gemini Error: {e}")
        return "WAIT", "AI Unreachable"