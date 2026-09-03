"""Load broker data into SQLite. Idempotent: re-running never duplicates rows.

Two entry points:
  bootstrap  - one-time load of the full history already fetched
  inbox      - ingest any envelope JSON dropped into sync/inbox/ by Claude Code
"""
# SPDX-License-Identifier: Apache-2.0
import json, os, sys, glob, csv, re, collections, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as D
import markets as MK
import config as CFG

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INBOX = os.path.join(ROOT, "sync", "inbox")
ARCHIVE = os.path.join(ROOT, "sync", "archive")
# Created here rather than relied upon from the checkout: git does not track empty
# directories, so `sync/archive` exists in a clone only as long as a .gitkeep survives —
# and one history rewrite removed it. Without this the first import inserts its rows and
# then dies moving the file, which looks like a parsing failure and is not one.
for _d in (INBOX, ARCHIVE):
    os.makedirs(_d, exist_ok=True)

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

def upsert_kite_holdings(c, rows, asof=None):
    """Zerodha holdings. quantity can be 0 while shares are pledged or authorised for
    sale, so opening_quantity is the honest count of what is owned."""
    asof = asof or dt.date.today().isoformat()
    c.execute("DELETE FROM positions WHERE broker='zerodha'")
    n = 0
    for r in rows:
        sym = r.get("tradingsymbol")
        qty = r.get("opening_quantity")
        if qty in (None, 0):
            qty = (r.get("quantity") or 0) + (r.get("used_quantity") or 0)
        if not sym or not qty:
            continue
        # Normalise to the canonical symbol: Zerodha reports the NSE series code in
        # holdings but not in the tradebook (MTARTECH-BE vs MTARTECH), and renamed
        # companies keep their old symbol on old trades. Without this the trades and the
        # holding never join, and the position looks like it has no history.
        sym = MK.canonical_symbol(sym, CFG.ticker_aliases())
        c.execute("INSERT INTO positions(ticker,quantity,price,asset,asof,broker,"
                  "currency,exchange,avg_cost) VALUES(?,?,?,?,?,?,?,?,?)",
                  (sym, float(qty), float(r.get("last_price") or 0),
                   _kite_asset(sym), asof, "zerodha", "INR",
                   r.get("exchange"), float(r.get("average_price") or 0)))
        n += 1
    return n


