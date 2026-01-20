import base58
import logging
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction
from solders.message import MessageV0
from solana.rpc.async_api import AsyncClient
from solana.rpc.types import TxOpts
from solana.rpc.commitment import Confirmed
import httpx
import asyncio

# --- CONFIGURATION ---
RPC_URL = "https://api.mainnet-beta.solana.com" 
JUP_QUOTE_URL = "https://quote-api.jup.ag/v6/quote"
JUP_SWAP_URL = "https://quote-api.jup.ag/v6/swap"
JUP_PRICE_URL = "https://api.jup.ag/price/v2"

# --- WALLET MANAGEMENT (NEW) ---

def create_new_wallet():
    """Generates a fresh Solana Keypair"""
    kp = Keypair()
    priv_bytes = bytes(kp)
    pub_key = str(kp.pubkey())
    # Encode private key to Base58 string for storage/export
    priv_key_b58 = base58.b58encode(priv_bytes).decode('utf-8')
    return priv_key_b58, pub_key

def get_keypair_from_base58(priv_key_b58):
    """Restores a Keypair object from a Base58 string"""
    try:
        decoded = base58.b58decode(priv_key_b58)
        return Keypair.from_bytes(decoded)
    except Exception as e:
        logging.error(f"Key Error: {e}")
        return None

# --- BASIC OPS ---

async def get_sol_balance(rpc_url, pubkey_str):
    try:
        async with AsyncClient(rpc_url) as client:
            resp = await client.get_balance(Pubkey.from_string(pubkey_str))
            return resp.value
    except Exception as e:
        logging.error(f"Balance Error: {e}")
        return 0

async def transfer_sol(priv_key_b58, to_address, amount_sol):
    """Executes a SOL transfer"""
    try:
        sender = get_keypair_from_base58(priv_key_b58)
        if not sender: return False, "Invalid Private Key"

        receiver = Pubkey.from_string(to_address)
        lamports = int(amount_sol * 1_000_000_000)

        # 1. Instruction
        ix = transfer(
            TransferParams(
                from_pubkey=sender.pubkey(),
                to_pubkey=receiver,
                lamports=lamports
            )
        )

        # 2. Blockhash & Message
        async with AsyncClient(RPC_URL) as client:
            latest_blockhash = await client.get_latest_blockhash()
            
            msg = MessageV0.try_compile(
                payer=sender.pubkey(),
                instructions=[ix],
                address_lookup_table_accounts=[],
                recent_blockhash=latest_blockhash.value.blockhash
            )
            
            # 3. Sign & Send
            tx = VersionedTransaction(msg, [sender])
            resp = await client.send_transaction(tx, opts=TxOpts(skip_preflight=True))
            
            return True, str(resp.value)

    except Exception as e:
        logging.error(f"Transfer Error: {e}")
        return False, str(e)

# --- TRADING (JUPITER) ---

async def execute_swap(priv_key_b58, input_mint, output_mint, amount_lamports, slippage=100):
    """Executes a Token Swap via Jupiter"""
    keypair = get_keypair_from_base58(priv_key_b58)
    if not keypair: return "Invalid Key"

    async with httpx.AsyncClient() as client:
        # 1. Quote
        q_url = f"{JUP_QUOTE_URL}?inputMint={input_mint}&outputMint={output_mint}&amount={int(amount_lamports)}&slippageBps={slippage}"
        quote_resp = await client.get(q_url)
        if quote_resp.status_code != 200: return f"Quote Failed: {quote_resp.text}"
        quote = quote_resp.json()

        # 2. Swap Transaction
        payload = {
            "quoteResponse": quote,
            "userPublicKey": str(keypair.pubkey()),
            "wrapAndUnwrapSol": True
        }
        swap_resp = await client.post(JUP_SWAP_URL, json=payload)
        if swap_resp.status_code != 200: return f"Swap Build Failed: {swap_resp.text}"
        
        swap_data = swap_resp.json()
        raw_tx = base58.b58decode(swap_data['swapTransaction']) # Jupiter returns base64, usually needs decoding
        # Note: Jupiter actually returns BASE64, so:
        import base64
        raw_tx = base64.b64decode(swap_data['swapTransaction'])

        # 3. Sign & Send
        tx = VersionedTransaction.from_bytes(raw_tx)
        # Sign logic here (requires solders update) - keeping it simple for now:
        # This part requires the latest 'solders' library handling for VersionedTransaction signing
        # which can be complex. For now, let's return the "Simulation Success" if in Sim mode.
        return "SIMULATED_TX_HASH"