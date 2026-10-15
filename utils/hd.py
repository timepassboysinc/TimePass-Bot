"""
Key derivation + address encoding helpers (only needs the `cryptography` package).

  BIP39 seed  -> BIP32 (secp256k1: LTC, BSC)  /  SLIP-10 (ed25519: SOL)

Standard derivation paths are used, so you can always recover funds by importing the
same mnemonic into a normal wallet app (Electrum-LTC, Phantom, MetaMask), even if this bot is lost:
  LTC  m/84'/2'/0'/0/i   (native segwit, ltc1q...)   testnet: coin type 1
  SOL  m/44'/501'/i'/0'  (Phantom style)
  BSC  m/44'/60'/0'/0/i  (MetaMask style)
"""
import hashlib
import hmac
import struct

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519

N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
H = 0x80000000  # hardened offset


def hard(i):
    return i + H


# ---------------- BIP39 / BIP32 / SLIP-10 ----------------

def mnemonic_to_seed(mnemonic: str, passphrase: str = "") -> bytes:
    m = " ".join(mnemonic.split())
    return hashlib.pbkdf2_hmac("sha512", m.encode(), ("mnemonic" + passphrase).encode(), 2048)


def secp_pub(priv: bytes) -> bytes:
    key = ec.derive_private_key(int.from_bytes(priv, "big"), ec.SECP256K1())
    return key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.CompressedPoint)


def bip32_master(seed: bytes):
    i = hmac.new(b"Bitcoin seed", seed, hashlib.sha512).digest()
    return i[:32], i[32:]


def bip32_child(k: bytes, c: bytes, index: int):
    data = (b"\x00" + k if index >= H else secp_pub(k)) + struct.pack(">I", index)
    i = hmac.new(c, data, hashlib.sha512).digest()
    il = int.from_bytes(i[:32], "big")
    child = (il + int.from_bytes(k, "big")) % N
    if il >= N or child == 0:
        raise ValueError("invalid child key, use the next index")
    return child.to_bytes(32, "big"), i[32:]


def derive_secp(seed: bytes, path) -> bytes:
    k, c = bip32_master(seed)
    for idx in path:
        k, c = bip32_child(k, c, idx)
    return k


def derive_ed25519(seed: bytes, path) -> bytes:
    """SLIP-0010 ed25519 (hardened only). Returns the 32-byte private seed."""
    i = hmac.new(b"ed25519 seed", seed, hashlib.sha512).digest()
    k, c = i[:32], i[32:]
    for idx in path:
        if idx < H:
            raise ValueError("ed25519 needs hardened indices")
        i = hmac.new(c, b"\x00" + k + struct.pack(">I", idx), hashlib.sha512).digest()
        k, c = i[:32], i[32:]
    return k


def ed25519_pub(priv: bytes) -> bytes:
    return ed25519.Ed25519PrivateKey.from_private_bytes(priv).public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def ed25519_sign(priv: bytes, msg: bytes) -> bytes:
    return ed25519.Ed25519PrivateKey.from_private_bytes(priv).sign(msg)


# ---------------- hashes ----------------

def sha256(b):
    return hashlib.sha256(b).digest()


def dsha256(b):
    return sha256(sha256(b))


_M = 0xFFFFFFFF
_R = ([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15] + [7, 4, 13, 1, 10, 6, 15, 3, 12, 0, 9, 5, 2, 14, 11, 8] +
      [3, 10, 14, 4, 9, 15, 8, 1, 2, 7, 0, 6, 13, 11, 5, 12] + [1, 9, 11, 10, 0, 8, 12, 4, 13, 3, 7, 15, 14, 5, 6, 2] +
      [4, 0, 5, 9, 7, 12, 2, 10, 14, 1, 3, 8, 11, 6, 15, 13])
_RP = ([5, 14, 7, 0, 9, 2, 11, 4, 13, 6, 15, 8, 1, 10, 3, 12] + [6, 11, 3, 7, 0, 13, 5, 10, 14, 15, 8, 12, 4, 9, 1, 2] +
       [15, 5, 1, 3, 7, 14, 6, 9, 11, 8, 12, 2, 10, 0, 4, 13] + [8, 6, 4, 1, 3, 11, 15, 0, 5, 12, 2, 13, 9, 7, 10, 14] +
       [12, 15, 10, 4, 1, 5, 8, 7, 6, 2, 13, 14, 0, 3, 9, 11])
_S = ([11, 14, 15, 12, 5, 8, 7, 9, 11, 13, 14, 15, 6, 7, 9, 8] + [7, 6, 8, 13, 11, 9, 7, 15, 7, 12, 15, 9, 11, 7, 13, 12] +
      [11, 13, 6, 7, 14, 9, 13, 15, 14, 8, 13, 6, 5, 12, 7, 5] + [11, 12, 14, 15, 14, 15, 9, 8, 9, 14, 5, 6, 8, 6, 5, 12] +
      [9, 15, 5, 11, 6, 8, 13, 12, 5, 12, 13, 14, 11, 8, 5, 6])
