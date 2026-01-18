import aiohttp
import base64
import logging
import socket
import asyncio
from solders.transaction import VersionedTransaction
from solders.message import to_bytes_versioned
from solana.rpc.async_api import AsyncClient
from solana.rpc.types import TxOpts

JUP_QUOTE = "https://quote-api.jup.ag/v6/quote"
JUP_SWAP = "https://quote-api.jup.ag/v6/swap"
JUP_PRICE = "https://api.jup.ag/price/v2"

SOL_MINT = "So11111111111111111111111111111111111111112"

# --- NETWORK HARDENING ---
# Force IPv4 to prevent "No address associated" errors
def get_conn():
    return aiohttp.TCPConnector(family=socket.AF_INET, ssl=False)

async def retry_request(url, method="GET", payload=None):
    """Retries a network request 3 times before failing."""
    for attempt in range(3):
        try:
            async with aiohttp.ClientSession(connector=get_conn()) as session:
                if method == "GET":
                    async with session.get(url) as resp:
                        return await resp.json()
                elif method == "POST":
                    async with session.post(url, json=payload) as resp:
                        return await resp.json()
        except Exception as e:
            logging.warning(f"⚠️ Network Attempt {attempt+1} failed: {e}")
            await asyncio.sleep(1) # Wait 1 second before retrying
    return None

async def get_price(mint):
    url = f"{JUP_PRICE}?ids={mint}"
    data = await retry_request(url)
    try:
        return float(data['data'][mint]['price'])
    except:
        logging.error(f"❌ Failed to fetch price for {mint}")
        return None

async def execute_swap(keypair, input_mint, output_mint, amount_lamports, rpc_url, slippage=100):
    # 1. Get Quote
    q_url = f"{JUP_QUOTE}?inputMint={input_mint}&outputMint={output_mint}&amount={int(amount_lamports)}&slippageBps={slippage}"
    quote = await retry_request(q_url)
    
    if not quote or "error" in quote: 
        return f"Quote Error: {quote.get('error', 'Network Fail') if quote else 'No Response'}"

    # 2. Get Swap Transaction
    payload = {
        "quoteResponse": quote,
        "userPublicKey": str(keypair.pubkey()),
        "wrapAndUnwrapSol": True,
        "prioritizationFeeLamports": 10000 
    }
    swap_resp = await retry_request(JUP_SWAP, method="POST", payload=payload)
    
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