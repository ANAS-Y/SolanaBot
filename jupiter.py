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

JUP_HOST = "quote-api.jup.ag"
JUP_PRICE_HOST = "api.jup.ag"
JUP_API_IP = "172.67.163.67" # Cloudflare Direct IP
SOL_MINT = "So11111111111111111111111111111111111111112"

# --- NETWORK HARDENING ---
class DirectResolver(aiohttp.resolver.AbstractResolver):
    async def resolve(self, host, port=0, family=socket.AF_INET):
        if host == JUP_HOST or host == JUP_PRICE_HOST:
            return [{'hostname': host, 'host': JUP_API_IP, 'port': port, 'family': family, 'proto': 0, 'flags': 0}]
        return await aiohttp.resolver.ThreadedResolver().resolve(host, port, family)
    async def close(self): pass

def get_conn():
    return aiohttp.TCPConnector(resolver=DirectResolver(), ssl=False)

async def retry_request(url, method="GET", payload=None):
    headers = {"Host": JUP_HOST if "quote-api" in url else JUP_PRICE_HOST}
    for attempt in range(3):
        try:
            async with aiohttp.ClientSession(connector=get_conn()) as session:
                if method == "GET":
                    async with session.get(url, headers=headers) as resp:
                        if resp.status == 200: return await resp.json()
                elif method == "POST":
                    async with session.post(url, json=payload, headers=headers) as resp:
                        if resp.status == 200: return await resp.json()
        except:
            await asyncio.sleep(1)
    return None

# --- IMPROVED BALANCE CHECK ---
async def get_sol_balance(rpc_url, pubkey_str):
    try:
        # We use standard aiohttp for RPC to allow DNS to resolve the RPC URL normally
        async with AsyncClient(rpc_url) as client:
            resp = await client.get_balance(Pubkey.from_string(pubkey_str))
            return resp.value
    except Exception as e:
        logging.error(f"❌ Balance Check Failed: {e}")
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
    q_url = f"https://{JUP_HOST}/v6/quote?inputMint={input_mint}&outputMint={output_mint}&amount={int(amount_lamports)}&slippageBps={slippage}"
    quote = await retry_request(q_url)
    if not quote or "error" in quote: return f"Quote Error: {quote}"

    payload = {"quoteResponse": quote, "userPublicKey": str(keypair.pubkey()), "wrapAndUnwrapSol": True, "prioritizationFeeLamports": 10000}
    swap = await retry_request(f"https://{JUP_HOST}/v6/swap", method="POST", payload=payload)
    if not swap or "swapTransaction" not in swap: return f"Swap Error: {swap}"

    try:
        raw_tx = base64.b64decode(swap['swapTransaction'])
        tx = VersionedTransaction.from_bytes(raw_tx)
        msg = to_bytes_versioned(tx.message)
        sig = keypair.sign_message(msg)
        signed_tx = VersionedTransaction.populate(tx.message, [sig])
        
        async with AsyncClient(rpc_url) as client:
            resp = await client.send_raw_transaction(bytes(signed_tx), opts=TxOpts(skip_preflight=True))
            return str(resp.value)
    except Exception as e: return f"Execution Error: {str(e)}"