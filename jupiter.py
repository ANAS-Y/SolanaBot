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

# --- FAILOVER RPCs ---
RPC_ENDPOINTS = [
    "https://api.mainnet-beta.solana.com",
    "https://solana-rpc.publicnode.com",
    "https://rpc.ankr.com/solana"
]

JUP_QUOTE_URL = "https://quote-api.jup.ag/v6/quote"
JUP_SWAP_URL = "https://quote-api.jup.ag/v6/swap"
SOL_MINT = "So11111111111111111111111111111111111111112"

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

async def get_working_client():
    for rpc in RPC_ENDPOINTS:
        try:
            client = AsyncClient(rpc)
            await client.get_health()
            return client
        except: await client.close()
    return AsyncClient(RPC_ENDPOINTS[0])

async def get_sol_balance(rpc_url_ignored, pubkey_str):
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

async def execute_swap(priv_key, input_mint, output_mint, amount_lamports, slippage=100, is_simulation=False):
    if is_simulation: return True, "SIMULATED_TX"
    keypair = get_keypair_from_input(priv_key)
    if not keypair: return False, "Invalid Key"

    try:
        async with httpx.AsyncClient(timeout=15.0) as http_client:
            q_url = f"{JUP_QUOTE_URL}?inputMint={input_mint}&outputMint={output_mint}&amount={int(amount_lamports)}&slippageBps={slippage}"
            quote_resp = await http_client.get(q_url)
            if quote_resp.status_code != 200: return False, "Quote Failed"
            quote = quote_resp.json()
            payload = {"quoteResponse": quote, "userPublicKey": str(keypair.pubkey()), "wrapAndUnwrapSol": True, "priorityFee": {"jitoTipLamports": 1000}}
            swap_resp = await http_client.post(JUP_SWAP_URL, json=payload)
            if swap_resp.status_code != 200: return False, "Swap Failed"
            raw_tx = base64.b64decode(swap_resp.json()['swapTransaction'])
    except: return False, "Net Error"

    client = await get_working_client()
    try:
        tx = VersionedTransaction.from_bytes(raw_tx)
        signed_tx = VersionedTransaction(tx.message, [keypair])
        resp = await client.send_transaction(signed_tx, opts=TxOpts(skip_preflight=True))
        await client.close()
        return True, str(resp.value)
    except Exception as e:
        await client.close()
        return False, str(e)