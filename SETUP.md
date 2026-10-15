# Going live: step by step

Do these IN ORDER. Do not skip the testnet round.

## 0. What this bot does with money
- Every deal gets its own deposit address, derived from ONE seed phrase (24 words).
- The money sits in that deal's address until release (-> receiver) or cancel (-> refund to sender).
- Your service fee (the part above the deal amount) is swept to your **treasury** addresses.
- Whoever has the seed phrase controls ALL open deals' funds. Protect it like a bank vault key.

## 1. Install
```
pip install -r requirements.txt
```

## 2. Create the seed (offline, once)
```
pip install mnemonic
python tools/gen_seed.py
```
- Write the 24 words on paper. Keep 2 copies in 2 places. Never photo it, never paste it into chat/cloud.
- Put it in `.env` (copy `.env.example`): `WALLET_MNEMONIC=...`   `chmod 600 .env`. Never put it in config.json or share the zip with it inside.

## 3. Prove the addresses are right
```
python tools/check_wallet.py
```
Import the SAME 24 words into Electrum-LTC, Phantom and MetaMask (on a throwaway device/profile) and check
that the first address shown matches the one printed (LTC native segwit, SOL account 1, BSC account 1).
If they match, you can always recover funds with normal wallet apps, even if this bot is gone.

## 4. Gas wallet (for USDT payouts only)
- Make a SEPARATE new BSC wallet. Put a small amount of BNB in it (e.g. 0.02-0.05 BNB).
- Put its private key (hex) in `.env` as `GAS_WALLET_KEY`. The address is printed by check_wallet.py.
- Top it up when it runs low. If it's empty, USDT payouts fail with a clear message (nothing is lost).

## 5. Treasury addresses (where your fees go)
- These are your public receiving addresses: one Litecoin address, one Solana address, and one BNB Smart Chain address for USDT. The bot sends service-fee remainder here after a deal is released; it does not send this portion to the receiver.
- Use addresses you control, ideally from wallets separate from the bot escrow wallet. Put them in `config.json -> wallet.treasury` under `LTC`, `SOL`, and `USDT`.
- The bot does not need the private keys for these treasury addresses just to receive fees. Network fees can reduce the amount swept; dust or very small amounts can remain at the deal address or be consumed as network fee.

## 6. Bot escrow wallet credentials
- The bot needs a dedicated BIP-39 seed phrase in its local `.env` as `WALLET_MNEMONIC`. It uses that seed to derive unique deposit addresses and sign receiver payouts/refunds. Do not use your main personal wallet seed.
- For USDT on BNB Smart Chain, the bot also needs a separate low-balance BNB gas wallet key in `.env` as `GAS_WALLET_KEY`. That key pays network gas; it is not the treasury address.
- Never send these credentials in chat or put them in `config.json`. Keep `.env` private and backed up securely.

## 7. Check the explorer works (LTC)
```
curl https://litecoinspace.org/api/blocks/tip/height
curl https://litecoinspace.org/api/v1/fees/recommended
```
The first returns a block height as plain text; the second returns JSON. If either fails, change `rpc.mainnet.LTC_EXPLORER` to another Esplora-compatible explorer.
For heavy use, run your own node or use a paid RPC. Public endpoints rate-limit.

## 8. TESTNET ROUND (mandatory)
In `config.json -> wallet`: `"mode": "live"`, `"network": "testnet"`.
- LTC testnet: get coins from a testnet faucet. Solana: `solana airdrop` on devnet.
- BSC testnet: set `usdt_contract.testnet` to a test BEP-20 token you can mint/faucet, and fund the gas wallet with test BNB.
- Run each coin through BOTH paths: (a) release to receiver, (b) cancel -> both confirm -> refund. Also try a wrong address,
  an underpayment, an overpayment, and an unpaid ticket.
- Check every txid on the testnet explorer, and that fees/treasury amounts are what you expect.

## 9. Mainnet, only after successful testnet validation
- `"network": "mainnet"`, keep `"max_usd": 10` at first, restart the bot.
- Do real deals yourself with 2 accounts for each coin. Then raise max_usd slowly.
- `"kill_switch": true` stops new deals instantly (open deals can still finish).

## 10. Operating it
- Back up `data/` (deals.json, wallet_index.json) regularly. The seed can always recover the money, but the files tell you which deal is which.
- Never delete wallet_index.json while deals are open.
- Overpaid deals: the extra stays in the deal address (not swept). Recover it with the seed + that deal's `wallet_index` in deals.json.
- Unpaid/underpaid tickets are closed after 20 min. If someone pays AFTER that, the money is in that deal's address; recover the same way.
- Keep the server patched, no remote access you don't need, `.env` readable only by the bot user.
- `.simpay` is a fake in-memory payment and does not exercise any chain adapter. Use actual testnet transactions for the testnet round.
- If a payout reports that confirmation is unknown, the deal is locked as `payout_pending` to prevent a duplicate send. Check the transaction ID on the chain explorer and have the wallet operator reconcile it; there is no automatic admin recovery command.

## Known limits (be honest with your users)
- This package has not been independently validated against live networks. A passing testnet run is necessary, but does not certify safe custody or payout behavior.
- Single explorer/RPC per chain = single point of failure; no fee bumping for stuck LTC transactions.
- It decides "paid" by balance, so two partial payments adding up to the amount are accepted.
- Check what your country's rules and Discord's terms say about holding other people's crypto.
