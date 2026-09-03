"""Paytm Money mutual fund statements -> transaction rows.

Paytm has no API we can use, so the only route is the per-financial-year transaction
statement, which arrives as a password-protected PDF. This module parses the *text* of
one; extraction from PDF is left to `pdftotext -layout` (poppler), which is a system tool
rather than a pip dependency, so Trans stays installable with nothing but Python.

Three things about the format drive the design:

  * There is NO ISIN anywhere, only a scheme name and a folio number. The folio is the
    stable key — one folio is one scheme — so the scheme name is derived by majority vote
    across every row sharing a folio. Reading the name off each row individually picks up
    page furniture ("Need Help?", "Statement generated on") and month prefixes, because
    long names wrap and the columns are only visually aligned.
  * Rows use at least two layouts. Short scheme names put the money on their own line;
    long ones wrap, and the money line then starts with the tail of the name. Anchoring
    on the units/NAV/amount/status suffix handles both.
  * Every statement carries its own Fresh Purchase and Withdrawal totals, which is a
    checksum worth using: parse_text returns them alongside the rows so the caller can
    refuse a file that does not add up rather than importing a partial year.
"""
# SPDX-License-Identifier: Apache-2.0
import re, collections

MONTHS = {m: i + 1 for i, m in enumerate(
    "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}

_TAIL = re.compile(r"([\d,]+\.\d+)\s+₹([\d,]+\.\d+)\s+₹([\d,]+\.\d+)\s+"
                   r"(Confirmed|Pending|Failed|Cancelled)\s*$")
_FOLIO = re.compile(r"\b(\d{6,14})\b")
_DATE = re.compile(r"\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b")
# The year sits alone in the date column, under the day and month. It must not be
# allowed to match inside a number: \b(20\d\d)\b happily finds "2073" in a unit count of
# 2073.456, which produced transactions dated 2073 and 2031.
_YEAR_LINE = re.compile(r"^\s*(20\d\d)(?![\d.,])")
_YEAR = re.compile(r"(?<![\d.,])(20\d\d)(?![\d.,])")
_SELL = re.compile(r"Withdraw|Redem|Sell|Switch Out", re.I)
_FY = re.compile(r"for FY (\d{4})-(\d{2})")
_TOT = re.compile(r"(Fresh Purchase|Withdrawal)\s*[+\-]?\s*₹?([\d,]+\.\d+)")

# Page furniture that sits in the same visual column as scheme names.
_JUNK = re.compile(r"Need Help\?|paytmmoney\.com|Statement generated|Page \d+ of \d+|"
                   r"Transaction Summary|Investment|Confirmed|Pending|Purchase|Withdraw|"
                   r"SIP|Folio|Scheme|NAV|Units|Amount|Status|Date", re.I)

_num = lambda s: float(s.replace(",", ""))
_norm = lambda s: re.sub(r"[^A-Z0-9]", "", (s or "").upper())
_FUNDWORD = re.compile(r"Fund|Plan|Index|ETF|FOF|Saver|Securities|Gilt", re.I)


def _clean_name(line):
    t = re.sub(r"\s{2,}", " ", line)
    t = _JUNK.sub(" ", t)
    t = re.sub(r"\((?:Equity|Debt|Hybrid|Other|Solution)[^)]*\)", " ", t)
    t = re.sub(r"₹[\d,\.]+|\b[\d,]+\.\d+\b|\b\d{4,}\b", " ", t)
    t = re.sub(r"^\s*\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b", " ", t)
    t = re.sub(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b", " ", t)
    return re.sub(r"\s+", " ", t).strip(" -–—·|")


def parse_text(text):
    """-> (rows, meta). Rows carry date, folio, type, units, nav, amount, status."""
    lines = text.split("\n")
    raw, unparsed = [], []
    for i, ln in enumerate(lines):
        m = _TAIL.search(ln)
        if not m:
            continue
        units, nav, amount, status = m.groups()
        window = lines[max(0, i - 3):i + 4]
        joined = "\n".join(window)
        folio = _FOLIO.search(ln[:m.start()]) or _FOLIO.search(joined)
        # A record split by a page break leaves its day and month on the previous page,
        # separated by the footer and header. Scan upward for the nearest preceding date
        # rather than widening the window blindly: rows run newest first, so the closest
        # date above a row is that row's own.
        date = None
        for k in range(i, max(-1, i - 14), -1):
            date = _DATE.search(lines[k])
            if date:
                break
        # Layout puts the year one or two lines below the day/month, at the line start.
        year = None
        for k2 in range(k, min(len(lines), k + 4)):
            year = _YEAR_LINE.match(lines[k2])
            if year:
                break
        if not year:
            year = _YEAR.search(ln) or _YEAR.search(joined)
        if not (folio and date and year):
            unparsed.append((i, ln.strip()[:90]))
            continue
        # Build the scheme name from its own columns, not from a vote over the folio:
        # one folio can hold several schemes. Folio 23284994 carries SBI Banking &
        # Financial Services, SBI Magnum Global and SBI Magnum Gilt, so keying a name to
        # a folio silently merges three funds into one holding.
        # A name occupies the column right of the date. When it is too long it wraps, and
        # the remainder lands on the money line ahead of the folio.
        # Two layouts, and they must not be blended. When the name fits, it sits on the
        # date line and often on the money line too — concatenating both yields "Axis
        # Small Cap Fund Direct-Growth Axis Small Cap Fund". When it does not fit, the
        # date line carries only the date, the name starts on the line above and finishes
        # on the money line ahead of the folio.
        head = _clean_name(lines[k][date.end():])
        if len(head) > 8:
            scheme = head
        else:
            pre = _clean_name(lines[i][:m.start()].split(folio.group(1))[0])
            above = ""
            if k and not _DATE.search(lines[k - 1]) and not _FOLIO.search(lines[k - 1]):
                above = _clean_name(lines[k - 1])
            scheme = " ".join(x for x in (above, pre) if len(x) > 2)
        scheme = re.sub(r"\s+", " ", scheme).strip(" -–—")
        # A name can also wrap across a page break, leaving only its tail ("Growth",
        # "Direct-Growth") on the next page with the footer in between. Reach back past
        # the furniture for the head of the name.
        if len(scheme) < 14 or not _FUNDWORD.search(scheme):
            for k2 in range(k - 1, max(-1, k - 12), -1):
                if _DATE.search(lines[k2]) or _FOLIO.search(lines[k2]):
                    break
                cand = _clean_name(lines[k2])
                if len(cand) > 10 and _FUNDWORD.search(cand):
                    scheme = re.sub(r"\s+", " ", f"{cand} {scheme}").strip(" -–—")
                    break

        raw.append({
            "date": f"{year.group(1)}-{MONTHS[date.group(2)]:02d}-{int(date.group(1)):02d}",
            "folio": folio.group(1),
            "type": "sell" if _SELL.search(joined) else "buy",
            "units": _num(units), "nav": _num(nav), "amount": _num(amount),
            "status": status, "scheme": scheme,
        })

    # Names still arrive with small variations between rows (a trailing "Direct" here,
    # a dropped "- Growth" there). Snap each row to the longest spelling seen for the
    # same folio that contains it, so one scheme does not split into near-duplicates.
    # The same fund is spelled several ways across rows — "Direct-Growth", "Direct
    # Growth", "Direct - Growth" — so compare on letters and digits alone. Names where
    # one is a continuation of another are the same scheme too, since a wrapped name can
    # lose its tail. The longest spelling seen becomes the display name.
    by_folio = collections.defaultdict(collections.Counter)
    for r in raw:
        if r["scheme"]:
            by_folio[r["folio"]][r["scheme"]] += 1
    canon = {}
    for f, c in by_folio.items():
        groups = collections.defaultdict(list)
        for n in c:
            groups[_norm(n)].append(n)
        # Within one normalised key, keep the spelling that appears most often.
        rep = {k: max(v, key=lambda n: (c[n], len(n))) for k, v in groups.items()}
        for key in groups:
            best = key
            for other in groups:
                if len(other) > len(best) and other.startswith(best):
                    best = other
            for n in groups[key]:
                canon[(f, n)] = rep[best]
    for r in raw:
        r["scheme"] = canon.get((r["folio"], r["scheme"]), r["scheme"]) or f"Folio {r['folio']}"

    names = {(r["folio"], r["scheme"]) for r in raw}
    meta = {"unparsed": unparsed, "schemes": sorted(names)}
    fy = _FY.search(text)
    if fy:
        meta["fy"] = f"{fy.group(1)}-{fy.group(2)}"
        # A statement covers exactly one financial year, so the month fixes the year:
        # April onward is the opening year, January to March the next. That is worth
        # applying rather than trusting what was scraped, because the last record on a
        # page has its year printed on the following page, and the nearest thing that
        # looks like a year is then the footer's "generated on 03/09/2026". Rows dated
        # 2026 inside a 2023-24 statement came from exactly that.
        start = int(fy.group(1))
        fixed = 0
        for r in raw:
            want = start if int(r["date"][5:7]) >= 4 else start + 1
            if int(r["date"][:4]) != want:
                r["date"] = f"{want}{r['date'][4:]}"
                fixed += 1
        meta["year_corrected"] = fixed
    for label, amt in _TOT.findall(text):
        meta["stated_buy" if label == "Fresh Purchase" else "stated_sell"] = _num(amt)
    return raw, meta


def canonicalise(rows):
    """Fold scheme-name variants together ACROSS statements, in place.

    parse_text can only reconcile spellings within the one file it is given, so a fund
    bought under "Invesco India ELSS Tax Saver Fund" in one year and sold under "Invesco
    India ELSS Tax Saver Fund Direct-Growth" in another stays split — and shows up as one
    holding with units it never bought and another with units it never sold. Runs the
    same normalise-and-extend rule over the whole batch.
    """
    by_folio = collections.defaultdict(collections.Counter)
    for r in rows:
        by_folio[r["folio"]][r["scheme"]] += 1
    canon = {}
    for f, c in by_folio.items():
        groups = collections.defaultdict(list)
        for n in c:
            groups[_norm(n)].append(n)
        rep = {k: max(v, key=lambda n: (c[n], len(n))) for k, v in groups.items()}
        for key in groups:
            best = key
            for other in groups:
                if len(other) > len(best) and other.startswith(best):
                    best = other
            for n in groups[key]:
                canon[(f, n)] = rep[best]
    merged = 0
    for r in rows:
        new = canon.get((r["folio"], r["scheme"]), r["scheme"])
        if new != r["scheme"]:
            merged += 1
        r["scheme"] = new
    return merged


def check(rows, meta):
    """Compare parsed totals against the statement's own summary. -> (ok, detail)."""
    ok = [r for r in rows if r["status"] == "Confirmed"]
    got_b = round(sum(r["amount"] for r in ok if r["type"] == "buy"), 2)
    got_s = round(sum(r["amount"] for r in ok if r["type"] == "sell"), 2)
    want_b, want_s = meta.get("stated_buy"), meta.get("stated_sell")
    good = (want_b is None or abs(got_b - want_b) < 1) and \
           (want_s is None or abs(got_s - want_s) < 1)
    return good, {"rows": len(ok), "buy": got_b, "sell": got_s,
                  "stated_buy": want_b, "stated_sell": want_s,
                  "unparsed": len(meta.get("unparsed", []))}
