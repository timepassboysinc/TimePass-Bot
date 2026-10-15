"""
Litecoin: native segwit (P2WPKH) deposit addresses, UTXO scanning via an Esplora-style
explorer API (litecoinspace.org is mempool.space-compatible), manual BIP143 signing.

Explorer endpoints used (verify they respond before going live, see SETUP.md):
  GET  {base}/api/address/{addr}/utxo
  GET  {base}/api/blocks/tip/height
  GET  {base}/api/v1/fees/recommended
  POST {base}/api/tx            (raw tx hex as body)
"""
import math
import asyncio
import json
import os
import struct
import time
from decimal import Decimal
from urllib.parse import quote, urlparse

import aiohttp
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    Prehashed, decode_dss_signature, encode_dss_signature)

from utils import hd

COIN = 10 ** 8
DUST = 10_000  # litoshi; outputs smaller than this are not worth creating
_ORDER = hd.N


class LtcError(Exception):
    pass


class LtcPendingError(LtcError):
    def __init__(self, message, txid):
        super().__init__(message)
        self.txid = txid


def derive(seed: bytes, index: int, testnet: bool):
    coin_type = 1 if testnet else 2
    priv = hd.derive_secp(seed, [hd.hard(84), hd.hard(coin_type), hd.hard(0), 0, index])
    pub = hd.secp_pub(priv)
    hrp = "tltc" if testnet else "ltc"
    return priv, pub, hd.segwit_encode(hrp, 0, hd.hash160(pub))


def address_to_script(addr: str, testnet: bool) -> bytes:
    """scriptPubKey for any standard LTC destination address."""
    hrp = "tltc" if testnet else "ltc"
    try:
        ver, prog = hd.segwit_decode(hrp, addr)
        op = b"\x00" if ver == 0 else bytes([0x50 + ver])
        return op + bytes([len(prog)]) + prog
    except Exception:
        pass
    try:
        raw = hd.b58check_decode(addr)
    except Exception:
        raise LtcError("Invalid Litecoin address")
    version, h = raw[0], raw[1:]
    p2pkh = {0x6F} if testnet else {0x30}
    p2sh = {0x3A, 0xC4} if testnet else {0x32, 0x05}
    if len(h) != 20:
        raise LtcError("Invalid Litecoin address")
    if version in p2pkh:
        return b"\x76\xa9\x14" + h + b"\x88\xac"
    if version in p2sh:
        return b"\xa9\x14" + h + b"\x87"
    raise LtcError("Address is for a different network")


# ---------------- explorer ----------------

def _is_nownodes(base):
    return urlparse(base).hostname == "ltcbook.nownodes.io"


def _api_headers(base):
    if not _is_nownodes(base):
        return {}
    api_key = os.environ.get("LTC_API_KEY", "").strip()
    if not api_key:
        raise LtcError("LTC_API_KEY is missing from .env for the NOWNodes Litecoin endpoint")
    return {"api-key": api_key}


async def _get(session, url, timeout=20):
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    async with session.get(url, headers=_api_headers(base), timeout=aiohttp.ClientTimeout(total=timeout)) as r:
        if r.status != 200:
            body = (await r.text())[:240]
            raise LtcError(f"Litecoin API returned HTTP {r.status}: {body}")
        return await r.json()


