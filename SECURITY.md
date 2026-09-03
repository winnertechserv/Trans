# Security

Trans handles brokerage data, so please treat issues here as sensitive.

## Reporting

**Do not open a public issue.** Use GitHub's private advisory form:
<https://github.com/winnertechserv/trans/security/advisories/new>

Include what you did, what happened, and what you expected. A proof of concept helps but
is not required to report something.

## What Trans does with your data

Worth knowing before you audit it, and worth preserving if you contribute:

- **Everything is local.** The database is a SQLite file in the repo directory. The server
  binds to `127.0.0.1` only, never `0.0.0.0`.
- **No secret is ever read from a config file.** The Anthropic API key comes from
  `ANTHROPIC_API_KEY` and the Paytm statement password from `PAYTM_PDF_PASSWORD`, both
  environment variables only. A secret in a JSON file gets copied, backed up, and pasted
  into chats.
- **Broker access is read-only.** Trans never calls an endpoint that places, modifies or
  cancels an order.
- **Nothing is uploaded** except a database snapshot to a Google Drive folder you
  configure, and only when you ask for one. There is no telemetry.
- **Personal data is kept out of git** by `scripts/check_clean.py`, enforced as a
  pre-commit hook. `sync/inbox` and `sync/archive` are ignored wholesale.

## Scope

In scope: anything that leaks portfolio data off the machine, exposes the server beyond
loopback, writes a secret to disk, or lets crafted broker data execute code.

Out of scope: the fact that `portfolio.db` is unencrypted at rest — it is your file on
your machine, and disk encryption is the operating system's job.
