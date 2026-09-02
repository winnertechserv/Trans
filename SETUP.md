# Trans — setup reference

**[README.md](README.md) covers the normal path**: connect the broker, clone, configure,
`bootstrap`, run. Start there.

This file is for the two cases README does not cover:

1. Moving to a **new machine** where your data already exists in Google Drive.
2. Adapting Trans to a **different broker**.

---

## Path A — new machine, your data already exists

You have backups in Google Drive and want this machine to pick up where the old one left off.

**1. Google Drive for Desktop**

Install it and **sign in with the account that holds the backups**. Then approve:

- System Settings → Privacy & Security → **Files and Folders** → Google Drive
- System Settings → General → **Login Items & Extensions** → enable the Drive
  *File Provider* extension

Skipping the extension is the usual reason the folder never appears. Verify:

```bash
ls -d ~/Library/CloudStorage/GoogleDrive-*/My\ Drive
```

No output means you are not actually signed in, whatever the app window says.

**2. Clone and configure**

```bash
git clone <your-repo-url> portfolio && cd portfolio
cp config.example.json config.json
```

Edit `config.json`:

```json
{
  "broker": { "account_number": "YOUR_ACCOUNT", "crypto_account_number": "YOUR_ACCOUNT" },
  "drive_backup_dir": "/Users/you/Library/CloudStorage/GoogleDrive-you@gmail.com/My Drive/trans",
  "backup": { "keep_daily": 30, "keep_monthly": 12 },
  "port": 8787
}
```

**3. Restore**

```bash
./run.sh --restore     # lists snapshots newest first; you choose
./run.sh               # http://127.0.0.1:8787
```

**4. Catch up on anything since that backup** — in Claude Code, say `sync`.

---

---

## Adapting it to a different broker

The app itself is broker-agnostic — it only ever reads `sync/inbox/*.json` envelopes:

```json
{"kind":"orders_equity","fetched_at":"<ISO>","data":[ ...raw order objects... ]}
```

Kinds: `orders_equity`, `orders_crypto`, `positions`, `quotes`, `fundamentals`.
To swap brokers, rewrite the prompts in `app/sync.py` to call your broker's tools and
emit the same envelopes. `app/ingest.py`, `analytics.py` and the UI need no changes.

Field mapping lives in `app/ingest.py` (`upsert_equity_orders` / `upsert_crypto_orders`).

## Troubleshooting

| Symptom | Cause |
|---|---|
| `./run.sh` prints "No portfolio data found" | empty DB — restore or bootstrap |
| Broker MCP tools missing in Claude Code | session predates the OAuth handshake — restart Claude Code. Do not curl the endpoint; it returns `authentication required` for any unauthenticated call and proves nothing |
| "Backup folder does not exist" | Drive not signed in, or File Provider extension disabled |
| Backups succeed but nothing appears in Drive online | Drive is paused, signed out, or out of quota — check the menu-bar icon |
| Fundamentals mostly "not loaded" | run `sync fundamentals` |

## Connecting the broker — the details

```bash
claude mcp add --transport http robinhood https://agent.robinhood.com/mcp/trading
```

Restart Claude Code, then `/mcp` → `robinhood` and complete OAuth in a **desktop**
browser. Mobile does not finish the flow.

The restart matters: Claude Code fetches a server's tool list when the session connects.
A session started before you authenticated keeps the logged-out tool list, so the account
tools look missing even though your login succeeded. Restart rather than debugging it.

Do not `curl` the endpoint to check. It returns `authentication required` for any
request without a token, whatever your browser session says, so it proves nothing.

### Finding your account number

Say `accounts` in Claude Code. It calls `get_accounts` and lists your accounts; put the
default one in `config.json`. Crypto calls use the same number in `rhs_account_number`.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `./run.sh` prints "No portfolio data found" | empty DB — restore or bootstrap |
| Broker MCP tools missing in Claude Code | session predates the OAuth handshake — restart Claude Code. Do not curl the endpoint; it returns `authentication required` for any unauthenticated call and proves nothing |
| "Backup folder does not exist" | Drive not signed in, or File Provider extension disabled |
| Backups succeed but nothing appears in Drive online | Drive is paused, signed out, or out of quota — check the menu-bar icon |
| `Port 8787 is already in use` | another copy running — `./run.sh 8788` or `pkill -f 'app/server.py'` |
| Fundamentals mostly "not loaded" | run `sync fundamentals` |
| Fundamentals show a generic metric set | run `classify tickers` |
| Bootstrap seems to hang | expected on a large account — ~80 paginated calls for ~10k orders |
