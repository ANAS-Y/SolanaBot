import google.generativeai as genai
import config
import logging

# Configure Gemini
if config.GEMINI_API_KEY:
    genai.configure(api_key=config.GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-pro')
else:
    model = None

async def analyze_token(ca, safety_status, market_data):
    """
    Sends data to Gemini AI for a trading decision.
    """
    if not model:
        return "WAIT", "⚠️ Gemini API Key missing."

    # 1. HARD RULES (Pre-Filter)
    # Even AI can hallucinate, so we enforce hard constraints first.
    if safety_status == "UNSAFE":
        return "AVOID", "⛔ RugCheck failed."
    
    if market_data['liquidity'] < 5000:
        return "AVOID", "💧 Liquidity too low (<$5k)."

    # 2. AI PROMPT CONSTRUCTION
    prompt = f"""
    You are Sentinel AI, a conservative cryptocurrency scalper specialized in Solana.
    
    TOKEN DATA:
    - Contract: {ca}
    - Safety Verdict: {safety_status}
    - Liquidity: ${market_data['liquidity']:,.2f}
    - Volume (5m): ${market_data['volume_5m']:,.2f}
    - Volume (1h): ${market_data['volume_1h']:,.2f}
    - Buys (5m): {market_data['txns_5m_buys']}
    - Sells (5m): {market_data['txns_5m_sells']}
    - Market Cap (FDV): ${market_data['fdv']:,.2f}

    DECISION RULES:
    1. If Safety is UNSAFE, decision MUST be 'AVOID'.
    2. If Volume (5m) is under $500, return 'WAIT' (Not enough activity).
    3. If Sells > Buys by 2x, return 'WAIT' (Selling pressure).
    4. If Buys > Sells AND Volume is high AND Price is trending up, return 'BUY'.

    TASK:
    Analyze the data. Return a JSON string with exactly two fields:
    1. "decision": One of ["BUY", "WAIT", "AVOID"]
    2. "reason": A short explanation (max 1 sentence).
    """

    try:
        # Run in executor to avoid blocking async loop
        response = await model.generate_content_async(prompt)
        text = response.text.strip()
        
        # Simple parsing (Gemini usually returns clean text if instructed well)
        if "BUY" in text: return "BUY", text
        if "AVOID" in text: return "AVOID", text
        return "WAIT", text

    except Exception as e:
        logging.error(f"Gemini Error: {e}")
        return "WAIT", "AI Unreachable"