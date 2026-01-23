import base58
import base64
import logging
import json
import asyncio
import aiohttp
import random

from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction
from solders.message import MessageV0
from solana.rpc.async_api import AsyncClient
from solana.rpc.types import TxOpts

# --- FAILOVER RPCs ---
# We use a list of high-availability public nodes
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
        if input_str.startswith("[") and input_str.endswith("]"):
            raw_bytes = json.loads(input_str)
            return Keypair.from_bytes(bytes(raw_bytes))
        decoded = base58.b58decode(input_str)
        return Keypair.from_bytes(decoded)
    except: return None

# --- NETWORK HELPERS ---
async def get_working_client():
    """
    Finds a working RPC by trying simple Version checks 
    instead of the strict Health checks that return 404.
    """
    random.shuffle(RPC_ENDPOINTS)
    for rpc in RPC_ENDPOINTS:
        try:
            client = AsyncClient(rpc, timeout=5)
            # Use get_version() as it is universally supported
            await client.get_version()
            return client
        except:
            await client.close()
            continue
            
    # Fallback to default
    return AsyncClient(RPC_ENDPOINTS[0])

# --- BASIC OPS ---
async def get_sol_balance(ignored, pubkey_str):
    client = await get_working_client()
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
        
        client = await get_working_client()
        latest_blockhash = await client.get_latest_blockhash()
        msg = MessageV0.try_compile(sender.pubkey(), [ix], [], latest_blockhash.value.blockhash)
        tx = VersionedTransaction(msg, [sender])
        resp = await client.send_transaction(tx, opts=TxOpts(skip_preflight=True))
        await client.close()
        return True, str(resp.value)
    except Exception as e:
        return False, str(e)

# --- TRADING ENGINE ---
async def execute_swap(priv_key, input_mint, output_mint, amount_lamports, slippage=100, is_simulation=False):
    if is_simulation: return True, "SIMULATED_TX"
    
    keypair = get_keypair_from_input(priv_key)
    if not keypair: return False, "Invalid Key"

    # 1. Get Quote & Tx from Jupiter (with Retries)
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    raw_tx = None
    
    for attempt in range(3):
        try:
            # Use a fresh session for each attempt to reset DNS cache if needed
            async with aiohttp.ClientSession(headers=headers) as session:
                q_url = f"{JUP_QUOTE_URL}?inputMint={input_mint}&outputMint={output_mint}&amount={int(amount_lamports)}&slippageBps={slippage}"
                
                async with session.get(q_url, timeout=10) as resp:
                    if resp.status != 200: continue
                    quote = await resp.json()

                payload = {
                    "quoteResponse": quote,
                    "userPublicKey": str(keypair.pubkey()),
                    "wrapAndUnwrapSol": True,
                    "priorityFee": {"jitoTipLamports": 1000}
                }
                
                async with session.post(JUP_SWAP_URL, json=payload, timeout=10) as resp:
                    if resp.status != 200: continue
                    swap_data = await resp.json()
                    raw_tx = base64.b64decode(swap_data['swapTransaction'])
                    break # Success
        except Exception as e:
            logging.error(f"Jup Attempt {attempt} failed: {e}")
            await asyncio.sleep(1)
            
    if not raw_tx: return False, "Jupiter API Unreachable"

    # 2. Sign & Send (RPC Failover)
    client = await get_working_client()
    try:
        tx = VersionedTransaction.from_bytes(raw_tx)
        signed_tx = VersionedTransaction(tx.message, [keypair])
        resp = await client.send_transaction(signed_tx, opts=TxOpts(skip_preflight=True))
        await client.close()
        return True, str(resp.value)
    except Exception as e:
        await client.close()
        return False, f"Chain: {str(e)[:50]}"