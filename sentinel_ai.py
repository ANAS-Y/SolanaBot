import httpx
import logging
import json
import config

# Endpoint to discover available models
MODELS_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models?key={}"
# Base endpoint for generation (we will append the model name dynamically)
GENERATE_BASE = "https://generativelanguage.googleapis.com/v1beta/{}:generateContent?key={}"

# Cache the working model name so we don't ask every time
CACHED_MODEL_NAME = None

async def get_best_model():
    """
    Dynamically asks Google for the best available model.
    Prioritizes 'flash' (speed) -> 'pro' (quality) -> any valid model.
    """
    global CACHED_MODEL_NAME
    if CACHED_MODEL_NAME: return CACHED_MODEL_NAME

    try:
        url = MODELS_ENDPOINT.format(config.GEMINI_API_KEY)
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10)
            if resp.status_code != 200:
                logging.error(f"Failed to list models: {resp.status_code}")
                return "models/gemini-1.5-flash" # Fallback

            data = resp.json()
            candidates = []

            # Filter for models that support 'generateContent'
            for m in data.get('models', []):
                if "generateContent" in m.get("supportedGenerationMethods", []):
                    candidates.append(m['name'])

            # Selection Logic: Prefer Flash, then Pro, then anything
            best_pick = None
            for name in candidates:
                if "gemini-1.5-flash" in name: 
                    best_pick = name
                    break # Found the gold standard
                if "gemini-1.5-pro" in name and not best_pick:
                    best_pick = name
            
            # If no specific preference found, take the first valid one
            if not best_pick and candidates:
                best_pick = candidates[0]

            if best_pick:
                logging.info(f"🧠 Sentinel AI selected model: {best_pick}")
                CACHED_MODEL_NAME = best_pick
                return best_pick
            
    except Exception as e:
        logging.error(f"Model Discovery Error: {e}")
    
    return "models/gemini-1.5-flash" # Absolute fallback

async def analyze_token(ca, safety_status, market_data):
    """
    Analyzes token using the dynamically resolved model.
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

    # 3. SEND REQUEST (Using Dynamic Model)
    try:
        model_name = await get_best_model()
        url = GENERATE_BASE.format(model_name, config.GEMINI_API_KEY)
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=30.0)
            
            if resp.status_code != 200:
                logging.error(f"Gemini API Error {resp.status_code}: {resp.text}")
                # If 404, force refresh cache next time
                if resp.status_code == 404: 
                    global CACHED_MODEL_NAME
                    CACHED_MODEL_NAME = None 
                return "WAIT", f"AI Error: {resp.status_code}"

            data = resp.json()
            
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
                return "WAIT", "AI Response Parse Error."

            # Parse Decision
            upper_text = text.upper()
            if upper_text.startswith("BUY"): return "BUY", text[3:].strip("- :")
            if upper_text.startswith("AVOID"): return "AVOID", text[5:].strip("- :")
            if upper_text.startswith("WAIT"): return "WAIT", text[4:].strip("- :")
            
            return "WAIT", text[:100]

    except Exception as e:
        logging.error(f"Gemini Connection Error: {e}")
        return "WAIT", "AI Unreachable"