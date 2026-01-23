import base58
import base64
import logging
import json
import asyncio
import httpx
import random

from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction
from solders.message import MessageV0
from solana.rpc.async_api import AsyncClient
from solana.rpc.types import TxOpts

# --- ROBUST CONFIGURATION ---
# List of RPCs to try in order. Randomize order to spread load.
RPC_ENDPOINTS = [
    "https://api.mainnet-beta.solana.com",
    "https://solana-rpc.publicnode.com",
    "https://rpc.ankr.com/solana",
    "https://mainnet.helius-rpc.com/?api-key=10ba898b-70c3-45c1-a836-339233630718" # Free tier example
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

# --- SMART CLIENT HELPERS ---
async def get_working_client():
    """Returns an AsyncClient connected to the first working RPC"""
    random.shuffle(RPC_ENDPOINTS) # Randomize to find a working one faster
    
    for rpc in RPC_ENDPOINTS:
        try:
            # Short timeout for health check
            client = AsyncClient(rpc, timeout=5.0) 
            await client.get_health()
            logging.info(f"Connected to RPC: {rpc}")
            return client
        except:
            await client.close()
            continue
            
    # Fallback if everything fails
    return AsyncClient("https://api.mainnet-beta.solana.com")

# --- BASIC OPS ---
async def get_sol_balance(rpc_url_ignored, pubkey_str):
    """Fetches balance using Failover RPCs"""
    client = await get_working_client()
    try:
        resp = await client.get_balance(Pubkey.from_string(pubkey_str))
        await client.close()
        return resp.value
    except Exception as e:
        logging.error(f"Balance Error: {e}")
        await client.close()
        return 0

async def transfer_sol(priv_key, to_address, amount_sol):
    sender = get_keypair_from_input(priv_key)
    if not sender: return False, "Invalid Key"
    
    receiver = Pubkey.from_string(to_address)
    lamports = int(amount_sol * 1_000_000_000)
    
    ix = transfer(TransferParams(from_pubkey=sender.pubkey(), to_pubkey=receiver, lamports=lamports))
    
    client = await get_working_client()
    try:
        latest_blockhash = await client.get_latest_blockhash()
        msg = MessageV0.try_compile(sender.pubkey(), [ix], [], latest_blockhash.value.blockhash)
        tx = VersionedTransaction(msg, [sender])
        resp = await client.send_transaction(tx, opts=TxOpts(skip_preflight=True))
        await client.close()
        return True, str(resp.value)
    except Exception as e:
        await client.close()
        return False, str(e)

# --- REAL TRADING ENGINE ---
async def execute_swap(priv_key, input_mint, output_mint, amount_lamports, slippage=100, is_simulation=False):
    """
    Executes a REAL Swap on Solana via Jupiter with Robust Retries.
    """
    if is_simulation:
        return True, "SIMULATED_TX_HASH"

    keypair = get_keypair_from_input(priv_key)
    if not keypair: return False, "Invalid Private Key"

    # 1. Get Quote & Transaction from Jupiter (HTTP)
    # We use a loop to retry if the API hiccups
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=20.0) as http_client:
                # Quote
                q_url = f"{JUP_QUOTE_URL}?inputMint={input_mint}&outputMint={output_mint}&amount={int(amount_lamports)}&slippageBps={slippage}"
                quote_resp = await http_client.get(q_url)
                
                if quote_resp.status_code != 200: 
                    logging.error(f"Quote Failed: {quote_resp.text}")
                    continue # Retry loop
                
                quote = quote_resp.json()

                # Swap Tx Build
                payload = {
                    "quoteResponse": quote,
                    "userPublicKey": str(keypair.pubkey()),
                    "wrapAndUnwrapSol": True,
                    "priorityFee": {"jitoTipLamports": 1000} # Priority fee helps success rate
                }
                swap_resp = await http_client.post(JUP_SWAP_URL, json=payload)
                
                if swap_resp.status_code != 200:
                    logging.error(f"Swap Build Failed: {swap_resp.text}")
                    continue # Retry loop
                
                swap_data = swap_resp.json()
                raw_tx = base64.b64decode(swap_data['swapTransaction'])
                break # Success! Exit loop
        except Exception as e:
            logging.error(f"Jupiter Net Error (Attempt {attempt}): {e}")
            await asyncio.sleep(1)
    else:
        # If loop finishes without breaking, all 3 attempts failed
        return False, "Jupiter API Unreachable"

    # 2. Sign & Send to Blockchain (RPC Failover)
    client = await get_working_client()
    try:
        # Deserialize
        tx = VersionedTransaction.from_bytes(raw_tx)
        
        # Resign with our key (Critical Step)
        signed_tx = VersionedTransaction(tx.message, [keypair])
        
        # Send
        opts = TxOpts(skip_preflight=True)
        resp = await client.send_transaction(signed_tx, opts=opts)
        await client.close()
        
        return True, str(resp.value)

    except Exception as e:
        await client.close()
        return False, f"Chain Error: {e}"