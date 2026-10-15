"""
Wallet layer for Time AutoMM.

Everything the Discord side needs from "the blockchain" goes through the
WalletBackend interface below, so the deal flow never touches keys directly.

Modes (config.json -> "wallet" -> "mode"):
  "dryrun"  Fake addresses, fake txids, no real money moves. Payments are
            simulated with the `.simpay` admin command. USE THIS TO TEST THE FLOW.
  "live"    REAL MONEY. Seed phrase comes from env var WALLET_MNEMONIC. See SETUP.md.
"""
import json
import os
import random
import re
import string
import threading
import time

import aiohttp
import asyncio

COINS = {
    "LTC":  {"name": "Litecoin",       "label": "LTC",  "cg": "litecoin", "decimals": 8},
    "SOL":  {"name": "Solana",         "label": "SOL",  "cg": "solana",   "decimals": 9},
    "USDT": {"name": "USDT [BEP-20]",  "label": "USDT", "cg": "tether",   "decimals": 4},
}

_ADDR = {
    "LTC":  re.compile(r"^(t?ltc1[a-z0-9]{20,70}|[LMmn][a-km-zA-HJ-NP-Z1-9]{26,33}|[32][a-km-zA-HJ-NP-Z1-9]{25,34})$"),
    "SOL":  re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$"),
    "USDT": re.compile(r"^0x[a-fA-F0-9]{40}$"),
}


class WalletError(Exception):
    pass


class WalletPendingError(WalletError):
    """A payout was submitted, but chain confirmation is still unknown."""
    def __init__(self, message, txid):
        super().__init__(message)
        self.txid = txid


def validate_address(coin: str, address: str) -> bool:
    """Format check only. It cannot tell whether the address belongs to the right person."""
    return bool(_ADDR[coin].match((address or "").strip()))


def fmt_amount(coin: str, amount: float) -> str:
    return f"{amount:.{COINS[coin]['decimals']}f}"


def calc_fee(config, usd: float) -> float:
    fees = config.get("fees", {})
    if usd < 50:
        return float(fees.get("under_50", 0.0))
    if usd < 250:
        return float(fees.get("under_250", 0.50))
    return float(fees.get("over_250", 1.50))


_price_cache = {}


async def get_price_usd(coin: str) -> float:
    cached = _price_cache.get(coin)
    if cached and time.time() - cached[1] < 60:
        return cached[0]
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={COINS[coin]['cg']}&vs_currencies=usd"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                data = await r.json()
        price = float(data[COINS[coin]["cg"]]["usd"])
    except Exception as e:
        raise WalletError(f"Could not fetch {coin} price: {e}")
    _price_cache[coin] = (price, time.time())
    return price


class WalletBackend:
    mode = "base"

    async def new_deposit_address(self, deal: dict):
        """Return (address, derivation_index) for this deal."""
        raise NotImplementedError

    async def get_received(self, deal: dict):
        """Return {"amount": float, "txid": str|None} once funds are seen (confirmed), else None."""
        raise NotImplementedError

    async def get_unconfirmed_received(self, deal: dict):
        """Return an unconfirmed LTC deposit, when the backend supports it."""
        return None

    async def send(self, deal: dict, to_address: str, amount: float, sweep_rest: bool):
        """
        Send `amount` of the deal's coin from the deal's own address to `to_address`.
        sweep_rest=True: leftover (service fee) may go to the configured treasury address.
        Return {"txid": str, "sent": float}. Raise WalletError on failure.
        """
        raise NotImplementedError


class DryRunWallet(WalletBackend):
    """No real funds. Lets you test every button and embed safely."""
    mode = "dryrun"

    def __init__(self):
        self.received = {}

    def _rand(self, n):
        return "".join(random.choices(string.ascii_letters + string.digits, k=n))

    async def new_deposit_address(self, deal):
        coin = deal["coin"]
        if coin == "LTC":
            return "ltc1q" + "".join(random.choices(string.ascii_lowercase + string.digits, k=38)), 0
        if coin == "USDT":
            return "0x" + "".join(random.choices("0123456789abcdef", k=40)), 0
        return self._rand(44), 0

    async def get_received(self, deal):
        return self.received.get(deal["address"])

    def simulate_payment(self, address, amount):
        self.received[address] = {"amount": amount, "txid": self._rand(64).lower()}

    async def send(self, deal, to_address, amount, sweep_rest):
        return {"txid": self._rand(64).lower(), "sent": amount}


