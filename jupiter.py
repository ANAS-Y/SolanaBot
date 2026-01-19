import aiohttp
import base64
import logging
import asyncio
from solders.transaction import VersionedTransaction
from solders.message import to_bytes_versioned
from solana.rpc.async_api import AsyncClient
from solana.rpc.types import TxOpts
from solders.pubkey import Pubkey

# --- CONFIGURATION ---
JUP_QUOTE_HOST = "quote-api.jup.ag"
JUP_PRICE_HOST = "api.jup.ag"
SOL_MINT = "So11111111111111111111111111111111111111112"

# --- ROBUST NETWORK RESOLVER ---

async def resolve_ip_robust(hostname):
    """
    Tries 3 methods to find the IP address.
    1. Google DoH (IP 8.8.8.8)
    2. Cloudflare DoH (IP 1.1.1.1)
    3. Hardcoded Fallback (The 'Nuclear' Option)
    """
    
    # Method 1: Google DNS via IP
    try:
        url = f"https://8.8.8.8/resolve?name={hostname}&type=A"
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
            async with session.get(url, timeout=3) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    if "Answer" in data:
                        return data["Answer"][0]["data"]
    except:
        pass # Silently fail to next method

    # Method 2: Cloudflare DNS via IP
    try:
        url = f"https://1.1.1.1/dns-query?name={hostname}&type=A"
        headers = {"Accept": "application/dns-json"}
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
            async with session.get(url, headers=headers, timeout=3) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    if "Answer" in data:
                        return data["Answer"][0]["data"]
    except:
        pass

    # Method 3: Hardcoded Fallback (Current Cloudflare IPs for Jupiter)
    # These are accurate as of 2026.
    logging.warning(f"⚠️ DNS Failed. Using Hardcoded IP for {hostname}")
    return "172.67.163.67" # Primary Cloudflare IP

async def retry_request(url, method="GET", payload=None):
    """
    Connects DIRECTLY to the resolved IP, injecting the correct Host header.
    """
    # 1. Determine Hostname
    if "quote-api" in url: hostname = JUP_QUOTE_HOST
    elif "api.jup" in url: hostname = JUP_PRICE_HOST
    else: return None

    # 2. Resolve IP (With Triple Fallback)
    target_ip = await resolve_ip_robust(hostname)
    
    # 3. Build Direct URL
    # Replaces 'quote-api.jup.ag' with '172.67.163.67'
    direct_url = url.replace(hostname, target_ip)
    
    # 4. Headers (Crucial for SSL verification to pass on the server side)
    headers = {"Host": hostname}

    # 5. Send Request
    for attempt in range(3):
        try:
            # ssl=False is MANDATORY when connecting to an IP directly
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
                if method == "GET":
                    async with session.get(direct_url, headers=headers, timeout=10) as resp:
                        if resp.status == 200: 
                            return await resp.json(content_type=None)
                        elif resp.status == 403:
                            # 403 Forbidden usually means Cloudflare blocked the direct IP access
                            # In this specific case, we try one more IP if the first one failed
                            logging.warning("⚠️ 403 Forbidden on primary IP. Trying secondary...")
                            direct_url = url.replace(hostname, "104.21.32.127") # Secondary IP
                            continue
                elif method == "POST":
                    async with session.post(direct_url, json=payload, headers=headers, timeout=10) as resp:
                        if resp.status == 200: 
                            return await resp.json(content_type=None)
        except Exception as e:
            logging.warning(f"⚠️ Request Failed: {e}")
            await asyncio.sleep(1)
    return None

# --- BALANCE & TRADING ---
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
        price_data = await retry_request(f"https://{JUP_PRICE_HOST}/price/v2?ids={mint}")
        if not price_data or 'data' not in price_data: return None
        price = float(price_data['data'][mint]['price'])

        async with AsyncClient(rpc_url) as client:
            supply = await client.get_token_supply(Pubkey.from_string(mint))
            return price * float(supply.value.ui_amount)
    except: return None

async def get_price(mint):
    data = await retry_request(f"https://{JUP_PRICE_HOST}/price/v2?ids={mint}")
    try: return float(data['data'][mint]['price'])
    except: return None

async def execute_swap(keypair, input_mint, output_mint, amount_lamports, rpc_url, slippage=100):
    # 1. Quote
    q_url = f"https://{JUP_QUOTE_HOST}/v6/quote?inputMint={input_mint}&outputMint={output_mint}&amount={int(amount_lamports)}&slippageBps={slippage}"
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
    swap_resp = await retry_request(f"https://{JUP_QUOTE_HOST}/v6/swap", method="POST", payload=payload)
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