async def confirmed_utxos(base, addr, min_conf):
    """Return UTXOs for an address with at least min_conf confirmations."""
    async with aiohttp.ClientSession() as s:
        if _is_nownodes(base):
            url = f"{base}/api/v2/utxo/{quote(addr, safe='')}?confirmed=true"
            rows = await _get(s, url)
            if not isinstance(rows, list):
                raise LtcError("NOWNodes returned an unexpected Litecoin UTXO response")
            out = []
            for row in rows:
                if int(row.get("confirmations", 0)) < min_conf:
                    continue
                txid = row.get("txid")
                vout = row.get("vout", row.get("n"))
                if not txid or vout is None or row.get("value") is None:
                    raise LtcError("NOWNodes returned a UTXO missing txid, vout, or value")
                out.append({"txid": txid, "vout": int(vout), "value": int(row["value"])})
            return out

        utxos = await _get(s, f"{base}/api/address/{addr}/utxo")
        if not utxos:
            return []
        # Esplora returns the tip height as plain text, unlike its JSON endpoints.
        async with s.get(f"{base}/api/blocks/tip/height", timeout=aiohttp.ClientTimeout(total=15)) as r:
            if r.status != 200:
                raise LtcError(f"explorer {r.status} while reading tip height")
            try:
                tip = int((await r.text()).strip())
            except ValueError as e:
                raise LtcError("explorer returned an invalid tip height") from e
    out = []
    for u in utxos:
        st = u.get("status", {})
        if st.get("confirmed") and tip - st["block_height"] + 1 >= min_conf:
            out.append({"txid": u["txid"], "vout": u["vout"], "value": int(u["value"])})
    return out


async def pending_utxos(base, addr, min_conf):
    """Return UTXOs below the required confirmation count for pending-deposit notices."""
    async with aiohttp.ClientSession() as s:
        if _is_nownodes(base):
            # Blockbook returns confirmed and unconfirmed outputs by default.
            url = f"{base}/api/v2/utxo/{quote(addr, safe='')}"
            async with s.get(url, headers=_api_headers(base), timeout=aiohttp.ClientTimeout(total=20)) as r:
                if r.status == 404:
                    return []
                if r.status != 200:
                    body = (await r.text())[:240]
                    raise LtcError(f"Litecoin API returned HTTP {r.status}: {body}")
                rows = await r.json()
            if not isinstance(rows, list):
                raise LtcError("NOWNodes returned an unexpected Litecoin UTXO response")
            return [
                {"txid": row["txid"], "vout": int(row.get("vout", row.get("n", 0))),
                 "value": int(row["value"]), "confirmations": int(row.get("confirmations", 0))}
                for row in rows
                if int(row.get("confirmations", 0)) < min_conf and row.get("txid") and row.get("value") is not None
            ]

        utxos = await _get(s, f"{base}/api/address/{addr}/utxo")
        if not utxos:
            return []
        async with s.get(f"{base}/api/blocks/tip/height", timeout=aiohttp.ClientTimeout(total=15)) as r:
            if r.status != 200:
                raise LtcError(f"explorer {r.status} while reading tip height")
            try:
                tip = int((await r.text()).strip())
            except ValueError as e:
                raise LtcError("explorer returned an invalid tip height") from e
    out = []
    for u in utxos:
        status = u.get("status", {})
        confirmations = tip - status["block_height"] + 1 if status.get("confirmed") else 0
        if confirmations < min_conf and u.get("txid") and u.get("value") is not None:
            out.append({"txid": u["txid"], "vout": u["vout"],
                        "value": int(u["value"]), "confirmations": confirmations})
    return out


async def address_utxos(base, addr, min_conf):
    """Fetch an address once and split its outputs into confirmed/pending sets."""
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
        if _is_nownodes(base):
            rows = await _get(s, f"{base}/api/v2/utxo/{quote(addr, safe='')}", timeout=10)
            if not isinstance(rows, list):
                raise LtcError("NOWNodes returned an unexpected Litecoin UTXO response")
            confirmed, pending = [], []
            for row in rows:
                if not row.get("txid") or row.get("value") is None:
                    continue
                out = {"txid": row["txid"], "vout": int(row.get("vout", row.get("n", 0))),
                       "value": int(row["value"])}
                (confirmed if int(row.get("confirmations", 0)) >= min_conf else pending).append(out)
            return confirmed, pending

        rows = await _get(s, f"{base}/api/address/{addr}/utxo", timeout=10)
        if not rows:
            return [], []
        confirmed_rows = [row for row in rows if row.get("status", {}).get("confirmed")]
        tip = None
        if confirmed_rows:
            async with s.get(f"{base}/api/blocks/tip/height") as response:
                if response.status != 200:
                    raise LtcError(f"explorer {response.status} while reading tip height")
                try:
                    tip = int((await response.text()).strip())
                except ValueError as exc:
                    raise LtcError("explorer returned an invalid tip height") from exc
        confirmed, pending = [], []
        for row in rows:
            status = row.get("status", {})
            confirmations = tip - status["block_height"] + 1 if status.get("confirmed") and tip is not None else 0
            out = {"txid": row["txid"], "vout": int(row["vout"]), "value": int(row["value"])}
            (confirmed if confirmations >= min_conf else pending).append(out)
        return confirmed, pending


