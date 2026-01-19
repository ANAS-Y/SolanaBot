import httpx
import base64
import logging
import asyncio
import json
from solders.transaction import VersionedTransaction
from solders.message import to_bytes_versioned
from solana.rpc.async_api import AsyncClient
from solana.rpc.types import TxOpts
from solders.pubkey import Pubkey

# --- CONFIGURATION ---
JUP_QUOTE_URL = "https://quote-api.jup.ag/v6/quote"
JUP_SWAP_URL = "https://quote-api.jup.ag/v6/swap"
JUP_PRICE_URL = "https://api.jup.ag/price/v2"

SOL_MINT = "So11111111111111111111111111111111111111112"

# --- NETWORK ENGINE (HTTPX) ---
# We use a single client for connection pooling
# verify=False bypasses the SSL/DNS mismatches on Render
CLIENT = httpx.AsyncClient(verify=False, timeout=10.0, follow_redirects=True)

async def retry_request(url, method="GET", payload=None):
    """
    Uses HTTPX with SSL verification DISABLED.
    This bypasses the handshake failures and DNS strictness.
    """
    for attempt in range(3):
        try:
            if method == "GET":
                resp = await CLIENT.get(url)
            elif method == "POST":
                resp = await CLIENT.post(url, json=payload)
            
            # Check for success
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                # Rate limit - wait and retry
                await asyncio.sleep(2)
            else:
                logging.warning(f"⚠️ HTTP {resp.status_code} from {url}: {resp.text[:100]}")
                
        except Exception as e:
            logging.warning(f"⚠️ Network Attempt {attempt+1} failed: {str(e)}")
            await asyncio.sleep(1)
            
    return None

# --- BALANCE & TRADING ---
async def get_sol_balance(rpc_url, pubkey_str):
    try:
        # RPC calls use standard library (AsyncClient handles connection)
        async with AsyncClient(rpc_url) as client:
            resp = await client.get_balance(Pubkey.from_string(pubkey_str))
            return resp.value
    except Exception as e:
        logging.error(f"Balance Check Error: {e}")
        return 0

async def get_market_cap(mint, rpc_url):
    try:
        price_data = await retry_request(f"{JUP_PRICE_URL}?ids={mint}")
        if not price_data or 'data' not in price_data: return None
        price = float(price_data['data'][mint]['price'])

        async with AsyncClient(rpc_url) as client:
            supply = await client.get_token_supply(Pubkey.from_string(mint))
            return price * float(supply.value.ui_amount)
    except: return None

async def get_price(mint):
    data = await retry_request(f"{JUP_PRICE_URL}?ids={mint}")
    try: return float(data['data'][mint]['price'])
    except: return None

async def execute_swap(keypair, input_mint, output_mint, amount_lamports, rpc_url, slippage=100):
    # 1. Quote
    q_url = f"{JUP_QUOTE_URL}?inputMint={input_mint}&outputMint={output_mint}&amount={int(amount_lamports)}&slippageBps={slippage}"
    quote = await retry_request(q_url)
    
    if not quote: return "Quote Error: Connection Failed (Check Logs)"
    if "error" in quote: return f"Quote Error: {quote['error']}"

    # 2. Swap
    payload = {
        "quoteResponse": quote,
        "userPublicKey": str(keypair.pubkey()),
        "wrapAndUnwrapSol": True,
        "prioritizationFeeLamports": 10000 
    }
    swap_resp = await retry_request(JUP_SWAP_URL, method="POST", payload=payload)
    
    if not swap_resp: return "Swap Error: No Response"
    if "swapTransaction" not in swap_resp: return f"Swap Error: {swap_resp}"

    # 3. Sign
    try:
        raw_tx = base64.b64decode(swap_resp['swapTransaction'])
        tx = VersionedTransaction.from_bytes(raw_tx)
        msg = to_bytes_versioned(tx.message)
        sig = keypair.sign_message(msg)
        signed_tx = VersionedTransaction.populate(tx.message, [sig])

        async with AsyncClient(rpc_url) as client:
            resp = await client.send_raw_transaction(bytes(signed_tx), opts=TxOpts(skip_preflight=True))
            return str(resp.value)
    except Exception as e: return f"Execution Error: {str(e)}"