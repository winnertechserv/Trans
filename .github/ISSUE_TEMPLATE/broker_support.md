---
name: Broker support
about: Ask for a broker to be supported, or offer to add one
labels: broker
---

**Broker and market**

**How does it expose data?**
- [ ] An MCP server
- [ ] An API
- [ ] File export only (CSV / Excel / PDF)
- [ ] Not sure

**What does the export contain?**
Dated transactions? Current holdings? Live prices? A stable identifier such as an ISIN?
These decide how much of the app will work — see `docs/IMPORTING.md`, which explains what
each is needed for.

**Does the export publish its own totals** (a purchase or withdrawal summary) that a
parser could reconcile against?

**Are you able to test?** Nobody can add a broker they cannot get real files from.
