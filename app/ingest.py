"""Load broker data into SQLite. Idempotent: re-running never duplicates rows.

Two entry points:
  bootstrap  - one-time load of the full history already fetched
  inbox      - ingest any envelope JSON dropped into sync/inbox/ by Claude Code
"""
import json, os, sys, glob, csv, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as D

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INBOX = os.path.join(ROOT, "sync", "inbox")
ARCHIVE = os.path.join(ROOT, "sync", "archive")

def _fill_date(o):
    ex = o.get("executions") or []
    if ex: return max(e["timestamp"] for e in ex)[:10]
    return (o.get("last_transaction_at") or o["created_at"])[:10]

def upsert_equity_orders(c, orders):
    n = 0
    for o in orders:
        q = float(o.get("cumulative_quantity") or 0)
        if q == 0: continue
        p = float(o.get("average_price") or 0)
        d = _fill_date(o); fees = float(o.get("fees") or 0)
        agent = o.get("placed_agent")
        # a DRIP buy is a dividend paid then immediately reinvested: record both legs
        if agent == "drip" and o["side"] == "buy":
            n += _ins(c, o["id"] + ":div", d, o["symbol"], "dividend", None, None, q * p, 0, "equity", agent)
        n += _ins(c, o["id"], d, o["symbol"], o["side"], q, p, None, fees, "equity", agent)
    return n

def upsert_crypto_orders(c, orders):
    n = 0
    for o in orders:
        q = float(o.get("cumulative_quantity") or 0)
        if q == 0: continue
        n += _ins(c, o["id"], o["created_at"][:10], o["currency_code"], o["side"],
                  q, float(o.get("average_price") or 0), None, 0, "crypto",
                  o.get("initiator_type"))
    return n

def _ins(c, oid, date, ticker, typ, qty, price, amount, fees, asset, agent):
    cur = c.execute(
        "INSERT OR IGNORE INTO transactions(order_id,date,ticker,type,quantity,price,amount,fees,asset,agent)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)", (oid, date, ticker, typ, qty, price, amount, fees, asset, agent))
    return cur.rowcount

def _asset_of(ticker, explicit=None):
    """Crypto vs equity. Prefer the envelope's own field; otherwise consult the
    sector map in config (sector 'crypto'); otherwise assume equity. No hardcoded
    coin list — this file is committed and should not imply what anyone holds."""
    if explicit in ("crypto", "equity"):
        return explicit
    try:
        import sectors as _S
        if _S.sector_of(ticker) == "crypto":
            return "crypto"
    except Exception:
        pass
    return "equity"

def upsert_positions(c, rows, asof=None):
    asof = asof or dt.date.today().isoformat()
    c.execute("DELETE FROM positions")
    for r in rows:
        c.execute("INSERT INTO positions(ticker,quantity,price,asset,asof) VALUES(?,?,?,?,?)",
                  (r["ticker"], float(r["quantity"]), float(r["price"]),
                   _asset_of(r["ticker"], r.get("asset")), asof))
    return len(rows)

def upsert_fundamentals(c, rows, asof=None):
    asof = asof or dt.date.today().isoformat()
    n = 0
    for r in rows:
        v = r.get("value"); tv = r.get("text_value")
        try: v = float(v) if v is not None else None
        except (TypeError, ValueError): tv, v = str(v), None
        c.execute("INSERT INTO fundamentals(ticker,asof,metric,value,text_value) VALUES(?,?,?,?,?)"
                  " ON CONFLICT(ticker,asof,metric) DO UPDATE SET value=excluded.value,"
                  " text_value=excluded.text_value", (r["ticker"], r.get("asof", asof), r["metric"], v, tv))
        n += 1
    return n

def upsert_quotes(c, rows, date=None):
    date = date or dt.date.today().isoformat()
    n = 0
    for r in rows:
        c.execute("INSERT INTO quotes(ticker,date,price,prev_close) VALUES(?,?,?,?)"
                  " ON CONFLICT(ticker,date) DO UPDATE SET price=excluded.price,"
                  " prev_close=excluded.prev_close",
                  (r["ticker"], r.get("date", date), float(r["price"]),
                   float(r["prev_close"]) if r.get("prev_close") is not None else None))
        n += 1
    return n

HANDLERS = {
    "orders_equity": upsert_equity_orders, "orders_crypto": upsert_crypto_orders,
    "positions": upsert_positions, "fundamentals": upsert_fundamentals, "quotes": upsert_quotes,
}

def ingest_envelope(c, env):
    kind = env.get("kind")
    if kind not in HANDLERS: raise ValueError(f"unknown kind: {kind}")
    return HANDLERS[kind](c, env["data"])

def run_inbox(c):
    total, files = 0, []
    for f in sorted(glob.glob(os.path.join(INBOX, "*.json"))):
        env = json.load(open(f))
        try:
            n = ingest_envelope(c, env); c.commit()
        except Exception as e:
            _log(c, "inbox", "error", 0, f"{os.path.basename(f)}: {e}"); continue
        total += n; files.append(f"{os.path.basename(f)}:{env['kind']}:+{n}")
        os.replace(f, os.path.join(ARCHIVE, dt.datetime.now().strftime("%Y%m%dT%H%M%S_") + os.path.basename(f)))
    _log(c, "inbox", "ok", total, "; ".join(files) or "no files")
    return total, files

def _log(c, kind, status, rows, detail):
    c.execute("INSERT INTO sync_runs(ts,kind,status,rows_added,detail) VALUES(?,?,?,?,?)",
              (dt.datetime.now().isoformat(timespec="seconds"), kind, status, rows, detail)); c.commit()

def bootstrap(c):
    """Bootstrap == ingest whatever envelopes Claude Code left in sync/inbox/.

    Deliberately the same code path as a daily sync, so a fresh clone needs no
    pre-seeded data files (they would be personal data and are gitignored).
    """
    pending = glob.glob(os.path.join(INBOX, "*.json"))
    if not pending:
        raise SystemExit(
            "sync/inbox/ is empty — nothing to bootstrap from.\n"
            "Open Claude Code in this folder and say: bootstrap\n"
            "(it follows sync/prompts/bootstrap.txt and writes envelopes here)")
    total, files = run_inbox(c)
    _log(c, "bootstrap", "ok", total, f"{len(pending)} envelope(s)")
    return total

if __name__ == "__main__":
    c = D.init()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "inbox"
    if cmd == "bootstrap":
        print("transaction rows inserted:", bootstrap(c))
    else:
        t, f = run_inbox(c); print("ingested:", t, f)
    for r in c.execute("SELECT type,COUNT(*) n,ROUND(SUM(COALESCE(amount,quantity*price)),2) v"
                       " FROM transactions GROUP BY type"):
        print(f"  {r['type']:9} {r['n']:>6} rows  ${r['v']:>12,.2f}")
    print("  positions:", c.execute("SELECT COUNT(*) n FROM positions").fetchone()["n"])
