# Deploy on Render (free) – steps

1. Upload this folder to a PRIVATE GitHub repo (the .gitignore already keeps .env out).
2. Render -> New + -> Web Service -> pick the repo.
   - Runtime: Python 3
   - Build Command: pip install -r requirements.txt
   - Start Command: python main.py
   - Instance Type: Free
3. Environment -> add these variables (copy values from your local .env / Discord portal):
   - DISCORD_TOKEN      (use a NEW token: Discord Developer Portal -> Bot -> Reset Token)
   - WALLET_MNEMONIC
   - WALLET_PASSPHRASE  (leave empty if you don't use one)
   - GAS_WALLET_KEY
   - LTC_API_KEY        (NOWNodes key, needed for Litecoin)
4. Deploy. When logs show "Logged in as ...", copy your URL (https://xxxx.onrender.com).
5. uptimerobot.com -> Add New Monitor -> HTTP(s) -> your URL -> every 5 minutes.

WARNING: Render free has NO persistent disk. Everything in data/ (deals.json,
wallet_index.json, vouches, panels) resets to what is in GitHub on every restart/redeploy.
