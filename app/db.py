"""SQLite schema + connection. Local file DB, no server process."""
import sqlite3, os, datetime as dt

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "portfolio.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
  id INTEGER PRIMARY KEY,
  order_id   TEXT UNIQUE,          -- broker order id; dedupe key for delta sync
  date       TEXT NOT NULL,        -- YYYY-MM-DD (fill date)
  ticker     TEXT NOT NULL,
  type       TEXT NOT NULL,        -- buy | sell | dividend
  quantity   REAL,
  price      REAL,
  amount     REAL,                 -- dividends: cash amount
  fees       REAL DEFAULT 0,
  asset      TEXT DEFAULT 'equity',-- equity | crypto
  agent      TEXT,                 -- user | recurring | drip
  account    TEXT
);
CREATE INDEX IF NOT EXISTS ix_tx_ticker ON transactions(ticker);
CREATE INDEX IF NOT EXISTS ix_tx_date   ON transactions(date);
CREATE INDEX IF NOT EXISTS ix_tx_type   ON transactions(type);

CREATE TABLE IF NOT EXISTS positions (
  ticker   TEXT PRIMARY KEY,
  quantity REAL NOT NULL,
  price    REAL NOT NULL,          -- current mark
  asset    TEXT DEFAULT 'equity',
  asof     TEXT NOT NULL
);

-- long/EAV format: sector-specific metric sets vary per ticker, so no fixed columns
CREATE TABLE IF NOT EXISTS fundamentals (
  ticker TEXT NOT NULL,
  asof   TEXT NOT NULL,
  metric TEXT NOT NULL,
  value  REAL,
  text_value TEXT,
  PRIMARY KEY (ticker, asof, metric)
);

CREATE TABLE IF NOT EXISTS quotes (
  ticker TEXT NOT NULL, date TEXT NOT NULL,
  price REAL, prev_close REAL,
  PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS ai_notes (
  id INTEGER PRIMARY KEY,
  ticker TEXT NOT NULL, created_at TEXT NOT NULL,
  kind TEXT,                       -- 'ta:<section>' for TradingAgents reports
  content TEXT,
  source TEXT,                     -- tradingagents/<backend>/<model> | claude_code
  ledger_id INTEGER
);
CREATE INDEX IF NOT EXISTS ix_ai_notes ON ai_notes(ticker, created_at);

-- every operation that consumes tokens, whichever path it took
CREATE TABLE IF NOT EXISTS token_ledger (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  operation TEXT NOT NULL,
  source TEXT NOT NULL,            -- claude_code (free at point of use) | claude_api (billed)
  model TEXT,
  input_tokens INTEGER DEFAULT 0,
  output_tokens INTEGER DEFAULT 0,
  cost_usd REAL DEFAULT 0,
  consented INTEGER DEFAULT 0,
  note TEXT
);

CREATE TABLE IF NOT EXISTS sync_runs (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL, kind TEXT, status TEXT,
  rows_added INTEGER DEFAULT 0, detail TEXT
);

CREATE TABLE IF NOT EXISTS backups (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL, file TEXT, bytes INTEGER, sha256 TEXT,
  n_transactions INTEGER, kind TEXT, status TEXT, detail TEXT
);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

def connect():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c

def init():
    c = connect(); c.executescript(SCHEMA); c.commit(); return c

def set_meta(c, k, v):
    c.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(v)))

def get_meta(c, k, default=None):
    r = c.execute("SELECT value FROM meta WHERE key=?", (k,)).fetchone()
    return r["value"] if r else default

def log_tokens(c, operation, source, model=None, input_tokens=0, output_tokens=0,
               cost_usd=0.0, consented=0, note=None):
    cur = c.execute(
        "INSERT INTO token_ledger(ts,operation,source,model,input_tokens,output_tokens,cost_usd,consented,note)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (dt.datetime.now().isoformat(timespec="seconds"), operation, source, model,
         input_tokens, output_tokens, cost_usd, consented, note))
    c.commit(); return cur.lastrowid

if __name__ == "__main__":
    init(); print("initialised", DB_PATH)
