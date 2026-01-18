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
JUP_QUOTE = "https://quote-api.jup.ag/v6/quote"
JUP_SWAP = "https://quote-api.jup.ag/v6/swap"
JUP_PRICE = "https://api.jup.ag/price/v2"
SOL_MINT = "So11111111111111111111111111111111111111112"

# --- DNS OVER HTTPS (DoH) WITH IP BOOTSTRAP ---
class DoHResolver(aiohttp.resolver.AbstractResolver):
    """
    Resolves DNS via Cloudflare's IP (1.1.1.1).
    This bypasses system DNS entirely.
    """
    async def resolve(self, host, port=0, family=socket.AF_INET):
        # We connect to 1.1.1.1 directly so we don't need to resolve 'cloudflare-dns.com'
        url = f"https://1.1.1.1/dns-query?name={host}&type=A"
        headers = {"Accept": "application/dns-json"}
        
        try:
            # ssl=False is required here because we are connecting to an IP (1.1.1.1)
            # but the certificate is for 'cloudflare-dns.com'.
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        if "Answer" in data:
                            ip = data["Answer"][0]["data"]
                            return [{'hostname': host, 'host': ip, 'port': port,
                                     'family': family, 'proto': 0, 'flags': 0}]
        except Exception as e:
            logging.error(f"DoH Bootstrap Error: {e}")

        # If 1.1.1.1 fails, try Google's IP (8.8.8.8) as backup
        try:
            url_backup = f"https://8.8.8.8/resolve?name={host}&type=A"
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
                async with session.get(url_backup) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        if "Answer" in data:
                            ip = data["Answer"][0]["data"]
                            return [{'hostname': host, 'host': ip, 'port': port,
                                     'family': family, 'proto': 0, 'flags': 0}]
        except:
            pass

        # Final fallback (unlikely to work if system DNS is broken, but required by code structure)
        return await aiohttp.resolver.ThreadedResolver().resolve(host, port, family)

    async def close(self): pass

def get_conn():
    # Use our custom IP-based DoH resolver
    return aiohttp.TCPConnector(resolver=DoHResolver(), ssl=False)

async def retry_request(url, method="GET", payload=None):
    for attempt in range(3):
        try:
            async with aiohttp.ClientSession(connector=get_conn()) as session:
                if method == "GET":
                    async with session.get(url, timeout=10) as resp:
                        if resp.status == 200: 
                            return await resp.json(content_type=None)
                        elif resp.status == 429: await asyncio.sleep(2)
                        else: logging.warning(f"⚠️ HTTP {resp.status} from {url}")
                elif method == "POST":
                    async with session.post(url, json=payload, timeout=10) as resp:
                        if resp.status == 200: 
                            return await resp.json(content_type=None)
                        else: logging.error(f"⚠️ POST Error {resp.status}")
        except Exception as e:
            logging.warning(f"⚠️ Network Attempt {attempt+1} failed: {e}")
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
        price_data = await retry_request(f"{JUP_PRICE}?ids={mint}")
        if not price_data or 'data' not in price_data: return None
        price = float(price_data['data'][mint]['price'])

        async with AsyncClient(rpc_url) as client:
            supply = await client.get_token_supply(Pubkey.from_string(mint))
            return price * float(supply.value.ui_amount)
    except: return None

async def get_price(mint):
    data = await retry_request(f"{JUP_PRICE}?ids={mint}")
    try: return float(data['data'][mint]['price'])
    except: return None

async def execute_swap(keypair, input_mint, output_mint, amount_lamports, rpc_url, slippage=100):
    # 1. Quote
    q_url = f"{JUP_QUOTE}?inputMint={input_mint}&outputMint={output_mint}&amount={int(amount_lamports)}&slippageBps={slippage}"
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
    swap_resp = await retry_request(JUP_SWAP, method="POST", payload=payload)
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