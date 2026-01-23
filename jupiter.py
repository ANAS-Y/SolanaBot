import base58
import base64
import logging
import json
import asyncio
import httpx

from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction
from solders.message import MessageV0
from solana.rpc.async_api import AsyncClient
from solana.rpc.types import TxOpts
from solana.rpc.commitment import Confirmed

# --- CONFIGURATION ---
RPC_URL = "https://api.mainnet-beta.solana.com" 
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
        
        # Try Base58
        decoded = base58.b58decode(input_str)
        return Keypair.from_bytes(decoded)
    except Exception as e:
        logging.error(f"Key Parse Error: {e}")
        return None

# --- BASIC OPS ---
async def get_sol_balance(rpc_url, pubkey_str):
    try:
        async with AsyncClient(rpc_url) as client:
            resp = await client.get_balance(Pubkey.from_string(pubkey_str))
            return resp.value
    except:
        return 0

async def transfer_sol(priv_key, to_address, amount_sol):
    try:
        sender = get_keypair_from_input(priv_key)
        if not sender: return False, "Invalid Key"
        
        receiver = Pubkey.from_string(to_address)
        lamports = int(amount_sol * 1_000_000_000)
        
        ix = transfer(TransferParams(from_pubkey=sender.pubkey(), to_pubkey=receiver, lamports=lamports))
        
        async with AsyncClient(RPC_URL) as client:
            latest_blockhash = await client.get_latest_blockhash()
            msg = MessageV0.try_compile(sender.pubkey(), [ix], [], latest_blockhash.value.blockhash)
            tx = VersionedTransaction(msg, [sender])
            resp = await client.send_transaction(tx, opts=TxOpts(skip_preflight=True))
            return True, str(resp.value)
    except Exception as e:
        return False, str(e)

# --- REAL TRADING ENGINE ---
async def execute_swap(priv_key, input_mint, output_mint, amount_lamports, slippage=100, is_simulation=False):
    """
    Executes a REAL Swap on Solana via Jupiter.
    """
    if is_simulation:
        return True, "SIMULATED_TX_HASH_12345"

    keypair = get_keypair_from_input(priv_key)
    if not keypair: return False, "Invalid Private Key"

    try:
        async with httpx.AsyncClient() as client:
            # 1. Get Quote
            q_url = f"{JUP_QUOTE_URL}?inputMint={input_mint}&outputMint={output_mint}&amount={int(amount_lamports)}&slippageBps={slippage}"
            quote_resp = await client.get(q_url)
            if quote_resp.status_code != 200: return False, f"Quote Failed: {quote_resp.text}"
            quote = quote_resp.json()

            # 2. Get Swap Transaction
            payload = {
                "quoteResponse": quote,
                "userPublicKey": str(keypair.pubkey()),
                "wrapAndUnwrapSol": True,
                "priorityFee": {"jitoTipLamports": 1000} # Small tip for speed
            }
            swap_resp = await client.post(JUP_SWAP_URL, json=payload)
            if swap_resp.status_code != 200: return False, f"Swap Build Failed: {swap_resp.text}"
            
            swap_data = swap_resp.json()
            raw_tx = base64.b64decode(swap_data['swapTransaction'])
            
            # 3. Sign Transaction
            # Deserialize the transaction from Jupiter
            tx = VersionedTransaction.from_bytes(raw_tx)
            
            # Create a NEW signed transaction using our keypair and the message from Jupiter
            signed_tx = VersionedTransaction(tx.message, [keypair])
            
            # 4. Send to Blockchain
            async with AsyncClient(RPC_URL) as sol_client:
                opts = TxOpts(skip_preflight=True, max_retries=3)
                resp = await sol_client.send_transaction(signed_tx, opts=opts)
                return True, str(resp.value)

    except Exception as e:
        logging.error(f"Swap Error: {e}")
        return False, str(e)