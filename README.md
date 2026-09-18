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

## Who may connect

`MCP_AUTH` picks the provider: `none` (default, right for stdio), `jwt`, or
`oidc` against any OpenID Connect issuer.

**Signing in is not the same as being allowed in**, and which of those you get
depends on the provider.

An *organisational* provider answers both at once. If someone is assigned to
the app in Okta or Entra, they are permitted; membership is the decision.

A *consumer* provider answers only the first. Anyone in the world with an
Intuit account completes a Sign in with Intuit flow successfully, and Intuit
exposes no way to ask whether they have anything to do with the company whose
books this server reads. Intuit's own single sign-on documentation is explicit
that the app maps Intuit identities to its own users — authorization is yours
to decide, not theirs to report.

So with a consumer provider, this is not optional:

```bash
MCP_OIDC_ALLOWED_SUBJECTS=1182d6ec-2a1f-4aa3-af3f-bb3b95db45af,...
```

Unlisted callers are refused at the token verification boundary, before any
tool runs. The server warns at startup if `oidc` is configured with no list.

**Key it on the subject, not an email.** Intuit's own guidance: *"an Intuit
account can have multiple emails at various points in time. However, the sub
value never changes."* `MCP_OIDC_ALLOWED_EMAILS` exists too, and an address is
refused when the provider reports it unverified — an unverified address may
belong to somebody else, which is the whole reason subjects are the better key.

### Signing in with QuickBooks credentials

Intuit is itself an OpenID Connect provider, so users can sign in with the
QuickBooks credentials they already have and you need no identity provider of
your own:

```bash
MCP_AUTH=oidc
MCP_OIDC_CONFIG_URL=https://developer.api.intuit.com/.well-known/openid_configuration
MCP_OIDC_SCOPES=openid,email,profile
MCP_OIDC_ALLOWED_SUBJECTS=<sub>,<sub>
```

**Request only those scopes.** Adding `com.intuit.quickbooks.accounting` would
make every sign-in a fresh company authorization, and Intuit disconnects the
previous one when that happens — each person signing in would break this
server's own connection to the ledger.

A caveat worth stating plainly: the ID token's `realmId` claim looks like it
would let the server verify a user belongs to *this* company, which would be
better than any list. It does not work. Intuit documents that `realmId` "is
returned whenever apps specify the QuickBooks Online API or Payments API scopes
in authorization requests" — so obtaining it requires exactly the scope that
breaks the connection. The list is the answer.

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

### Enabling writes

Two independent brakes, and they work at different levels.

**`QBO_READ_ONLY`** decides whether writes are *permitted*. It defaults to
`true`, and the write tools still appear — calling one returns:

> Refusing POST journalentry: this client is in read-only mode.
> Set read_only=False (QBO_READ_ONLY=false) to permit writes.

The tools stay visible on purpose, so the refusal can say what to change.
Hiding them would leave you wondering why the server cannot do something this
README says it does.

**`--groups`** decides whether the write tools *exist*. It defaults to `all`,
which includes them.

To permit writes, set the variable:

```bash
QBO_READ_ONLY=false
```

To remove them entirely instead, leave the group out:

```bash
./main.py --groups core,accounts,reports,sales,expenses,sync
```

The second is the stronger control, and the two fail differently. A mistyped
variable — `QBO_READ_ONLY=flase` — silently permits writes; a tool that was
never registered cannot be called whatever the environment says. A deployment
with no business writing should do both.

Setting one without the other does nothing useful: the group without the flag
gives you tools that always refuse, and the flag without the group gives you
nothing to permit.

**Defaults differ by layer, deliberately.** The SDK is a library and lets its
caller write unless told otherwise (`QboClient(..., read_only=True)`). This
server refuses by default, because a model calling tools is a different
proposition from code someone wrote on purpose. A deployment pointed at a live
general ledger should be stricter still — see the deployment wrapper, which
turns both brakes on and expects you to turn them off deliberately.

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
| `QBO_READ_ONLY` | defaults **true**; refuses writes before any request is built. See *Enabling writes* |
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
