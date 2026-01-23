import base58
import base64
import logging
import json
import asyncio
import aiohttp # Switched to aiohttp for better DNS handling on Render
import random

from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction
from solders.message import MessageV0
from solana.rpc.async_api import AsyncClient
from solana.rpc.types import TxOpts

# --- CONFIGURATION ---
# Public RPCs - Randomize to avoid rate limits
RPC_ENDPOINTS = [
    "https://api.mainnet-beta.solana.com",
    "https://solana-rpc.publicnode.com",
    "https://rpc.ankr.com/solana"
]

JUP_QUOTE_URL = "https://quote-api.jup.ag/v6/quote"
JUP_SWAP_URL = "https://quote-api.jup.ag/v6/swap"
SOL_MINT = "So11111111111111111111111111111111111111112"

# --- KEY MANAGEMENT ---
def create_new_wallet():
    kp = Keypair()
    priv_bytes = bytes(kp)
    pub_key = str(kp.pubkey())
    priv_key_b58 = base58.b58encode(priv_bytes).decode('utf-8')
    return priv_key_b58, pub_key

def get_keypair_from_input(input_str):
    input_str = input_str.strip()
    try:
        # Try JSON Array
        if input_str.startswith("[") and input_str.endswith("]"):
            raw_bytes = json.loads(input_str)
            return Keypair.from_bytes(bytes(raw_bytes))
        # Try Base58
        decoded = base58.b58decode(input_str)
        return Keypair.from_bytes(decoded)
    except:
        return None

# --- CLIENT HELPERS ---
async def get_rpc_client():
    """Finds a working RPC"""
    random.shuffle(RPC_ENDPOINTS)
    for url in RPC_ENDPOINTS:
        try:
            client = AsyncClient(url, timeout=5)
            if await client.is_connected():
                return client
            await client.close()
        except:
            continue
    # Last resort
    return AsyncClient(RPC_ENDPOINTS[0])

# --- BASIC OPS ---
async def get_sol_balance(ignored_url, pubkey_str):
    client = await get_rpc_client()
    try:
        resp = await client.get_balance(Pubkey.from_string(pubkey_str))
        await client.close()
        return resp.value
    except:
        await client.close()
        return 0

async def transfer_sol(priv_key, to_address, amount_sol):
    sender = get_keypair_from_input(priv_key)
    if not sender: return False, "Invalid Key"
    
    try:
        receiver = Pubkey.from_string(to_address)
        lamports = int(amount_sol * 1_000_000_000)
        ix = transfer(TransferParams(from_pubkey=sender.pubkey(), to_pubkey=receiver, lamports=lamports))
        
        client = await get_rpc_client()
        latest_blockhash = await client.get_latest_blockhash()
        msg = MessageV0.try_compile(sender.pubkey(), [ix], [], latest_blockhash.value.blockhash)
        tx = VersionedTransaction(msg, [sender])
        resp = await client.send_transaction(tx, opts=TxOpts(skip_preflight=True))
        await client.close()
        return True, str(resp.value)
    except Exception as e:
        return False, str(e)

# --- REAL TRADING ENGINE (aiohttp + Headers) ---
async def execute_swap(priv_key, input_mint, output_mint, amount_lamports, slippage=100, is_simulation=False):
    """
    Executes a REAL Swap using aiohttp to fix DNS/Network issues.
    """
    if is_simulation:
        return True, "SIMULATED_TX_HASH_XYZ"

    keypair = get_keypair_from_input(priv_key)
    if not keypair: return False, "Invalid Private Key"

    # HEADERS ARE CRITICAL TO AVOID BLOCKING
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json"
    }

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            # 1. Get Quote
            q_url = f"{JUP_QUOTE_URL}?inputMint={input_mint}&outputMint={output_mint}&amount={int(amount_lamports)}&slippageBps={slippage}"
            
            async with session.get(q_url) as resp:
                if resp.status != 200:
                    err_txt = await resp.text()
                    return False, f"Quote Failed: {resp.status}"
                quote = await resp.json()

            # 2. Get Swap Transaction
            payload = {
                "quoteResponse": quote,
                "userPublicKey": str(keypair.pubkey()),
                "wrapAndUnwrapSol": True,
                "priorityFee": {"jitoTipLamports": 1000} 
            }
            
            async with session.post(JUP_SWAP_URL, json=payload) as resp:
                if resp.status != 200:
                    return False, "Swap Build Failed"
                swap_data = await resp.json()
                
            raw_tx = base64.b64decode(swap_data['swapTransaction'])

    except Exception as e:
        logging.error(f"Jupiter API Error: {e}")
        return False, "Network/API Error"

    # 3. Sign & Send
    client = await get_rpc_client()
    try:
        tx = VersionedTransaction.from_bytes(raw_tx)
        signed_tx = VersionedTransaction(tx.message, [keypair])
        
        opts = TxOpts(skip_preflight=True)
        resp = await client.send_transaction(signed_tx, opts=opts)
        await client.close()
        
        return True, str(resp.value)

    except Exception as e:
        await client.close()
        return False, f"Chain Error: {str(e)[:50]}"