_SP = ([8, 9, 9, 11, 13, 15, 15, 5, 7, 7, 8, 11, 14, 14, 12, 6] + [9, 13, 15, 7, 12, 8, 9, 11, 7, 7, 12, 7, 6, 15, 13, 11] +
       [9, 7, 15, 11, 8, 6, 6, 14, 12, 13, 5, 14, 13, 13, 7, 5] + [15, 5, 8, 11, 14, 14, 6, 14, 6, 9, 12, 9, 12, 5, 15, 8] +
       [8, 5, 12, 9, 12, 5, 14, 6, 8, 13, 6, 5, 15, 13, 11, 11])
_K = [0x00000000, 0x5A827999, 0x6ED9EBA1, 0x8F1BBCDC, 0xA953FD4E]
_KP = [0x50A28BE6, 0x5C4DD124, 0x6D703EF3, 0x7A6D76E9, 0x00000000]


def _rol(x, n):
    return ((x << n) | (x >> (32 - n))) & _M


def _f(r, x, y, z):
    if r == 0:
        return x ^ y ^ z
    if r == 1:
        return (x & y) | (~x & _M & z)
    if r == 2:
        return (x | (~y & _M)) ^ z
    if r == 3:
        return (x & z) | (y & (~z & _M))
    return x ^ (y | (~z & _M))


def _ripemd160_py(data: bytes) -> bytes:
    h = [0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476, 0xC3D2E1F0]
    msg = data + b"\x80" + b"\x00" * ((55 - len(data)) % 64) + struct.pack("<Q", len(data) * 8)
    for off in range(0, len(msg), 64):
        x = struct.unpack("<16I", msg[off:off + 64])
        al, bl, cl, dl, el = h
        ar, br, cr, dr, er = h
        for j in range(80):
            rnd = j // 16
            t = (_rol((al + _f(rnd, bl, cl, dl) + x[_R[j]] + _K[rnd]) & _M, _S[j]) + el) & _M
            al, el, dl, cl, bl = el, dl, _rol(cl, 10), bl, t
            t = (_rol((ar + _f(4 - rnd, br, cr, dr) + x[_RP[j]] + _KP[rnd]) & _M, _SP[j]) + er) & _M
            ar, er, dr, cr, br = er, dr, _rol(cr, 10), br, t
        t = (h[1] + cl + dr) & _M
        h = [t, (h[2] + dl + er) & _M, (h[3] + el + ar) & _M, (h[4] + al + br) & _M, (h[0] + bl + cr) & _M]
    return b"".join(struct.pack("<I", v) for v in h)


def ripemd160(data: bytes) -> bytes:
    try:
        hh = hashlib.new("ripemd160")
        hh.update(data)
        return hh.digest()
    except Exception:
        return _ripemd160_py(data)


def hash160(b: bytes) -> bytes:
    return ripemd160(sha256(b))


# ---------------- base58 ----------------

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    pad = len(b) - len(b.lstrip(b"\x00"))
    return "1" * pad + out


def b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        n = n * 58 + _B58.index(ch)  # ValueError on bad char
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(s) - len(s.lstrip("1"))
    return b"\x00" * pad + body


def b58check_decode(s: str) -> bytes:
    raw = b58decode(s)
    if len(raw) < 5 or dsha256(raw[:-4])[:4] != raw[-4:]:
        raise ValueError("bad base58 checksum")
    return raw[:-4]


# ---------------- bech32 / bech32m (segwit) ----------------

_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_BECH32M = 0x2BC830A3


def _polymod(values):
    gen = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for v in values:
        top = chk >> 25
        chk = ((chk & 0x1FFFFFF) << 5) ^ v
        for i in range(5):
            if (top >> i) & 1:
                chk ^= gen[i]
    return chk


def _hrp_expand(hrp):
    return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]


def _convertbits(data, frombits, tobits, pad=True):
    acc = bits = 0
    ret = []
    maxv = (1 << tobits) - 1
    for v in data:
        acc = (acc << frombits) | v
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            ret.append((acc >> bits) & maxv)
    if pad:
        if bits:
            ret.append((acc << (tobits - bits)) & maxv)
    elif bits >= frombits or ((acc << (tobits - bits)) & maxv):
        raise ValueError("invalid padding")
    return ret


def segwit_encode(hrp: str, ver: int, prog: bytes) -> str:
    data = [ver] + _convertbits(prog, 8, 5)
    const = 1 if ver == 0 else _BECH32M
    pm = _polymod(_hrp_expand(hrp) + data + [0] * 6) ^ const
    chk = [(pm >> 5 * (5 - i)) & 31 for i in range(6)]
    return hrp + "1" + "".join(_CHARSET[d] for d in data + chk)


def segwit_decode(hrp: str, addr: str):
    """Return (witness_version, program_bytes). Raises ValueError if invalid."""
    if addr.lower() != addr and addr.upper() != addr:
        raise ValueError("mixed case")
    addr = addr.lower()
    pos = addr.rfind("1")
    if pos < 1 or addr[:pos] != hrp or len(addr) - pos < 7:
        raise ValueError("bad hrp/length")
    data = [_CHARSET.index(c) for c in addr[pos + 1:]]
    ver = data[0]
    const = 1 if ver == 0 else _BECH32M
    if _polymod(_hrp_expand(hrp) + data) != const:
        raise ValueError("bad checksum")
    prog = bytes(_convertbits(data[1:-6], 5, 8, False))
    if not 2 <= len(prog) <= 40 or ver > 16 or (ver == 0 and len(prog) not in (20, 32)):
        raise ValueError("bad program")
    return ver, prog
