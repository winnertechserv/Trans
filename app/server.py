"""Local dashboard server. Stdlib only — no pip install, matches the repo's
zero-dependency convention. Binds to 127.0.0.1 so it is never exposed off-machine.
"""
import os, sys, json, urllib.parse, datetime as dt
from http.server import HTTPServer, BaseHTTPRequestHandler
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import db as D, analytics as A, sync as SY, costs as C, sectors as S, ingest as I
import backup as B, config as CFG, analysis as AN, explain as EX, markets as MK

STATIC = os.path.join(HERE, "static")

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass  # quiet

    def _send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers(); self.wfile.write(b)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, default=str))

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        p = u.path
        try:
            # Client-side routing: every tab has a real URL (/holdings, /research/MSFT),
            # so serve the app shell for any non-API path that is not a file request.
            # Deep links, bookmarks, back/forward and refresh then all work.
            if not p.startswith("/api/"):
                if "." in os.path.basename(p) and p != "/index.html":
                    return self._send(404, b"not found", "text/plain")
                return self._send(200, open(os.path.join(STATIC, "index.html"), "rb").read(),
                                  "text/html; charset=utf-8")
            c = D.connect()
            mk = MK.get(q.get("market", [None])[0])["key"]
            if p == "/api/markets":
                return self._json({"markets": MK.all_markets(), "default": MK.DEFAULT})
            if p == "/api/overview":
                try: B.maybe_auto_backup()
                except Exception: pass
                rows, ov = A.results(c, market=mk)
                return self._json({"market": MK.get(mk), "overall": ov, "results": rows,
                                   "health": A.health(c, market=mk),
                                   "pricing_verified": C.pricing().get("verified", False)})
            if p == "/api/results":
                rows, ov = A.results(c, market=mk); return self._json({"results": rows, "overall": ov, "market": MK.get(mk)})
            if p == "/api/daily-buys":
                return self._json(A.daily_buys(c, int(q.get("days", ["30"])[0]), market=mk))
            if p == "/api/buy-program":
                return self._json(A.buy_program(c, int(q.get("days", ["30"])[0]), market=mk))
            if p == "/api/dividends":   return self._json(A.dividends(c, market=mk))
            if p == "/api/allocation":  return self._json(A.allocation(c, market=mk))
            if p == "/api/contributions": return self._json(A.contributions(c, market=mk))
            if p == "/api/costs":       return self._json(A.costs(c))
            if p == "/api/health":      return self._json(A.health(c, market=mk))
            if p.startswith("/api/trades/"):
                return self._json(A.trades(c, urllib.parse.unquote(p.rsplit("/", 1)[1]),
                                           market=mk))
            if p == "/api/fundamentals":
                ex = [x for x in (q.get("extra", [""])[0] or "").split(",") if x.strip()]
                return self._json(A.fundamentals_matrix(c, market=mk, extra=ex))
            if p.startswith("/api/fundamentals/"):
                return self._json(A.fundamentals(c, p.rsplit("/", 1)[1].upper()))
            if p == "/api/sync/prompt":
                k = q.get("kind", ["daily"])[0]
                pr = SY.write_prompt_files(c)
                key = f"{mk}:{k}"
                return self._json({"kind": k, "market": MK.get(mk),
                                   "prompt": pr.get(key, pr.get(f"{mk}:daily", "")),
                                   "cursor": SY.cursor(c, mk), "inbox": SY.INBOX})
            if p == "/api/analysis":
                return self._json({"available": AN.available(),
                                   "estimate": AN.estimate(),
                                   "reports": AN.reports(),
                                   "jobs": AN.status()})
            if p == "/api/analysis/status":
                return self._json(AN.status(q.get("job", [None])[0]))
            if p.startswith("/api/analysis/"):
                return self._json(AN.reports(p.rsplit("/", 1)[1].upper()))
            if p == "/api/backups":
                return self._json(B.status())
            if p == "/api/sectors":
                return self._json({"map": S.TICKER_SECTOR(),
                                   "metrics": S.SECTOR_METRICS, "meta": S.METRIC_META})
            return self._json({"error": "unknown endpoint"}, 404)
        except Exception as e:
            import traceback; traceback.print_exc()
            return self._json({"error": str(e)}, 500)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        try:
            c = D.connect()
            if u.path == "/api/sync/ingest":
                total, files = I.run_inbox(c)
                D.log_tokens(c, "sync_ingest", "local", note=f"{total} rows")
                return self._json({"ok": True, "rows": total, "files": files,
                                   "cursor": SY.cursor(c)})
            if u.path == "/api/analysis/explain":
                return self._json(EX.explain(body.get("ticker"),
                                             consented=bool(body.get("consented"))))
            if u.path == "/api/analysis/probe":
                return self._json(AN.probe(body.get("ticker")))
            if u.path == "/api/analysis/estimate":
                return self._json(AN.estimate(body.get("ticker"), body.get("model")))
            if u.path == "/api/analysis/run":
                return self._json(AN.run(body.get("ticker"), body.get("date"),
                                         consented=bool(body.get("consented")),
                                         model=body.get("model")))
            if u.path == "/api/backup":
                rec = B.snapshot(kind=body.get("kind", "manual"))
                removed = B.prune()
                D.log_tokens(c, "backup", "local", note=f"{rec['file']} ({rec['n_transactions']} txns)")
                return self._json({"ok": True, "backup": rec, "pruned": removed,
                                   "note": "snapshot written and queued to Google Drive — "
                                           "Drive uploads in the background, so confirm in Drive "
                                           "that it finished."})
            if u.path == "/api/restore":
                c.close()                                 # release our handle first
                r = B.restore(body["file"], force=True)   # this process is the server
                c = D.connect()                           # reopen against restored data
                SY.write_prompt_files(D.connect())        # cursor moved -> refresh prompts
                D.log_tokens(c, "restore", "local", note=r["restored"])
                return self._json({"ok": True, **r})
            if u.path == "/api/backup/auto":
                return self._json(B.maybe_auto_backup(throttle_sec=0))
            if u.path == "/api/costs/estimate":
                return self._json(C.estimate_chars(body.get("model", "claude-sonnet-5"),
                                                   int(body.get("prompt_chars", 4000)),
                                                   int(body.get("expected_output_tokens", 1200))))
            if u.path == "/api/costs/log":
                rid = C.record(body["operation"], body.get("source", "claude_code"),
                               body.get("model"), int(body.get("input_tokens", 0)),
                               int(body.get("output_tokens", 0)),
                               1 if body.get("consented") else 0, body.get("note"))
                return self._json({"ok": True, "id": rid})
            return self._json({"error": "unknown endpoint"}, 404)
        except Exception as e:
            import traceback; traceback.print_exc()
            return self._json({"error": str(e)}, 500)

def main(port=8787):
    D.init()
    import threading
    def _startup_backup():
        try:
            r = B.maybe_auto_backup(throttle_sec=0)
            if r.get("ran"):
                print(f"auto-backup: {r['backup']['file']} ({r['reason']})")
        except Exception as e:
            print("auto-backup skipped:", e)
    threading.Thread(target=_startup_backup, daemon=True).start()
    try:
        srv = HTTPServer(("127.0.0.1", port), H)
    except OSError as e:
        if getattr(e, "errno", None) in (48, 98):     # EADDRINUSE (mac, linux)
            print(f"Port {port} is already in use.\n"
                  f"  Another copy may be running:  ./run.sh <other-port>\n"
                  f"  Or stop it:  pkill -f 'app/server.py'")
            raise SystemExit(1)
        raise
    print(f"portfolio dashboard -> http://127.0.0.1:{port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")

if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8787)
