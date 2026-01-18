import aiohttp
import base64
import logging
import socket
import asyncio
from solders.transaction import VersionedTransaction
from solders.message import to_bytes_versioned
from solana.rpc.async_api import AsyncClient
from solana.rpc.types import TxOpts
from solders.pubkey import Pubkey

# --- CONFIGURATION ---
# We use the direct IP for Cloudflare (Jupiter's host) to bypass DNS failures
JUP_API_IP = "172.67.163.67" 
JUP_HOST = "quote-api.jup.ag"
JUP_PRICE_HOST = "api.jup.ag"

SOL_MINT = "So11111111111111111111111111111111111111112"

# --- NETWORK HARDENING ---
class DirectResolver(aiohttp.resolver.AbstractResolver):
    """Forces aiohttp to use a specific IP for a specific hostname."""
    async def resolve(self, host, port=0, family=socket.AF_INET):
        if host == JUP_HOST or host == JUP_PRICE_HOST:
            return [{'hostname': host, 'host': JUP_API_IP, 'port': port,
                     'family': family, 'proto': 0, 'flags': 0}]
        # For everything else (like Telegram), use standard DNS
        return await aiohttp.resolver.ThreadedResolver().resolve(host, port, family)
    
    async def close(self): pass

def get_conn():
    # Use our custom resolver that hardcodes the IP
    connector = aiohttp.TCPConnector(resolver=DirectResolver(), ssl=False)
    return connector

async def retry_request(url, method="GET", payload=None):
    """Retries a network request 3 times with a hardcoded IP resolver."""
    for attempt in range(3):
        try:
            # We must pass the 'Host' header manually so Cloudflare knows who we want
            headers = {"Host": JUP_HOST if "quote-api" in url else JUP_PRICE_HOST}
            
            # Rewrite URL to use IP address directly if needed, 
            # OR rely on DirectResolver. Let's rely on DirectResolver for cleaner SSL handling.
            
            async with aiohttp.ClientSession(connector=get_conn()) as session:
                if method == "GET":
                    async with session.get(url, headers=headers) as resp:
                        if resp.status != 200:
                            logging.warning(f"⚠️ HTTP {resp.status} from {url}")
                            # If 403/429, wait longer
                            if resp.status in [403, 429]: await asyncio.sleep(2)
                            continue
                        return await resp.json()
                elif method == "POST":
                    async with session.post(url, json=payload, headers=headers) as resp:
                        return await resp.json()
        except Exception as e:
            logging.warning(f"⚠️ Network Attempt {attempt+1} failed: {e}")
            await asyncio.sleep(1)
    return None

async def get_sol_balance(rpc_url, pubkey_str):
    try:
        async with AsyncClient(rpc_url) as client:
            resp = await client.get_balance(Pubkey.from_string(pubkey_str))
            return resp.value
    except Exception as e:
        logging.error(f"Balance Check Error: {e}")
        return 0

async def get_market_cap(mint, rpc_url):
    try:
        # 1. Get Price
        price_data = await retry_request(f"https://{JUP_PRICE_HOST}/price/v2?ids={mint}")
        if not price_data or 'data' not in price_data or mint not in price_data['data']:
            return None
        price = float(price_data['data'][mint]['price'])

        # 2. Get Supply
        async with AsyncClient(rpc_url) as client:
            supply_resp = await client.get_token_supply(Pubkey.from_string(mint))
            if not supply_resp.value: return None
            supply = float(supply_resp.value.ui_amount)

        return price * supply
    except Exception as e:
        logging.error(f"MC Check Error: {e}")
        return None

async def get_price(mint):
    url = f"https://{JUP_PRICE_HOST}/price/v2?ids={mint}"
    data = await retry_request(url)
    try:
        return float(data['data'][mint]['price'])
    except:
        return None

async def execute_swap(keypair, input_mint, output_mint, amount_lamports, rpc_url, slippage=100):
    # 1. Get Quote
    q_url = f"https://{JUP_HOST}/v6/quote?inputMint={input_mint}&outputMint={output_mint}&amount={int(amount_lamports)}&slippageBps={slippage}"
    quote = await retry_request(q_url)
    
    if not quote or "error" in quote: 
        return f"Quote Error: {quote.get('error', 'No Response from Jupiter') if quote else 'No Response'}"

    # 2. Get Swap Transaction
    payload = {
        "quoteResponse": quote,
        "userPublicKey": str(keypair.pubkey()),
        "wrapAndUnwrapSol": True,
        "prioritizationFeeLamports": 10000 
    }
    swap_resp = await retry_request(f"https://{JUP_HOST}/v6/swap", method="POST", payload=payload)
    
    if not swap_resp or "swapTransaction" not in swap_resp: 
        return f"Swap Error: {swap_resp}"

    # 3. Sign & Send
    try:
        raw_tx = base64.b64decode(swap_resp['swapTransaction'])
        tx = VersionedTransaction.from_bytes(raw_tx)
        message = to_bytes_versioned(tx.message)
        signature = keypair.sign_message(message)
        signed_tx = VersionedTransaction.populate(tx.message, [signature])

        async with AsyncClient(rpc_url) as client:
            opts = TxOpts(skip_preflight=True)
            resp = await client.send_raw_transaction(bytes(signed_tx), opts=opts)
            return str(resp.value)
    except Exception as e:
        return f"Execution Error: {str(e)}"