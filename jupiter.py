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

# --- NETWORK HARDENING (Enterprise DNS Fix) ---
def get_conn():
    """
    Creates a connection using Google DNS (8.8.8.8).
    This fixes 'No Address Associated' errors on Render/VPS
    without hardcoding a temporary IP.
    """
    return aiohttp.TCPConnector(
        family=socket.AF_INET, # Force IPv4
        resolver=aiohttp.AsyncResolver(nameservers=["8.8.8.8", "1.1.1.1"]), # Force Google DNS
        ssl=False # Prevent SSL Handshake hangs
    )

async def retry_request(url, method="GET", payload=None):
    """Retries a network request 3 times."""
    for attempt in range(3):
        try:
            async with aiohttp.ClientSession(connector=get_conn()) as session:
                if method == "GET":
                    async with session.get(url, timeout=10) as resp:
                        if resp.status != 200:
                            logging.warning(f"⚠️ HTTP {resp.status} from {url}")
                            if resp.status == 429: # Rate limit
                                await asyncio.sleep(2)
                                continue
                            return None # Stop if error is not temporary
                        return await resp.json()
                elif method == "POST":
                    async with session.post(url, json=payload, timeout=10) as resp:
                        if resp.status != 200:
                            text = await resp.text()
                            logging.error(f"⚠️ Swap Error {resp.status}: {text}")
                            return None
                        return await resp.json()
        except Exception as e:
            logging.warning(f"⚠️ Network Attempt {attempt+1} failed: {e}")
            await asyncio.sleep(1)
    return None

async def get_sol_balance(rpc_url, pubkey_str):
    try:
        # For RPC calls, we use standard connection (AsyncClient handles it)
        async with AsyncClient(rpc_url) as client:
            resp = await client.get_balance(Pubkey.from_string(pubkey_str))
            return resp.value
    except Exception as e:
        logging.error(f"Balance Check Error: {e}")
        return 0

async def get_market_cap(mint, rpc_url):
    try:
        # 1. Get Price
        price_data = await retry_request(f"{JUP_PRICE}?ids={mint}")
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
    url = f"{JUP_PRICE}?ids={mint}"
    data = await retry_request(url)
    try:
        return float(data['data'][mint]['price'])
    except:
        return None

async def execute_swap(keypair, input_mint, output_mint, amount_lamports, rpc_url, slippage=100):
    # 1. Get Quote
    q_url = f"{JUP_QUOTE}?inputMint={input_mint}&outputMint={output_mint}&amount={int(amount_lamports)}&slippageBps={slippage}"
    quote = await retry_request(q_url)
    
    if not quote: 
        return "Quote Error: Connection Failed (Check Logs)"
    if "error" in quote:
        return f"Quote Error: {quote['error']}"

    # 2. Get Swap Transaction
    payload = {
        "quoteResponse": quote,
        "userPublicKey": str(keypair.pubkey()),
        "wrapAndUnwrapSol": True,
        "prioritizationFeeLamports": 10000 
    }
    swap_resp = await retry_request(JUP_SWAP, method="POST", payload=payload)
    
    if not swap_resp:
        return "Swap Error: No Response from API"
    if "swapTransaction" not in swap_resp: 
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