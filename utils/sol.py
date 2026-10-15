"""
Solana: deposit addresses from SLIP-10 ed25519 (m/44'/501'/i'/0', same as Phantom),
balance checks and SOL transfers through plain JSON-RPC. Legacy transaction format,
built by hand so there is no heavy dependency.
"""
import base64
import asyncio
import struct
import time
from decimal import Decimal

import aiohttp

from utils import hd

LAMPORTS = 10 ** 9
FEE_PER_SIGNATURE = 5000  # protocol base fee; we set no priority fee
SYSTEM_PROGRAM = bytes(32)


class SolError(Exception):
    pass


class SolPendingError(SolError):
    def __init__(self, message, txid):
        super().__init__(message)
        self.txid = txid


def derive(seed: bytes, index: int):
    priv = hd.derive_ed25519(seed, [hd.hard(44), hd.hard(501), hd.hard(index), hd.hard(0)])
    pub = hd.ed25519_pub(priv)
    return priv, pub, hd.b58encode(pub)


async def rpc(url, method, params):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=20)) as r:
            data = await r.json()
    if "error" in data:
        raise SolError(f"{method}: {data['error'].get('message', data['error'])}")
    return data["result"]


async def balance(url, addr, commitment="finalized"):
    res = await rpc(url, "getBalance", [addr, {"commitment": commitment}])
    return int(res["value"])


async def latest_signature(url, addr):
    try:
        sigs = await rpc(url, "getSignaturesForAddress", [addr, {"limit": 1}])
        return sigs[0]["signature"] if sigs else None
    except Exception:
        return None


async def account_exists(url, addr):
    res = await rpc(url, "getAccountInfo", [addr, {"encoding": "base64", "commitment": "confirmed"}])
    return res["value"] is not None


async def rent_minimum(url):
    return int(await rpc(url, "getMinimumBalanceForRentExemption", [0]))


def _cu16(n):
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out += bytes([b | 0x80])
        else:
            return out + bytes([b])


def build_transfer_tx(priv, pub, blockhash_b58, transfers):
    """transfers = [(dest_pubkey_bytes, lamports)] from `pub`. Returns raw signed tx bytes."""
    dests = []
    for d, _ in transfers:
        if d == pub:
            raise SolError("destination equals source")
        if d not in dests:
            dests.append(d)
    keys = [pub] + dests + [SYSTEM_PROGRAM]  # signer+writable, writable..., readonly program
    header = bytes([1, 0, 1])
    instrs = b""
    for d, lamports in transfers:
        data = struct.pack("<IQ", 2, lamports)  # SystemProgram::Transfer
        idxs = bytes([0, keys.index(d)])
        instrs += bytes([len(keys) - 1]) + _cu16(len(idxs)) + idxs + _cu16(len(data)) + data
    msg = (header + _cu16(len(keys)) + b"".join(keys) + hd.b58decode(blockhash_b58) +
           _cu16(len(transfers)) + instrs)
    sig = hd.ed25519_sign(priv, msg)
    return _cu16(1) + sig + msg, hd.b58encode(sig)


async def send(url, priv, pub, own_addr, to_addr, want_lamports, treasury, commitment="finalized"):
    """Pay to_addr; leftover (after fee) goes to treasury if configured and large enough. Returns (sig, sent)."""
    bal = await balance(url, own_addr, commitment)
    if bal <= FEE_PER_SIGNATURE:
        raise SolError("No funds at the deal address")
    rent = await rent_minimum(url)
    to_pub = hd.b58decode(to_addr)
    if len(to_pub) != 32:
        raise SolError("Invalid Solana address")

    transfers = []
    rest = bal - want_lamports - FEE_PER_SIGNATURE
    treasury_can_receive = bool(treasury) and (
        rest >= rent or await account_exists(url, treasury)
    )
    if treasury_can_receive and rest > 0:
        user_amt = want_lamports
        transfers = [(to_pub, user_amt), (hd.b58decode(treasury), rest)]
    else:
        user_amt = min(want_lamports, bal - FEE_PER_SIGNATURE)
        # anything left over (rest < rent) simply stays; a refund/full-balance send leaves 0
        transfers = [(to_pub, user_amt)]

    if not await account_exists(url, to_addr) and user_amt < rent:
        raise SolError(f"Solana requires at least {rent / LAMPORTS:.6f} SOL to open a new account")

    bh = await rpc(url, "getLatestBlockhash", [{"commitment": "confirmed"}])
    raw, sig = build_transfer_tx(priv, pub, bh["value"]["blockhash"], transfers)
    res = await rpc(url, "sendTransaction",
                    [base64.b64encode(raw).decode(), {"encoding": "base64", "preflightCommitment": "confirmed"}])
    # sendTransaction only means the RPC accepted the bytes; it does not mean
    # the cluster processed the transfer. Wait for the requested commitment.
    deadline = time.monotonic() + 90
    levels = {"processed": 0, "confirmed": 1, "finalized": 2}
    required = levels.get(commitment, 2)
    while time.monotonic() < deadline:
        try:
            status_result = await rpc(url, "getSignatureStatuses", [[res], {"searchTransactionHistory": True}])
        except Exception as e:
            raise SolPendingError(f"Solana accepted transaction {res}, but status lookup failed: {e}", res) from e
        status = status_result.get("value", [None])[0]
        if status:
            if status.get("err") is not None:
                raise SolError(f"Solana transaction failed on-chain: {status['err']}")
            observed = levels.get(status.get("confirmationStatus"), -1)
            if observed >= required:
                return res, user_amt
        await asyncio.sleep(2)
    raise SolPendingError(f"Solana accepted transaction {res}, but confirmation is still unknown", res)


def to_lamports(amount) -> int:
    return int((Decimal(str(amount)) * LAMPORTS).to_integral_value())
