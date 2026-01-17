import aiohttp
import base64
import logging
from solders.transaction import VersionedTransaction # type: ignore
from solders.message import to_bytes_versioned # type: ignore
from solana.rpc.async_api import AsyncClient
from solana.rpc.types import TxOpts

JUP_QUOTE = "https://quote-api.jup.ag/v6/quote"
JUP_SWAP = "https://quote-api.jup.ag/v6/swap"
JUP_PRICE = "https://api.jup.ag/price/v2"

# Token Mints
SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

async def get_price(mint):
    try:
        async with aiohttp.ClientSession() as session:
            url = f"{JUP_PRICE}?ids={mint}"
            async with session.get(url) as resp:
                data = await resp.json()
                return float(data['data'][mint]['price'])
    except Exception as e:
        logging.error(f"Price Fetch Error: {e}")
        return None

async def execute_swap(keypair, input_mint, output_mint, amount_lamports, rpc_url, slippage=100):
    try:
        # 1. Get Quote
        async with aiohttp.ClientSession() as session:
            q_url = f"{JUP_QUOTE}?inputMint={input_mint}&outputMint={output_mint}&amount={int(amount_lamports)}&slippageBps={slippage}"
            async with session.get(q_url) as resp:
                quote = await resp.json()
                if "error" in quote: return f"Quote Error: {quote['error']}"

        # 2. Get Transaction Object
        payload = {
            "quoteResponse": quote,
            "userPublicKey": str(keypair.pubkey()),
            "wrapAndUnwrapSol": True,
            # Aggressive priority fee for faster execution
            "prioritizationFeeLamports": 10000 
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(JUP_SWAP, json=payload) as resp:
                swap_resp = await resp.json()
                if "swapTransaction" not in swap_resp: return f"Swap Error: {swap_resp}"

        # 3. Sign Transaction
        raw_tx = base64.b64decode(swap_resp['swapTransaction'])
        tx = VersionedTransaction.from_bytes(raw_tx)
        message = to_bytes_versioned(tx.message)
        signature = keypair.sign_message(message)
        signed_tx = VersionedTransaction.populate(tx.message, [signature])

        # 4. Send to Blockchain
        async with AsyncClient(rpc_url) as client:
            opts = TxOpts(skip_preflight=True)
            resp = await client.send_raw_transaction(bytes(signed_tx), opts=opts)
            return str(resp.value) # Returns TX Sig
            
    except Exception as e:
        logging.error(f"Swap Exception: {e}")
        return f"Error: {str(e)}"