import aiohttp
import base64
import logging
import asyncio
import socket
from solders.transaction import VersionedTransaction
from solders.message import to_bytes_versioned
from solana.rpc.async_api import AsyncClient
from solana.rpc.types import TxOpts
from solders.pubkey import Pubkey

# --- CONFIGURATION ---
JUP_QUOTE_HOST = "quote-api.jup.ag"
JUP_PRICE_HOST = "api.jup.ag"
JUP_QUOTE_URL = f"https://{JUP_QUOTE_HOST}/v6/quote"
JUP_SWAP_URL = f"https://{JUP_QUOTE_HOST}/v6/swap"
JUP_PRICE_URL = f"https://{JUP_PRICE_HOST}/price/v2"

# Cloudflare IPs for Jupiter (Hardcoded to bypass DNS)
HARDCODED_IPS = {
    JUP_QUOTE_HOST: "172.67.163.67",  # Primary Cloudflare IP
    JUP_PRICE_HOST: "104.21.32.127"   # Secondary Cloudflare IP
}

SOL_MINT = "So11111111111111111111111111111111111111112"

# --- NETWORK HARDENING: HARDCODED RESOLVER ---
class HardcodedResolver(aiohttp.resolver.AbstractResolver):
    """
    A resolver that ignores the network and returns hardcoded IPs.
    This fixes DNS failures while preserving SSL SNI.
    """
    async def resolve(self, host, port=0, family=socket.AF_INET):
        # 1. Check if we have a hardcoded IP for this host
        if host in HARDCODED_IPS:
            return [{
                'hostname': host,
                'host': HARDCODED_IPS[host],
                'port': port,
                'family': family,
                'proto': 0,
                'flags': 0
            }]
        
        # 2. Fallback: If it's not Jupiter, try standard resolution (e.g. for RPC)
        return await aiohttp.resolver.ThreadedResolver().resolve(host, port, family)

    async def close(self): pass

def get_conn():
    # We pass the HardcodedResolver to the connector.
    # We set ssl=True (Default) because now we are using the real Hostname, so SSL checks will pass!
    return aiohttp.TCPConnector(resolver=HardcodedResolver())

async def retry_request(url, method="GET", payload=None):
    for attempt in range(3):
        try:
            # We use the standard URL (e.g. https://quote-api.jup.ag)
            # The Magic happens in 'get_conn()' which maps it to the IP internally.
            async with aiohttp.ClientSession(connector=get_conn()) as session:
                if method == "GET":
                    async with session.get(url, timeout=10) as resp:
                        if resp.status == 200: 
                            # Disable strict content-type check just to be safe
                            return await resp.json(content_type=None)
                        elif resp.status == 429: 
                            await asyncio.sleep(2)
                        else:
                            logging.warning(f"⚠️ HTTP {resp.status} from {url}")
                elif method == "POST":
                    async with session.post(url, json=payload, timeout=10) as resp:
                        if resp.status == 200: 
                            return await resp.json(content_type=None)
                        else:
                            logging.error(f"⚠️ POST Error {resp.status}")
        except Exception as e:
            logging.warning(f"⚠️ Attempt {attempt+1} Failed: {e}")
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
    if not quote: return "Quote Error: Connection Failed"
    if "error" in quote: return f"Quote Error: {quote['error']}"

    # 2. Swap
    payload = {
        "quoteResponse": quote,
        "userPublicKey": str(keypair.pubkey()),
        "wrapAndUnwrapSol": True,
        "prioritizationFeeLamports": 10000 
    }
    swap_resp = await retry_request(JUP_SWAP_URL, method="POST", payload=payload)
    if not swap_resp or "swapTransaction" not in swap_resp: return f"Swap Error: {swap_resp}"

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