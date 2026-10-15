# Time AutoMM — Full Discord Auto + Human Middleman Bot

A clean, dark-themed Auto Middleman bot inspired by Jace's Auto MM, plus a full Human/Manual MM ticket system.

---

## Features

- **Auto MM request panel**
  - Request LTC
  - Request USDT (BEP-20)
  - Request SOL
  - Buttons start private tickets; they do not independently verify blockchain payments.
  - Button labels are editable, and custom server emojis are optional.
- **Human Middleman** ticket system
  - Opens the same trade details form as the screenshot
  - The requester and named trader can see the ticket while it is waiting
  - A staff member claims it; after that, the requester, trader, assigned middleman, and admins retain access, while the general staff role is denied
- Fee calculator matching Jace's structure:
  - Under $50 → FREE
  - Under $250 → $0.50
  - $250+ → $1.50
- Blacklist system
- Deal logs + Ticket logs
- Staff / Admin role support
- Persistent buttons (survive bot restarts)
- Fully slash-command based

For auto deals, configure `wallet.treasury.LTC`, `wallet.treasury.SOL`, and `wallet.treasury.USDT` with your public fee-receiving addresses. The sender pays the trade amount plus the service fee to a unique per-deal escrow address; on release, the bot sends the trade amount to the receiver and routes the remaining fee amount to your configured treasury address. Refunds return the deposit to the sender and do not collect the service fee. Network fees can reduce the treasury amount, and very small Litecoin dust may be consumed as network fee.

---

## Requirements

- Python 3.10 or higher
- A Discord Bot Token

```bash
pip install -r requirements.txt
```

---

## Full Setup Guide (Same structure as Jace MM)

### 1. Create your Discord Server

Create a new server (or use existing).

### 2. Create Roles (in this order, top to bottom)

| Role            | Color       | Key Permission          |
|-----------------|-------------|-------------------------|
| Owner           | Gold/Red    | Administrator           |
| Admin           | Red         | Manage Channels + Roles |
| Staff / MM      | Blue        | View + manage tickets   |
| Verified        | Green       | Optional                |
| Customer        | Grey        | Default                 |
| Blacklisted     | Dark Red    | None (deny access)      |

### 3. Create Categories & Channels

**Category: INFORMATION**
- `#rules`
- `#tos-crypto`          ← Put your Terms of Service here
- `#how-to-use`
- `#fees` (optional)

**Category: AUTO MIDDLEMAN**
- `#auto-mm-panel`       ← Main panel goes here
- `#deal-logs`           ← Private (Staff + Admin only)
- `#blacklist`           ← Private

**Category: TICKETS - AUTO**
- (empty – bot creates tickets here)

**Category: TICKETS - HUMAN**
- (empty – bot creates human MM tickets here)

### 4. Invite the Bot

Use this permission integer: `8` (Administrator) for easy setup,  
or more restricted: Manage Channels, Manage Roles, Send Messages, Embed Links, Attach Files, Read Message History, Manage Messages, View Channels.

### 5. Get all IDs

Enable **Developer Mode** in Discord (User Settings → Advanced).

Right-click and **Copy ID** for:
- Server (guild)
- `#auto-mm-panel` channel
- `#deal-logs` channel
- `#blacklist` channel
- Auto Tickets category
- Human Tickets category
- Admin role
- Staff role
- Blacklisted role

### 6. Edit `config.json`

Open `config.json` and fill in:

```json
{
  "token": "YOUR_BOT_TOKEN_HERE",
  "guild_id": 123456789012345678,

  "channels": {
    "panel": 123456789012345678,
    "deal_logs": 123456789012345678,
    "blacklist": 123456789012345678,
    "ticket_logs": 123456789012345678
  },

  "categories": {
    "auto_tickets": 123456789012345678,
    "human_tickets": 123456789012345678
  },

  "roles": {
    "admin": 123456789012345678,
    "staff": 123456789012345678,
    "blacklisted": 123456789012345678
  }
}
```

### 7. Run the Bot

```bash
python main.py
```

### 8. Send the Panel

In any channel (preferably `#auto-mm-panel`), run:

```
/sendpanel
```

This will post:
1. The Auto MM panel (LTC / USDT / SOL buttons)
2. The Human Middleman request panel

---

## Commands

| Command          | Description                              | Permission     |
|------------------|------------------------------------------|----------------|
| `/sendpanel`     | Send Auto + Human MM panels              | Administrator  |
| `/blacklist`     | Blacklist a user                         | Administrator  |
| `/unblacklist`   | Remove user from blacklist               | Administrator  |
| `/calcfee`       | Calculate fee for a deal amount          | Everyone       |
| `/setup`         | Show quick setup reminder                | Administrator  |

---

## Auto deal flow (new)

Request LTC/SOL/USDT now runs the full flow in `cogs/autodeal.py`:

1. Form modal (trader, what each side gives) -> private ticket with both traders
2. Select role (Sender/Receiver) -> both click Correct
3. Set USD amount -> both click Correct
4. Payment info (exact coin amount + deposit address, 20 min window)
5. Bot detects payment -> **Payment Received** with `Release to Receiver` / `Cancel Deal`
   - Sender releases (with an "are you sure") -> receiver enters address -> Confirm -> funds sent to receiver
   - Cancel -> **both** must confirm -> sender enters address -> Confirm -> refund to sender
   - If either side declines the cancel, the deal goes back to step 5

**Wallet mode:** `config.json -> wallet.mode` is `"dryrun"` by default (fake money; `.simpay` fakes a payment).
`"live"` uses real keys from your seed phrase. The live adapters are implemented, but this package has not been independently validated against live chains. **Read `SETUP.md` and complete the testnet round before considering mainnet.**

## Important Notes about Crypto

The live path derives per-deal deposit addresses and includes balance monitoring and payout code for LTC, SOL, and USDT on BNB Smart Chain. It waits for the configured chain confirmation before reporting a payout as successful. If confirmation is ambiguous, it locks the deal as `payout_pending` to prevent an automatic duplicate; staff must reconcile that case manually. This is not production-certified: explorer/RPC behavior, derivation compatibility, fee accounting, and recovery must be exercised on testnets and independently reviewed before custody of real funds. The bot controls funds sent to its derived addresses. Never send a seed phrase or private key to anyone.

---

## Support the Style

The embeds, fee structure, and button layout are designed to match the screenshot you provided from Jace's Auto Middleman as closely as possible.

Enjoy your Time AutoMM bot!


## Customizing AutoMM buttons

Use the prefix command as an administrator, then resend the panel so the new buttons appear:

```text
.editpanel auto ltc_label Request LTC
.editpanel auto usdt_label Request USDT BEP20
.editpanel auto sol_label Request SOL
```

To add your own server emoji, copy its emoji mention from Discord and use it as the value. This works for every panel button:

```text
.editpanel auto ltc_emoji <:yourLTC:123456789012345678>
.editpanel auto usdt_emoji <:yourUSDT:123456789012345678>
.editpanel auto sol_emoji <:yourSOL:123456789012345678>
.editpanel human button_emoji <:yourMM:123456789012345678>
.editpanel nitro button_emoji <:yourNitro:123456789012345678>
.editpanel robux button_emoji <:yourRobux:123456789012345678>
.editpanel boosts button_emoji <:yourBoost:123456789012345678>
```

Replace each example with your actual custom emoji markup and ID. Use `none` to remove an emoji, for example `.editpanel nitro button_emoji none`. Button labels are editable too with `button_label` (or `ltc_label`, `usdt_label`, `sol_label` for crypto). After changing settings, re-run the matching `send...panel` command to post a panel with the new button appearance. Existing messages won't change automatically.
