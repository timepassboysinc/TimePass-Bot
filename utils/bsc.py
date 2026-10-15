"""
BNB Smart Chain: USDT (BEP-20) deposit addresses (m/44'/60'/0'/0/i, same as MetaMask).

Moving a token costs BNB for gas, but a deal address only ever holds USDT. So before each payout
the bot tops the deal address up with a little BNB from a separate "gas wallet"
(private key in env var GAS_WALLET_KEY, keep only a small amount of BNB in it).

web3 is synchronous here and is run in a worker thread so the bot never blocks.
"""
import asyncio
import time
from decimal import Decimal

from utils import hd

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ABI = [
    {"constant": True, "inputs": [{"name": "a", "type": "address"}], "name": "balanceOf",
     "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "decimals",
     "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
    {"constant": False, "inputs": [{"name": "to", "type": "address"}, {"name": "v", "type": "uint256"}],
     "name": "transfer", "outputs": [{"name": "", "type": "bool"}], "type": "function"},
]


class BscError(Exception):
    pass


class BscPendingError(BscError):
    def __init__(self, message, txid):
        super().__init__(message)
        self.txid = txid


def derive(seed: bytes, index: int):
    from eth_account import Account
    priv = hd.derive_secp(seed, [hd.hard(44), hd.hard(60), hd.hard(0), 0, index])
    return priv, Account.from_key(priv).address


class Bsc:
    def __init__(self, rpc_url, usdt_contract, gas_key=None, confirmations=15):
        from web3 import Web3
        self.Web3 = Web3
        self.w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 20}))
        self.token = self.w3.eth.contract(address=Web3.to_checksum_address(usdt_contract), abi=ABI)
        self.gas_key = gas_key
        self.confirmations = confirmations
        self._dec = None

    # ---- helpers (sync) ----
    def decimals(self):
        if self._dec is None:
            self._dec = int(self.token.functions.decimals().call())
        return self._dec

    def _raw(self, signed):
        return getattr(signed, "raw_transaction", None) or signed.rawTransaction

    def _send_signed(self, tx, key):
        signed = self.w3.eth.account.sign_transaction(tx, key)
        raw = self._raw(signed)
        expected = self.Web3.keccak(raw).hex()
        try:
            h = self.w3.eth.send_raw_transaction(raw).hex()
        except Exception as e:
            # A transport error can happen after the node accepted the bytes.
            raise BscPendingError(f"Transaction submission result is unknown ({expected})", expected) from e
        return h if h.startswith("0x") else "0x" + h

    def _wait_confirmed(self, txid):
        try:
            receipt = self.w3.eth.wait_for_transaction_receipt(txid, timeout=90)
        except Exception as e:
            raise BscPendingError(f"Transaction {txid} was submitted, but confirmation is unknown", txid) from e
        if int(receipt.get("status", 0)) != 1:
            raise BscError(f"Transaction {txid} failed on-chain")
        target = int(receipt["blockNumber"]) + max(0, self.confirmations - 1)
        deadline = time.monotonic() + 90
        try:
            while self.w3.eth.block_number < target:
                if time.monotonic() >= deadline:
                    raise BscPendingError(f"Transaction {txid} was included but has not reached {self.confirmations} confirmations", txid)
                time.sleep(2)
        except BscPendingError:
            raise
        except Exception as e:
            raise BscPendingError(f"Transaction {txid} was included, but confirmation lookup failed", txid) from e
        return receipt

    # ---- deposits ----
    def received_sync(self, addr):
        latest = self.w3.eth.block_number
        at = max(0, latest - self.confirmations)
        bal = self.token.functions.balanceOf(addr).call(block_identifier=at)
        if bal <= 0:
            return None
        txid = None
        try:  # best effort, only for display
            logs = self.w3.eth.get_logs({
                "fromBlock": max(0, latest - 4000), "toBlock": latest, "address": self.token.address,
                "topics": [TRANSFER_TOPIC, None, "0x" + addr[2:].lower().rjust(64, "0")]})
            if logs:
                txid = logs[-1]["transactionHash"].hex()
                txid = txid if txid.startswith("0x") else "0x" + txid
        except Exception:
            pass
        return {"amount": float(Decimal(bal) / Decimal(10 ** self.decimals())), "txid": txid}

    # ---- payouts ----
    def send_sync(self, priv, own, to_addr, want_units, treasury, sweep_min_units):
        w3 = self.w3
        to_addr = self.Web3.to_checksum_address(to_addr)
        bal = self.token.functions.balanceOf(own).call()
        if bal <= 0:
            raise BscError("No funds at the deal address")
        pay = min(want_units, bal)
        rest = bal - pay
        sweep = bool(treasury) and rest >= sweep_min_units

        gas_price = w3.eth.gas_price
        gas_each = int(self.token.functions.transfer(to_addr, pay).estimate_gas({"from": own}) * 1.25) + 5000
        n_tx = 2 if sweep else 1
        need = gas_each * gas_price * n_tx
        have = w3.eth.get_balance(own)
        if have < need:
            self._top_up(own, need - have + gas_price * 21000)

        chain_id = w3.eth.chain_id
        nonce = w3.eth.get_transaction_count(own)
        payments = [(to_addr, pay)] + ([(self.Web3.to_checksum_address(treasury), rest)] if sweep else [])
        txid = None
        for i, (dest, amt) in enumerate(payments):
            tx = self.token.functions.transfer(dest, amt).build_transaction({
                "from": own, "nonce": nonce + i, "gas": gas_each, "gasPrice": gas_price, "chainId": chain_id})
            sent_txid = self._send_signed(tx, priv)
            if i == 0:
                txid = sent_txid
                self._wait_confirmed(sent_txid)
            else:
                # A failed fee sweep must not make the already-confirmed customer
                # payout look retryable; that could pay the customer twice.
                try:
                    self._wait_confirmed(sent_txid)
                except Exception as e:
                    print(f"[BSC] Treasury sweep confirmation unknown ({sent_txid}): {e}")
        return txid, float(Decimal(pay) / Decimal(10 ** self.decimals()))

    def _top_up(self, to, wei):
        if not self.gas_key:
            raise BscError("GAS_WALLET_KEY is not set, so the bot can't pay BNB gas for the payout")
        from eth_account import Account
        w3 = self.w3
        gas_acct = Account.from_key(self.gas_key)
        if w3.eth.get_balance(gas_acct.address) < wei + 21000 * w3.eth.gas_price:
            raise BscError("The gas wallet is out of BNB. Top it up and try again")
        tx = {"to": to, "value": int(wei), "gas": 21000, "gasPrice": w3.eth.gas_price,
              "nonce": w3.eth.get_transaction_count(gas_acct.address), "chainId": w3.eth.chain_id}
        h = self._send_signed(tx, self.gas_key)
        w3.eth.wait_for_transaction_receipt(h, timeout=90)
        for _ in range(20):  # wait until the node we read from sees the BNB
            if w3.eth.get_balance(to) >= wei:
                return
            time.sleep(1)

    # ---- async wrappers ----
    async def received(self, addr):
        return await asyncio.to_thread(self.received_sync, addr)

    async def send(self, priv, own, to_addr, want_amount, treasury):
        dec = await asyncio.to_thread(self.decimals)
        want_units = int((Decimal(str(want_amount)) * Decimal(10 ** dec)).to_integral_value())
        sweep_min = 1  # sweep any positive token remainder when a treasury is configured
        return await asyncio.to_thread(self.send_sync, priv, own, to_addr, want_units, treasury, sweep_min)