async def fee_rate(base):
    """sat/vB. Falls back to a safe default if the explorer can't tell us."""
    # Blockbook's estimate-fee unit differs from the litoshis/vbyte expected here.
    if _is_nownodes(base):
        return 5
    try:
        async with aiohttp.ClientSession() as s:
            f = await _get(s, f"{base}/api/v1/fees/recommended")
        return max(2, math.ceil(float(f.get("halfHourFee", 5))))
    except Exception:
        return 5


async def broadcast(base, raw_hex):
    async with aiohttp.ClientSession() as s:
        if _is_nownodes(base):
            # Blockbook also supports the raw transaction as a URL path segment.
            # This avoids body quoting/content-type ambiguity at the gateway.
            url = f"{base}/api/v2/sendtx/{quote(raw_hex, safe='')}"
            async with s.get(url, headers=_api_headers(base), timeout=aiohttp.ClientTimeout(total=30)) as r:
                body = (await r.text()).strip()
                if r.status != 200:
                    raise LtcError(f"Litecoin broadcast API returned HTTP {r.status}: {body[:240]}")
                try:
                    response = json.loads(body)
                except json.JSONDecodeError:
                    response = body.strip('"')
                txid = (response.get("result") or response.get("txid")) if isinstance(response, dict) else response
                if not isinstance(txid, str) or len(txid) != 64:
                    raise LtcError("NOWNodes returned no transaction ID after broadcast")
                return txid

        async with s.post(f"{base}/api/tx", data=raw_hex, timeout=aiohttp.ClientTimeout(total=20)) as r:
            body = (await r.text()).strip()
            if r.status != 200:
                raise LtcError(f"broadcast failed: {body}")
            return body  # txid


async def wait_confirmed(base, txid, min_conf, timeout=90):
    """Wait for explorer confirmation, preserving ambiguous broadcasts for manual review."""
    deadline = time.monotonic() + timeout
    async with aiohttp.ClientSession() as s:
        while time.monotonic() < deadline:
            if _is_nownodes(base):
                tx = await _get(s, f"{base}/api/v2/tx/{txid}")
                if int(tx.get("confirmations", 0)) >= min_conf:
                    return
                await asyncio.sleep(3)
                continue
            status = await _get(s, f"{base}/api/tx/{txid}/status")
            if status.get("confirmed"):
                async with s.get(f"{base}/api/blocks/tip/height", timeout=aiohttp.ClientTimeout(total=15)) as r:
                    if r.status != 200:
                        raise LtcPendingError(f"Litecoin payout {txid} is confirmed but its confirmation count is unknown", txid)
                    tip = int((await r.text()).strip())
                if tip - int(status["block_height"]) + 1 >= min_conf:
                    return
            await asyncio.sleep(3)
    raise LtcPendingError(f"Litecoin accepted payout {txid}, but confirmation is still unknown", txid)


# ---------------- transaction building ----------------

def _varint(n):
    if n < 0xFD:
        return bytes([n])
    if n <= 0xFFFF:
        return b"\xfd" + struct.pack("<H", n)
    return b"\xfe" + struct.pack("<I", n)


