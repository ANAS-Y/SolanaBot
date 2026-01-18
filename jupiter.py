import aiohttp
import base64
import logging
import asyncio
import ssl
from solders.transaction import VersionedTransaction
from solders.message import to_bytes_versioned
from solana.rpc.async_api import AsyncClient
from solana.rpc.types import TxOpts
from solders.pubkey import Pubkey

# --- CONFIGURATION ---
JUP_QUOTE_HOST = "quote-api.jup.ag"
JUP_PRICE_HOST = "api.jup.ag"
SOL_MINT = "So11111111111111111111111111111111111111112"

# --- MANUAL NETWORK OVERRIDE ---

async def manual_resolve_ip(hostname):
    """
    Manually asks Cloudflare (1.1.1.1) for the IP address.
    Bypasses system DNS completely.
    """
    # We connect directly to Cloudflare's IP to ask for the address
    doh_url = f"https://1.1.1.1/dns-query?name={hostname}&type=A"
    headers = {"Accept": "application/dns-json"}
    
    # We use a clean connector for the DNS lookup itself
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
            async with session.get(doh_url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    if "Answer" in data:
                        # Return the first IP found
                        return data["Answer"][0]["data"]
    except Exception as e:
        logging.error(f"Manual DNS Fail for {hostname}: {e}")
    return None

async def retry_request(url, method="GET", payload=None):
    """
    Connects DIRECTLY to the IP address, bypassing DNS.
    Injects the 'Host' header so Jupiter knows who we are.
    """
    # 1. Extract Hostname from URL
    if "quote-api" in url:
        hostname = JUP_QUOTE_HOST
    elif "api.jup" in url:
        hostname = JUP_PRICE_HOST
    else:
        return None

    # 2. Manually Resolve IP
    target_ip = await manual_resolve_ip(hostname)
    if not target_ip:
        logging.error(f"❌ Could not resolve IP for {hostname}")
        return None

    # 3. Construct "Direct IP" URL
    # Replaces 'https://quote-api.jup.ag/...' with 'https://123.45.67.89/...'
    direct_url = url.replace(hostname, target_ip)
    
    # 4. Create Headers (Crucial: Tell the server who we really want)
    headers = {"Host": hostname}

    # 5. Execute Request
    for attempt in range(3):
        try:
            # ssl=False allows us to connect to an IP while asking for a Hostname
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
                if method == "GET":
                    async with session.get(direct_url, headers=headers, timeout=10) as resp:
                        if resp.status == 200: 
                            return await resp.json(content_type=None)
                elif method == "POST":
                    async with session.post(direct_url, json=payload, headers=headers, timeout=10) as resp:
                        if resp.status == 200: 
                            return await resp.json(content_type=None)
        except Exception as e:
            logging.warning(f"⚠️ IP Connect Attempt {attempt+1} failed: {e}")
            # If IP failed, maybe try resolving again? For now just sleep.
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
    if not quote: return "Quote Error: Connection Failed"
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