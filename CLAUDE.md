## Objective

MCP server over the QuickBooks Online Accounting API v3, built on
`quickbooks-online-sdk`. Public and MIT: it knows nothing about any particular
company, and deployment concerns belong outside it.

## Architecture

- `main.py` — `TOOL_GROUPS`, `--groups`, transport and auth wiring
- `src/settings.py` — configuration, read once from the environment
- `src/client.py` — the shared SDK client, built lazily
- `src/auth.py` — inbound authentication: `none`, `jwt`, or any OIDC provider
- `src/identity.py` — the caller, from the verified token; for the log only
- `src/query.py` — query building, escaping, and the capability guard
- `src/shaping.py` — response envelopes, truncation, decimal money
- `src/coverage.py` — tool-to-capability map, asserted by the tests
- `src/services/` — one module per domain

## Rules

1. **Everything runs in Docker.** `./scripts/dev check` is the entry point.
   There is no Python toolchain on the host and none is required.
2. Coverage is pinned at **100%**, statements and branches.
3. pyright runs in **strict** mode. Decoded JSON is `Any`; narrow it rather
   than indexing raw values.
4. Every tool goes through `execute(...)`, which authorizes before it runs and
   turns any failure into a described error rather than a stack trace.
5. New tools are added to `src/coverage.py` in the same change, or the suite
   fails.

## Critical rules

- **Do not add per-caller authorization.** QuickBooks issues one token per app
  per company and cannot tell callers apart. A gate here would look like
  QuickBooks permissions without being them, which is worse than none: it
  invites the belief that someone is restricted in the books when they are not.
  Differing access means differing deployments — separate tokens, separate
  `QBO_READ_ONLY`, separate `--groups`. Identity is logged on writes, never
  enforced on.
- **Never filter or sort on a field the registry does not mark as such.**
  QuickBooks answers `WHERE PaymentType = 'CreditCard'` with zero rows against
  202 matching purchases, and no error. `build_query` refuses; do not bypass it
  by hand-assembling a statement.
- **Never infer an account's role from its name.** Use `AcctNum` and
  `AccountType`. Files routinely name a bank account "Receivable".
- **Report names are not route names.** Nine differ; the SDK resolves either.
- **Money stays decimal.** `jsonable` renders `Decimal` as a string. A float
  would be a different number from the one in the ledger.
- **Structured tools take only the filters they document.** Anything else goes
  through `query_quickbooks`. Do not add one-off parameters to `list_*` tools.

## Gotcha worth knowing

`sitecustomize.py` at the repository root imports `key_value.aio` before
anything else runs. That package — a FastMCP dependency — installs a beartype
import hook when imported, and if coverage is already tracing when that
happens, beartype's own modules deadlock on a circular import and the entire
suite fails to collect. Importing it at interpreter startup, before coverage
exists, is what prevents that. Deleting the file breaks `./scripts/dev check`
with an error that points at beartype and not at this.

`docker-compose.override.yml` is gitignored and puts a local checkout of the
SDK ahead of the pinned dependency. Delete it to test against what is published.