# ---------------- LIVE ----------------

_idx_lock = threading.Lock()
INDEX_FILE = "data/wallet_index.json"


def _next_index() -> int:
    """Each deal gets a fresh derivation index. NEVER delete data/wallet_index.json while deals are open."""
    with _idx_lock:
        os.makedirs("data", exist_ok=True)
        n = 0
        if os.path.exists(INDEX_FILE):
            with open(INDEX_FILE) as f:
                n = json.load(f)["next"]
        # safety net: never go below the highest index any saved deal already used
        try:
            from utils import deals as _deals
            used = [d.get("wallet_index") for d in _deals.all_deals() if d.get("wallet_index") is not None]
            n = max([n] + [u + 1 for u in used])
        except Exception:
            pass
        tmp = INDEX_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"next": n + 1}, f)
        os.replace(tmp, INDEX_FILE)
        return n


class LiveWallet(WalletBackend):
    """Real keys, real chains. One deposit address per deal, derived from WALLET_MNEMONIC."""
    mode = "live"

    def __init__(self, config):
        from utils import hd
        w = config.get("wallet", {})
        mnemonic = os.environ.get("WALLET_MNEMONIC", "").strip()
        try:
            from mnemonic import Mnemonic
            valid_mnemonic = Mnemonic("english").check(mnemonic)
        except ImportError as e:
            raise WalletError("Install the 'mnemonic' package before enabling live wallet mode") from e
        if not valid_mnemonic:
            raise WalletError("WALLET_MNEMONIC is missing or is not a valid English BIP-39 seed phrase")
        self.hd = hd
        self.seed = hd.mnemonic_to_seed(mnemonic, os.environ.get("WALLET_PASSPHRASE", ""))
        self.testnet = w.get("network", "mainnet") == "testnet"
        net = "testnet" if self.testnet else "mainnet"
        rpc = w.get("rpc", {}).get(net, {})
        self.ltc_base = (rpc.get("LTC_EXPLORER") or "https://litecoinspace.org").rstrip("/")
        self.sol_url = rpc.get("SOL") or ("https://api.devnet.solana.com" if self.testnet else "https://api.mainnet-beta.solana.com")
        self.bsc_url = rpc.get("BSC") or "https://bsc-dataseed.binance.org"
        self.usdt = w.get("usdt_contract", {}).get(net) or (
            None if self.testnet else "0x55d398326f99059fF775485246999027B3197955")
        self.treasury = {k: (v or "").strip() for k, v in w.get("treasury", {}).items()}
        conf = w.get("confirmations", {})
        self.ltc_conf = int(conf.get("LTC", 2))
        self.sol_commitment = conf.get("SOL", "finalized")
        self.bsc_conf = int(conf.get("USDT", 15))
        self._bsc = None
        for coin, addr in self.treasury.items():
            if addr and not validate_address(coin, addr):
                raise WalletError(f"treasury address for {coin} is not valid")

    def bsc(self):
        if self._bsc is None:
            if not self.usdt:
                raise WalletError("wallet.usdt_contract is not set for this network")
            from utils.bsc import Bsc
            self._bsc = Bsc(self.bsc_url, self.usdt, os.environ.get("GAS_WALLET_KEY"), self.bsc_conf)
        return self._bsc

    def _keys(self, coin, index):
        if coin == "LTC":
            from utils import ltc
            return ltc.derive(self.seed, index, self.testnet)
        if coin == "SOL":
            from utils import sol
            return sol.derive(self.seed, index)
        from utils import bsc
        priv, addr = bsc.derive(self.seed, index)
        return priv, None, addr

    async def new_deposit_address(self, deal):
        index = await asyncio.to_thread(_next_index)
        return self._keys(deal["coin"], index)[2], index

    async def get_received(self, deal):
        coin, addr = deal["coin"], deal["address"]
        try:
            if coin == "LTC":
                from utils import ltc
                utxos = await ltc.confirmed_utxos(self.ltc_base, addr, self.ltc_conf)
                if not utxos:
                    return None
                return {"amount": sum(u["value"] for u in utxos) / ltc.COIN, "txid": utxos[0]["txid"]}
            if coin == "SOL":
                from utils import sol
                bal = await sol.balance(self.sol_url, addr, self.sol_commitment)
                if bal <= 0:
                    return None
                return {"amount": bal / sol.LAMPORTS, "txid": await sol.latest_signature(self.sol_url, addr)}
            return await self.bsc().received(addr)
        except WalletError:
            raise
        except Exception as e:
            raise WalletError(f"{coin} check failed: {e}")

    async def get_ltc_received_state(self, deal):
        """Check confirmed and unconfirmed LTC outputs with one explorer lookup."""
        try:
            from utils import ltc
            confirmed, pending = await ltc.address_utxos(self.ltc_base, deal["address"], self.ltc_conf)
            seen = ({"amount": sum(u["value"] for u in confirmed) / ltc.COIN,
                     "txid": confirmed[0]["txid"]} if confirmed else None)
            unconfirmed = ({"amount": sum(u["value"] for u in pending) / ltc.COIN,
                            "txid": pending[0]["txid"]} if pending else None)
            return seen, unconfirmed
        except WalletError:
            raise
        except Exception as e:
            raise WalletError(f"LTC check failed: {e}")

    async def get_unconfirmed_received(self, deal):
        if deal["coin"] != "LTC":
            return None
        try:
            from utils import ltc
            utxos = await ltc.pending_utxos(self.ltc_base, deal["address"], self.ltc_conf)
            if not utxos:
                return None
            return {
                "amount": sum(u["value"] for u in utxos) / ltc.COIN,
                "txid": utxos[0]["txid"],
            }
        except WalletError:
            raise
        except Exception as e:
            raise WalletError(f"LTC pending check failed: {e}")

    async def send(self, deal, to_address, amount, sweep_rest):
        coin = deal["coin"]
        if not validate_address(coin, to_address):
            raise WalletError("Invalid destination address")
        priv, pub, own = self._keys(coin, deal["wallet_index"])
        if own != deal["address"]:
            raise WalletError("Derived address does not match the deal address (wrong seed?). Nothing was sent.")
        treasury = self.treasury.get(coin) if sweep_rest else None
        try:
            if coin == "LTC":
                from utils import ltc
                txid, sent = await ltc.send(self.ltc_base, priv, pub, own, to_address, ltc.to_litoshi(amount),
                                            treasury or None, self.ltc_conf, self.testnet)
                return {"txid": txid, "sent": sent / ltc.COIN, "confirmation_pending": True}
            if coin == "SOL":
                from utils import sol
                sig, sent = await sol.send(self.sol_url, priv, pub, own, to_address, sol.to_lamports(amount),
                                           treasury or None, self.sol_commitment)
                return {"txid": sig, "sent": sent / sol.LAMPORTS}
            txid, sent = await self.bsc().send(priv, own, to_address, amount, treasury or None)
            return {"txid": txid, "sent": sent}
        except WalletError:
            raise
        except Exception as e:
            from utils import ltc, sol, bsc
            pending_types = tuple(t for t in (
                getattr(ltc, "LtcPendingError", None),
                getattr(sol, "SolPendingError", None),
                getattr(bsc, "BscPendingError", None),
            ) if t)
            if pending_types and isinstance(e, pending_types):
                raise WalletPendingError(str(e), e.txid) from e
            raise WalletError(f"{coin} send failed: {e}") from e


_wallet = None


def get_wallet(config) -> WalletBackend:
    global _wallet
    if _wallet is None:
        mode = config.get("wallet", {}).get("mode", "dryrun")
        _wallet = LiveWallet(config) if mode == "live" else DryRunWallet()
    return _wallet
