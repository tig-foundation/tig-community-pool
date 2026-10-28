# Member and operator screens

The API serves its own static website at `/`, `/join` and `/operator`.
Deploy it behind HTTPS at the exact origin configured for wallet signatures.
Assets are local; no third-party scripts or analytics are required.
The screens carry the Community Pool identity; the name, mark, colours and
the rules for member-facing wording are in the [branding guide](BRANDING.md).

The `/join` address opens the member account with the **Connect your master**
guide shown above it. The header lists only **Member account** and **Operator**;
the account page links to the guide. The guide covers account
setup, the deployment's API address,
execution tokens, work requests and confirmed assignment handover, plus result
and proof submission and troubleshooting. Links are visible before and after
sign-in. Members configure their own helper machines; website concurrency only
configures the separately labelled single-machine reference installer. See the
[member setup guide](MEMBER_SETUP.md).

## Member account

Members sign a one-time message with their wallet. The page shows available
TIG, collateral, pending withdrawals, the current multiplier, benchmark holds
and slots, exact qualifying credit, and settled rewards. TIG amounts use integer
base units through the API and browser; they never pass through floating-point
currency arithmetic. Pending credit is shown separately from spendable funds.
Benchmark rows retain their original multiplier after an operator changes it.

Members can request a full-amount withdrawal, cancel a request that has not
entered a send attempt, verify a new destination with that wallet's signature,
and issue or revoke execution tokens for workers. Revocation does not require
retaining a token's original secret; a wallet session can list and revoke its
own active token identifiers. The server enforces ownership,
available funds, one pending withdrawal, and the seven-day interval after a
successful payment. Execution tokens cannot authorize financial actions.

Session, operator and newly issued execution tokens stay in page memory. They
are not written to browser storage. Leaving the page clears them, and restoring
a page from the browser's back/forward cache requires a fresh login. Financial
responses and the HTML shell use `Cache-Control: no-store`. The API remains the
authority for every action; hiding a button does not grant or remove access.

## Operator controls

The operator page uses the configured bearer token and shows aggregate ledger
accounts, members, collateral, withdrawal reviews, round settlement status,
uncertain protocol submissions, collection alerts and multiplier changes.
Member/hold activity is paginated. Recent round and alert summaries are bounded.
Member API projections only contain the authenticated member's activity.

An operator can change a member's multiplier with an audit reason, review or
reject withdrawals, prepare a manual payment and reconcile its confirmed token
event. Preparing a payment durably reserves its nonce and fee budget before
showing the custody route. The page does not send or sign custody transactions.
An uncertain attempt can be reopened and recovered from its recorded nonce;
closing a dialog does not release funds or permit a second payment attempt.

An operator can also return part or all of a finished benchmark's held
collateral before finalization, with an audit reason; the page lists these
releases with the member, benchmark, amount and reason. Work still in flight
cannot be released.

The submission funding panel shows the observed TIG fee balance alongside its
recorded backing. The operator can reserve a manual fee top-up using available
operator funds, recover its transaction, and check positive TIG credit. A
verified outgoing transfer stays separate from spendable submission credit
until the protocol confirms it. See [protocol funding notes](PROTOCOL_FUNDING.md).

Round posting requires a calculated preview. Its fingerprint is checked again
inside the posting transaction. Changed evidence requires a fresh preview;
replaying the same approved allocation cannot credit the round twice. Wallet
addresses identify the member allocations displayed for review. Finalizing a
benchmark's collateral still requires authoritative outcome and post-X+2
arbitration evidence. The operator cannot bypass these checks from the screen.

## Runtime images

Which challenges members receive is not an operator setting. Within an offered
resource the pool hands out every active challenge whose official runtime image
it has pinned; a challenge without a pinned image is never selected, because
members' workers run only a pinned official image. The coordinator pins the
images itself: it reads `runtime_version` from the manifest named by
`release_manifest_file` in its configuration (new work refuses to start without
it), takes each active challenge's name from TIG's challenge configuration in
the newest complete snapshot, asks the registry which content digest that
runtime tag names, and appends the result to the `runtime_images` table
(migration 029) whenever it changes. It asks again every
`runtime_image_refresh_seconds` (default 3600). Each assignment carries the
image for its challenge as `runtime_image`; members pull it themselves and the
pool downloads nothing. The operator page lists the current pins read-only,
`GET /api/v2/runtime-images` serves them with challenge names and resolution
times, the served release carries the same map as `runtime_images`, and the
public capabilities route reports the ids as `offered_challenges`. Each
reservation's evidence records `pinned_challenges`, `excluded_challenges` and
`runtime_image`. A challenge TIG adds is offered as soon as its image is
published at the recorded runtime version; moving to a new TIG runtime means
changing `runtime_version` in a release. The per-challenge operator toggle
introduced with migration 028 was removed on 20 October 2026; the widened
runtime-control names stay in the schema and any recorded `offer:<id>` rows
are inert history.

## Pausing new work

`POST /api/v2/operator/controls/new-work` takes `paused`, `reason` and an
`event_key`. The change is audited and idempotent. Replaying an old pause event
does not undo a later resume: a request whose key was already recorded returns
the recorded event with `replayed: true` and applies nothing, so a tool that
reuses a key must not assume the value is now in force. Use a fresh key for
every intended change and check the capabilities route afterwards. Migration
007 adds the control and immutable history.

New collateral reservations and first precommit sends check the control under
the shared protocol-budget lock. An already committed send can still finish;
the pause is not a cancellation of in-flight requests. Unsent precommits can be
cancelled safely. Existing handover, result/proof upload, protocol recovery,
collateral accounting and withdrawal reconciliation remain available. Resuming
this control cannot enable work disabled by deployment configuration.

## Validation and remaining rollout work

The browser suite uses a real Chromium browser, isolated HTTPS FastAPI service,
PostgreSQL, generated signing wallets and simulated canonical chain receipts.
It checks exact 18-decimal amounts, wallet login, withdrawal review and payment,
worker authority, multiplier snapshots, work pause/resume, audit text escaping,
session storage, and desktop/mobile layouts. CI installs the pinned browser and
requires this test. Local invocation:

```sh
python3 -m pip install -r tests/v2/requirements.txt
python3 -m playwright install --with-deps --only-shell chromium
POOL_V2_BROWSER_TESTS=1 POOL_V2_TEST_DSN='<isolated database ending in _test>' \
  python3 -m unittest discover -s tests/v2 -p test_dashboard_browser.py -v
```

Set `POOL_V2_BROWSER_ARTIFACTS` to a local directory to save desktop and mobile
screenshots using fixture accounts only. Never run this test against a deployed
pool database: it deliberately recreates its isolated test schema.

Funds, work and settlement capability flags default to disabled. This screen
does not establish live reporting scope, final reward-receipt attribution or
definitive benchmark expiry, and it does not override the work pause, custody
readiness or settlement evidence gates. [Custody observation](CUSTODY_OBSERVER.md)
adds continuous finalized-deposit indexing, wallet checks and incoming funding
review; the evidenced benchmark expiry rules are in
[submission recovery](SUBMISSION_RECOVERY.md). The worker connection page
supports the [paired installer](PAIRED_RELEASES.md), and keeps it unavailable
until checked release metadata and installer bytes are explicitly configured.
[ROADMAP.md](ROADMAP.md) lists what is not implemented or not yet validated,
including the live finalization and expense adapters, unconfirmed deposit
views and funded takeover.
