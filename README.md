# quickbooks-online-mcp

MCP server with complete coverage of the **QuickBooks Online Accounting API v3**.

Built on [`quickbooks-online-sdk`](https://github.com/rgellis/quickbooks-online-sdk),
which generates its entity registry and models from Intuit's published
documentation and verifies that coverage mechanically.

## What this is not

It holds no credentials of its own and knows nothing about whose books it
reads. Which Intuit app, which company, and how the authorization is performed
are decisions for whoever deploys it. Point it at one QuickBooks company; to
serve several, run it more than once.

## Access is a property of the deployment, not the caller

**QuickBooks issues one OAuth token per app per company.** Only an admin can
authorize an app, and a second admin connecting *disconnects the first*.
Intuit's own guidance is to authorize once as master admin and map your users
to that single token. There is no per-user token and no per-user enforcement.

So every call this server makes carries the same company-wide access, whoever
asked. This server does not pretend otherwise. There is no role file and no
per-caller gate, because a gate here would resemble QuickBooks permissions
without being them — and something that looks like "Jason has read-only access
to the books" while the server holds full access is worse than no control at
all.

What genuinely differs is what a **deployment** can do:

| | |
|---|---|
| `QBO_READ_ONLY` | refuses every write before a request is built |
| `--groups` | which tools exist; omit `writes` and there is nothing to call |
| `MCP_AUTH` | who may reach this server at all |

**If two people need different access, run two deployments.** Separate Intuit
apps, separate tokens, separate configuration — a read-only instance and a
read-write one, and people connect to whichever matches their authority. That
is how the API is built to work, and it is the only arrangement where the
restriction is real rather than advisory.

Identity is still recorded: every write is logged with the caller from the
verified token. Knowing who asked is worth having even when everyone who can
ask could have asked for anything.

## Tools

24 tools in seven groups, selected with `--groups`.

| Group | Tools |
|---|---|
| `core` | `check_connection`, `query_quickbooks`, `get_entity`, `list_entities`, `describe_entity`, `list_reports` |
| `accounts` | `list_accounts` |
| `reports` | `get_report`, `get_profit_and_loss`, `get_balance_sheet`, `get_general_ledger`, `get_trial_balance` |
| `sales` | `list_invoices`, `list_customers`, `list_payments` |
| `expenses` | `list_bills`, `list_vendors`, `list_purchases` |
| `sync` | `get_changes` |
| `writes` | `create_entity`, `update_entity`, `delete_entity`, `void_transaction`, `create_journal_entry` |

Named tools cover the common path; `query_quickbooks` reaches everything else,
so anything a structured tool does not support goes there rather than accreting
another parameter.

Omitting `writes` is stronger than any flag: the tools are not registered, so
there is nothing to call.

### Every response says where it came from

`source`, `asOf`, and `derived` on everything; `period` where one applies;
`count`/`returned`/`omitted` on anything list-shaped, so a truncated answer
cannot be mistaken for a complete one. Money is a decimal string, never a
float.

### Reports are checked before they are returned

A report whose stated total its own line items disprove raises instead of being
handed back, as does a balance sheet that does not balance. The figure is
withheld, because a number that fails its own arithmetic is wrong rather than
uncertain.

## A QuickBooks behaviour worth knowing

Some fields cannot be filtered or sorted on, and QuickBooks does not
consistently say so. `ORDER BY AcctNum` returns HTTP 400. Filtering `Purchase`
by `PaymentType` returns **zero rows** — against a company with 202 credit card
purchases, with no error anywhere.

A silently empty result reads as a fact, which makes it worse than a rejection.
So the documented capability of every field is checked before a query is sent,
and a refusal names the fields that can be used instead.

## Requirements

Docker. Nothing else — no Python, `uv` or Homebrew on the host.

```bash
./scripts/dev check                  # ruff + pyright (strict) + pytest
./scripts/dev run ./main.py --list-tools
./scripts/dev shell
```

## Configuration

Copy `.env.example` to `.env`.

| | |
|---|---|
| `QBO_CLIENT_ID` / `QBO_CLIENT_SECRET` / `QBO_REALM_ID` | required; one company |
| `QBO_TOKEN_STORE` | where the rotating refresh token lives |
| `QBO_READ_ONLY` | defaults **true**; refuses writes before any request is built |
| `QBO_MAX_ROWS` | rows returned in full before a response is summarised |
| `MCP_TRANSPORT` | `stdio` (default) or `http` |
| `MCP_AUTH` | `none` (default), `jwt`, or `oidc` — who may connect |

Seed the token store with the SDK's `scripts/get_refresh_token.py`. This server
never runs the authorization step.

## Testing

```
220 tests · 100% statement and branch coverage · pyright strict, 0 errors
```

No test reaches QuickBooks.

## License

MIT