def ingest_zerodha_tradebook(c, paths):
    """Zerodha Console tradebook CSVs -> transactions.

    Kite's API serves one day of orders, so this export is the only route to history.
    Four traps, all found in the real files:
      * The dedupe key must use the raw symbol, not the normalised one, or adding a
        rename later re-imports every affected trade under both names.
      * trade_id is NOT globally unique — id 26030407 belongs to both a 2022 HEROMOTOCO
        trade and a 2021 FILATEX one. The dedupe key is (trade_id, date, symbol).
      * Symbols carry an NSE series code in holdings but not in the tradebook
        (MTARTECH-BE vs MTARTECH), and renamed companies keep the old symbol on old
        trades (GET&D -> GVT&D), so both sides go through markets.canonical_symbol.
      * The EQ export holds equities only. Bonds and Sovereign Gold Bonds bought in the
        primary market never appear, and neither do shares received from demergers.
        Mutual funds come from a separate MF export in the same shape — handled here,
        keyed on ISIN because the fund name is not stable across Zerodha's own sources.
    """
    n = 0
    seen_files = []
    mf_names = {}
    aliases = CFG.ticker_aliases()
    for path in sorted(paths):
        with open(path, newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
        for r in rows:
            raw = (r.get("symbol") or "").strip().upper()
            qty = float(r.get("quantity") or 0)
            px = float(r.get("price") or 0)
            side = (r.get("trade_type") or "").strip().lower()
            date = (r.get("trade_date") or "")[:10]
            if not (qty and side in ("buy", "sell") and date):
                continue

            # Mutual fund exports share the equity schema but fill it differently:
            # segment is MF, `symbol` holds the fund's NAME rather than a ticker, and the
            # ISIN column is the only stable identifier. Names genuinely move — the same
            # INF966L01614 is "QUANT ACTIVE FUND" in the tradebook and "QUANT MULTI CAP
            # FUND" from get_mf_holdings, and INF247L01445 is "DIRECT GROWTH" in one and
            # "DIRECT PLAN" in the other — so keying on the name would split one fund's
            # history in two. ISIN also matches how MF positions are already stored.
            if (r.get("segment") or "").strip().upper() == "MF":
                isin = (r.get("isin") or "").strip().upper()
                if not isin:
                    continue
                oid = f"zmf:{r.get('trade_id','')}:{date}:{isin}"
                n += _ins2(c, oid, date, isin, side, qty, px, None, 0.0,
                           "mf", "user", "zerodha", "INR")
                if raw:
                    mf_names.setdefault(isin, r.get("symbol").strip())
                continue

            sym = MK.canonical_symbol(raw, aliases)
            if not sym:
                continue
            # Key on the RAW symbol from the file, never the resolved one: resolution
            # depends on markets.RENAMES and config ticker_aliases, and if either changes
            # the same trade would hash to a new id and import a second time. The raw
            # symbol is whatever Zerodha wrote and never moves. Verified unique across
            # 4,354 real trades; trade_id alone is not (one id is reused).
            oid = f"zt:{r.get('trade_id','')}:{date}:{raw}"
            n += _ins2(c, oid, date, sym, side, qty, px, None, 0.0,
                       _kite_asset(sym), "user", "zerodha", "INR")
        seen_files.append(f"{os.path.basename(path)}:{len(rows)}")
    # Only fill a name where none exists: get_mf_holdings runs against funds still held
    # and is the fresher source, so it wins. The tradebook is the only name available for
    # funds fully exited.
    today = dt.date.today().isoformat()
    for isin, fund in mf_names.items():
        c.execute("INSERT INTO fundamentals(ticker,asof,metric,value,text_value)"
                  " VALUES(?,?,?,NULL,?) ON CONFLICT(ticker,asof,metric) DO NOTHING",
                  (isin, today, "name", fund))
    c.commit()
    return n, seen_files


def upsert_kite_mf_holdings(c, rows, asof=None):
    """Kite get_mf_holdings() -> positions, as asset 'mf'.

    Mutual funds live outside the demat entirely, which is why they were invisible: the
    tradebook is the EQ segment and get_holdings() returns only demat stock. They are
    keyed on the ISIN Kite reports as `tradingsymbol` (INF966L01614) because that is the
    stable identifier across platforms — the same fund bought through another broker
    carries the same ISIN. The readable fund name is stored alongside as a `name` metric,
    the same way equity names are, so the UI shows the fund and not the ISIN.

    There is no order history: Kite exposes holdings only, so units and average cost come
    from the broker and these report no XIRR, like the SGBs.
    """
    asof = asof or dt.date.today().isoformat()
    n = 0
    for r in rows:
        isin = (r.get("tradingsymbol") or "").strip().upper()
        qty = r.get("quantity")
        if not isin or not qty:
            continue
        c.execute("INSERT INTO positions(ticker,quantity,price,asset,asof,broker,"
                  "currency,exchange,avg_cost) VALUES(?,?,?,?,?,?,?,?,?)"
                  " ON CONFLICT(broker,ticker) DO UPDATE SET quantity=excluded.quantity,"
                  " price=excluded.price,asof=excluded.asof,avg_cost=excluded.avg_cost,"
                  " asset=excluded.asset,exchange=excluded.exchange",
                  (isin, float(qty), float(r.get("last_price") or 0), "mf", asof,
                   "zerodha", "INR", "MF", float(r.get("average_price") or 0)))
        fund = (r.get("fund") or "").strip()
        if fund:
            c.execute("INSERT INTO fundamentals(ticker,asof,metric,value,text_value)"
                      " VALUES(?,?,?,NULL,?) ON CONFLICT(ticker,asof,metric)"
                      " DO UPDATE SET text_value=excluded.text_value",
                      (isin, asof, "name", fund))
        n += 1
    return n


def ingest_paytm_statements(c, paths):
    """Paytm Money MF statements (extracted text, or PDF via pdftotext) -> transactions.

    Paytm exposes no API, so the per-financial-year statement is the only route. Each is
    checked against its own Fresh Purchase and Withdrawal totals before anything is
    written: a statement that does not add up is skipped whole rather than imported
    partially, because a missing purchase silently inflates realised profit.

    There is no ISIN and no trade id. Holdings are keyed on folio plus a slug of the
    scheme name — a folio alone is not enough, since one SBI folio here carries three
    different schemes — and rows dedupe on the full content of the transaction plus an
    occurrence counter, which is stable across re-uploads of the same statements.
    """
    import hashlib
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import paytm as PM

    pw = os.environ.get("PAYTM_PDF_PASSWORD", "")
    rows, files, skipped, seen_text = [], [], [], set()   # rows: one list per statement
    for path in sorted(paths):
        if path.lower().endswith(".pdf"):
            import subprocess, tempfile
            with tempfile.NamedTemporaryFile(suffix=".txt") as tmp:
                cmd = ["pdftotext", "-layout"] + (["-upw", pw] if pw else []) + [path, tmp.name]
                try:
                    r = subprocess.run(cmd, capture_output=True, text=True)
                except FileNotFoundError:
                    skipped.append(f"{os.path.basename(path)}: pdftotext not installed"
                                   " (brew install poppler / apt install poppler-utils)")
                    continue
                if r.returncode:
                    skipped.append(f"{os.path.basename(path)}: {r.stderr.strip()[:60]}"
                                   " — set PAYTM_PDF_PASSWORD if it is protected")
                    continue
                text = open(tmp.name).read()
        else:
            text = open(path).read()

        h = hashlib.md5(text.encode()).hexdigest()
        if h in seen_text:                       # the same year handed over twice
            files.append(f"{os.path.basename(path)}:duplicate")
            continue
        seen_text.add(h)

        parsed, meta = PM.parse_text(text)
        ok, detail = PM.check(parsed, meta)
        if not ok:
            skipped.append(f"{os.path.basename(path)}: FY{meta.get('fy','?')} totals "
                           f"{detail['buy']:.0f}/{detail['sell']:.0f} vs stated "
                           f"{detail['stated_buy']}/{detail['stated_sell']}")
            continue
        rows.append([r for r in parsed if r["status"] == "Confirmed"])
        files.append(f"FY{meta.get('fy','?')}:{detail['rows']}")

    # Names must be reconciled across the whole batch before anything is keyed on them.
    flat = [r for one in rows for r in one]
    if flat:
        PM.canonicalise(flat)

    # Statements have no trade id, so a row is identified by its own content plus an
    # occurrence number. Counting occurrences across the whole batch would turn a
    # re-handed statement into duplicate transactions — and the same year does arrive
    # twice, with a different generation timestamp in the footer so the files are not
    # byte-identical. Count within each statement and keep the highest, which is what
    # overlapping statements of the same period mean.
    best, sample = {}, {}
    for one in rows:
        seen_here = collections.Counter()
        for r in sorted(one, key=lambda x: (x["date"], x["folio"], x["scheme"], x["amount"])):
            tick = _paytm_ticker(r["folio"], r["scheme"])
            key = (tick, r["date"], r["type"], round(r["units"], 4), round(r["amount"], 2))
            seen_here[key] += 1
            sample[key] = (r, tick)
        for k, v in seen_here.items():
            best[k] = max(best.get(k, 0), v)

    n = 0
    names = {}
    for key, times in best.items():
        r, tick = sample[key]
        names[tick] = r["scheme"]
        for j in range(1, times + 1):
            oid = "pm:%s:%s:%s:%.4f:%.2f:%d" % (tick, r["date"], r["type"],
                                                r["units"], r["amount"], j)
            n += _ins2(c, oid, r["date"], tick, r["type"], r["units"], r["nav"],
                       None, 0.0, "mf", "user", "paytm", "INR")

    today = dt.date.today().isoformat()
    for tick, nm in names.items():
        c.execute("INSERT INTO fundamentals(ticker,asof,metric,value,text_value)"
                  " VALUES(?,?,?,NULL,?) ON CONFLICT(ticker,asof,metric) DO NOTHING",
                  (tick, today, "name", nm))
    c.commit()
    _rebuild_paytm_positions(c)
    return n, files + [f"SKIPPED {x}" for x in skipped]


def _paytm_ticker(folio, scheme):
    """Folio, a readable slug, and a hash of the full scheme name.

    The slug alone is not enough: folio 14540129 holds five ICICI Prudential schemes
    whose names agree for well past ten characters, and truncating merged all five into
    one holding. The hash makes the key exact; the slug keeps it recognisable in a log.
    """
    import hashlib
    norm = re.sub(r"[^A-Za-z0-9]", "", scheme).upper()
    return f"PM{folio}-{norm[:10]}{hashlib.md5(norm.encode()).hexdigest()[:4].upper()}"


def _rebuild_paytm_positions(c):
    """Paytm reports no holdings, so derive them from the transactions.

    Units are what was bought less what was sold. There is no live NAV either, so the
    mark is the NAV of the most recent transaction in that scheme and `asof` records its
    date — for anything still on a monthly SIP that is days old, and for a fund left
    untouched for years the staleness is visible rather than hidden.
    """
    agg = {}
    for r in c.execute("SELECT ticker,date,type,quantity,price FROM transactions"
                       " WHERE broker='paytm' ORDER BY date,id"):
        a = agg.setdefault(r["ticker"], {"u": 0.0, "nav": 0.0, "asof": ""})
        a["u"] += r["quantity"] if r["type"] == "buy" else -r["quantity"]
        if r["date"] >= a["asof"]:
            a["asof"], a["nav"] = r["date"], r["price"]
    c.execute("DELETE FROM positions WHERE broker='paytm'")
    for tick, a in agg.items():
        if a["u"] <= 1e-6:
            continue
        c.execute("INSERT INTO positions(ticker,quantity,price,asset,asof,broker,"
                  "currency,exchange,avg_cost) VALUES(?,?,?,?,?,?,?,?,NULL)",
                  (tick, a["u"], a["nav"], "mf", a["asof"], "paytm", "INR", "MF"))
    c.commit()
    return len(agg)


def _kite_asset(sym):
    """Not everything in an Indian demat is a stock — Sovereign Gold Bonds and listed
    bonds sit alongside equities and must not be sector-classified as companies."""
    s = (sym or "").upper()
    if s.startswith("SGB"):
        return "sgb"
    if s[:1].isdigit():
        return "bond"
    return "equity"


def _ins(c, oid, date, ticker, typ, qty, price, amount, fees, asset, agent):
    return _ins2(c, oid, date, ticker, typ, qty, price, amount, fees, asset, agent,
                 "robinhood", "USD")


def _ins2(c, oid, date, ticker, typ, qty, price, amount, fees, asset, agent,
          broker, currency):
    cur = c.execute(
        "INSERT OR IGNORE INTO transactions(order_id,date,ticker,type,quantity,price,"
        "amount,fees,asset,agent,broker,currency) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (oid, date, ticker, typ, qty, price, amount, fees, asset, agent, broker, currency))
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

def upsert_positions(c, rows, asof=None, broker="robinhood"):
    """Replace one broker's positions. A snapshot is authoritative for its own broker
    only — it says nothing about the others.

    This used to `DELETE FROM positions` outright, which was right when Robinhood was the
    only source and became silent data loss the moment a second market existed: a US sync
    deleted every Zerodha and Paytm holding, and nothing complained because the US numbers
    it was checked against were perfectly correct.
    """
    asof = asof or dt.date.today().isoformat()
    c.execute("DELETE FROM positions WHERE broker=?", (broker,))
    for r in rows:
        c.execute("INSERT INTO positions(ticker,quantity,price,asset,asof,broker,currency)"
                  " VALUES(?,?,?,?,?,?,?)",
                  (r["ticker"], float(r["quantity"]), float(r["price"]),
                   _asset_of(r["ticker"], r.get("asset")), asof, broker,
                   "USD" if broker == "robinhood" else "INR"))
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
    "kite_holdings": upsert_kite_holdings, "kite_mf_holdings": upsert_kite_mf_holdings,
    "positions": upsert_positions, "fundamentals": upsert_fundamentals, "quotes": upsert_quotes,
}

def ingest_envelope(c, env):
    kind = env.get("kind")
    if kind not in HANDLERS: raise ValueError(f"unknown kind: {kind}")
    return HANDLERS[kind](c, env["data"])

def run_inbox(c):
    total, files = 0, []
    csvs = sorted(glob.glob(os.path.join(INBOX, "tradebook-*.csv")))
    if csvs:
        n, seen = ingest_zerodha_tradebook(c, csvs)
        total += n
        files.append(f"zerodha tradebook x{len(csvs)}:+{n}")
        for p in csvs:
            os.replace(p, os.path.join(ARCHIVE,
                       dt.datetime.now().strftime("%Y%m%dT%H%M%S_") + os.path.basename(p)))
    pm = sorted(glob.glob(os.path.join(INBOX, "Transactions_*.pdf"))
                + glob.glob(os.path.join(INBOX, "paytm*.pdf"))
                + glob.glob(os.path.join(INBOX, "paytm*.txt")))
    if pm:
        n, det = ingest_paytm_statements(c, pm)
        total += n
        files.append(f"paytm x{len(pm)}:+{n} [{'; '.join(det)}]")
        for p in pm:
            os.replace(p, os.path.join(ARCHIVE,
                       dt.datetime.now().strftime("%Y%m%dT%H%M%S_") + os.path.basename(p)))
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

def remap_symbols(c):
    """Re-apply canonical_symbol to stored Zerodha rows.

    Dedupe is INSERT OR IGNORE, so a trade already in the table is skipped whole — a
    rename added to markets.RENAMES or config ticker_aliases after the import does not
    reach it. Re-uploading the CSV will not help either, and should not: the dedupe key
    is the raw symbol precisely so that re-uploads stay no-ops. This walks the existing
    rows instead. Safe to run repeatedly; it only ever rewrites the ticker column.
    """
    al = CFG.ticker_aliases()
    moved = []
    for table, extra in (("transactions", ""), ("positions", "")):
        # Alias rowid: `transactions` has its own `id` column, which shadows rowid in the
        # result set, so selecting it unaliased returns the id under the wrong key.
        for r in c.execute(f"SELECT rowid AS rid, ticker FROM {table}"
                           " WHERE broker='zerodha'").fetchall():
            canon = MK.canonical_symbol(r["ticker"], al)
            if canon != r["ticker"]:
                c.execute(f"UPDATE {table} SET ticker=? WHERE rowid=?", (canon, r["rid"]))
                moved.append(f"{table}: {r['ticker']} -> {canon}")
    c.commit()
    return moved


if __name__ == "__main__":
    c = D.init()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "inbox"
    if cmd == "bootstrap":
        print("transaction rows inserted:", bootstrap(c))
    elif cmd == "remap":
        m = remap_symbols(c)
        print(f"remapped {len(m)} row(s)")
        for line in m[:20]:
            print("  " + line)
        sys.exit(0)
    else:
        t, f = run_inbox(c); print("ingested:", t, f)
    for r in c.execute("SELECT type,COUNT(*) n,ROUND(SUM(COALESCE(amount,quantity*price)),2) v"
                       " FROM transactions GROUP BY type"):
        print(f"  {r['type']:9} {r['n']:>6} rows  ${r['v']:>12,.2f}")
    print("  positions:", c.execute("SELECT COUNT(*) n FROM positions").fetchone()["n"])
