import aiohttp
import base64
import logging
import asyncio
import socket
import json
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

SOL_MINT = "So11111111111111111111111111111111111111112"

# --- DYNAMIC NETWORK RESOLVER ---

# Global cache to store the fresh IPs once we find them
IP_CACHE = {}

async def fetch_fresh_ip(hostname):
    """
    Asks Google DNS (8.8.8.8) for the latest IP of a hostname.
    Bypasses system DNS.
    """
    if hostname in IP_CACHE: return IP_CACHE[hostname]
    
    # We ask Google (8.8.8.8) directly via HTTPS
    doh_url = f"https://8.8.8.8/resolve?name={hostname}&type=A"
    
    try:
        # ssl=False is required to connect to 8.8.8.8 via IP
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
            async with session.get(doh_url, timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    if "Answer" in data:
                        # Get the last IP in the list (usually the most reliable)
                        fresh_ip = data["Answer"][-1]["data"]
                        logging.info(f"🌍 Resolved {hostname} -> {fresh_ip}")
                        IP_CACHE[hostname] = fresh_ip
                        return fresh_ip
    except Exception as e:
        logging.error(f"❌ DNS Lookup Failed for {hostname}: {e}")
        
    # Fallback to a known Cloudflare IP if Google fails (Emergency Backup)
    return "104.18.40.155" 

class DynamicResolver(aiohttp.resolver.AbstractResolver):
    """
    Injects the dynamically fetched IP into the connection
    while keeping the Hostname for SSL verification.
    """
    async def resolve(self, host, port=0, family=socket.AF_INET):
        # 1. Get the fresh IP (either from cache or by asking Google)
        target_ip = await fetch_fresh_ip(host)
        
        # 2. Return the mapping
        return [{
            'hostname': host,
            'host': target_ip,
            'port': port,
            'family': family,
            'proto': 0,
            'flags': 0
        }]

    async def close(self): pass

def get_conn():
    # Use the Dynamic Resolver
    return aiohttp.TCPConnector(resolver=DynamicResolver())

async def retry_request(url, method="GET", payload=None):
    for attempt in range(3):
        try:
            async with aiohttp.ClientSession(connector=get_conn()) as session:
                if method == "GET":
                    async with session.get(url, timeout=10) as resp:
                        if resp.status == 200: 
                            return await resp.json(content_type=None)
                        elif resp.status == 530:
                            # 530 means IP is bad. Clear cache to force new lookup next time.
                            logging.warning("⚠️ 530 Origin Error. Clearing DNS Cache.")
                            IP_CACHE.clear()
                        else:
                            logging.warning(f"⚠️ HTTP {resp.status} from {url}")
                elif method == "POST":
                    async with session.post(url, json=payload, timeout=10) as resp:
                        if resp.status == 200: 
                            return await resp.json(content_type=None)
                        else:
                            logging.error(f"⚠️ POST Error {resp.status}")
        except Exception as e:
            logging.warning(f"⚠️ Request Failed: {e}")
            await asyncio.sleep(1)
    return None

# --- BALANCE & TRADING ---
async def get_sol_balance(rpc_url, pubkey_str):
    try:
        # Use standard client for RPC (Alchemy/Helius usually work fine)
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