"""
Prints the deposit address your seed gives for deal #0 on each coin, plus the gas wallet address.
Import the SAME seed into Electrum-LTC / Phantom / MetaMask and confirm these addresses match
BEFORE you ever send real money. Run from the bot folder:  python tools/check_wallet.py [index]
"""
import os
import sys

sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv()
from utils import hd, ltc, sol, bsc

idx = int(sys.argv[1]) if len(sys.argv) > 1 else 0
seed = hd.mnemonic_to_seed(os.environ["WALLET_MNEMONIC"], os.environ.get("WALLET_PASSPHRASE", ""))
print("LTC  (m/84'/2'/0'/0/%d)      mainnet:" % idx, ltc.derive(seed, idx, False)[2])
print("LTC  (m/84'/1'/0'/0/%d)      testnet:" % idx, ltc.derive(seed, idx, True)[2])
print("SOL  (m/44'/501'/%d'/0')     :" % idx, sol.derive(seed, idx)[2])
print("BSC  (m/44'/60'/0'/0/%d)     :" % idx, bsc.derive(seed, idx)[1])
if os.environ.get("GAS_WALLET_KEY"):
    from eth_account import Account
    print("Gas wallet (send a little BNB here):", Account.from_key(os.environ["GAS_WALLET_KEY"]).address)