def _est_fee(n_in, out_scripts, rate):
    vsize = 11 + 68 * n_in + sum(9 + len(s) for s in out_scripts)  # conservative
    return math.ceil(vsize * rate)


def _sign(priv: bytes, digest: bytes) -> bytes:
    key = ec.derive_private_key(int.from_bytes(priv, "big"), ec.SECP256K1())
    der = key.sign(digest, ec.ECDSA(Prehashed(hashes.SHA256())))
    r, s = decode_dss_signature(der)
    if s > _ORDER // 2:  # low-S, required by relay policy
        s = _ORDER - s
    return encode_dss_signature(r, s) + b"\x01"  # SIGHASH_ALL


def build_tx(priv, pub, utxos, outputs):
    """outputs = [(script, value)]. All inputs are P2WPKH owned by `pub`. Returns raw tx bytes."""
    pkh = hd.hash160(pub)
    version, locktime, seq = struct.pack("<I", 2), b"\x00" * 4, b"\xff\xff\xff\xff"
    ins = [(bytes.fromhex(u["txid"])[::-1], struct.pack("<I", u["vout"]), u["value"]) for u in utxos]
    hash_prevouts = hd.dsha256(b"".join(t + v for t, v, _ in ins))
    hash_seq = hd.dsha256(seq * len(ins))
    outs_ser = b"".join(struct.pack("<q", val) + _varint(len(sc)) + sc for sc, val in outputs)
    hash_outputs = hd.dsha256(outs_ser)
    script_code = b"\x19\x76\xa9\x14" + pkh + b"\x88\xac"

    witnesses = b""
    for txid_le, vout, value in ins:
        preimage = (version + hash_prevouts + hash_seq + txid_le + vout + script_code +
                    struct.pack("<q", value) + seq + hash_outputs + locktime + struct.pack("<I", 1))
        sig = _sign(priv, hd.dsha256(preimage))
        witnesses += b"\x02" + _varint(len(sig)) + sig + _varint(len(pub)) + pub

    inputs_ser = b"".join(t + v + b"\x00" + seq for t, v, _ in ins)
    return (version + b"\x00\x01" + _varint(len(ins)) + inputs_ser +
            _varint(len(outputs)) + outs_ser + witnesses + locktime)


async def send(base, priv, pub, own_addr, to_addr, want_litoshi, treasury, min_conf, testnet):
    """
    Pay `want_litoshi` to `to_addr`. Whatever is left (after the network fee) goes to `treasury`
    if given and big enough, otherwise back to the deal address. Network fee is taken from the
    leftover first; if there is no leftover, the receiver's amount is reduced by the fee.
    Returns (txid, sent_litoshi).
    """
    utxos = await confirmed_utxos(base, own_addr, min_conf)
    if not utxos:
        raise LtcError("No confirmed funds found at the deal address")
    total = sum(u["value"] for u in utxos)
    rate = await fee_rate(base)
    user_script = address_to_script(to_addr, testnet)
    own_script = b"\x00\x14" + hd.hash160(pub)
    rest_script = address_to_script(treasury, testnet) if treasury else own_script

    fee2 = _est_fee(len(utxos), [user_script, rest_script], rate)
    rest = total - want_litoshi - fee2
    if rest >= DUST:
        outputs = [(user_script, want_litoshi), (rest_script, rest)]
        sent = want_litoshi
    else:
        fee1 = _est_fee(len(utxos), [user_script], rate)
        sent = min(want_litoshi, total - fee1)
        if sent <= DUST:
            raise LtcError("Amount is too small to cover the network fee")
        outputs = [(user_script, sent)]

    raw = build_tx(priv, pub, utxos, outputs)
    txid = await broadcast(base, raw.hex())
    # Return as soon as the explorer accepts the broadcast. The bot records this
    # as payout_pending and shows the txid/close button immediately; it must not
    # hold the ticket open while polling for block confirmations.
    return txid, sent


def to_litoshi(amount) -> int:
    return int((Decimal(str(amount)) * COIN).to_integral_value())
