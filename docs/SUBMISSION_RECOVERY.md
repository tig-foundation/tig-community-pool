# TIG submission and recovery service

The coordinator links the member queue to TIG using durable submission
intents. It runs independently of the API and block collector. The coordinator
can reconcile existing submissions while new work is paused.
[ROADMAP.md](ROADMAP.md) lists which submission paths have been validated
live and which have not.

## Durable submission sequence

Before a precommit, the pool downloads and saves the chosen binary, verifies
that it contains the needed architecture library/PTX, and checks that TIG's
current block still matches the immutable selection. It captures existing
benchmark IDs between two reads of that same head. A changed head cancels the
proven-unsent reservation and returns its collateral/fee commitment. The next
request receives a new selection and multiplier snapshot.

The database commits the potentially-sent marker before the HTTP call. Only one
process can begin that intent. Pending precommits with the same settings and
compute type are serialized even if their unselected track parameters differ:
TIG's random track choice can otherwise make their eventual records
indistinguishable. A timeout does not clear this fence or permit a resend.
TIG accepts one precommit, one benchmark and one proof per player every five
seconds, each endpoint counted separately, and refuses a second send inside
that window with HTTP 503. The coordinator therefore leaves an intent queued
while the newest send of the same kind is younger than
`precommit_interval_seconds` (default 6). If TIG still answers a precommit
with exactly that rate-limit reply from the configured origin, the pool treats
it as a refused, unrecorded send and releases the reservation at once
(`tig-rate-limited-precommit-v1`); a results or proofs intent refused that way
is never resent (one intent per reservation and kind, and a potentially sent
intent never returns to ready), so the work ends through the late-results rule
below when its lifetime elapses.

A successful precommit response stores the returned benchmark ID immediately
with its immutable reservation owner. Metadata fetches happen afterwards. If
the response was lost, recovery searches for exactly one matching precommit
that was absent from the captured baseline and is not already claimed by
another reservation. Multiple matches remain unresolved; zero matches resolve
only through the never-received rule below.

Publication waits for confirmed details, validates the chosen track, nonce
count, random seed, exact fee and creation height against the saved block, and
then publishes the assignment. Its `binary_url` points at the pool's saved,
checksum-addressed archive when the configured member origin is supplied.
Algorithm names inside TIG archives differ from their IDs; both are preserved.

Member result/proof uploads create immutable intents in the same transaction
as their stored payloads. The sender again commits uncertainty before writing.
Confirmed owned benchmark/proof records reconcile lost responses. A successful
proof POST is not an activation signal: a confirmed verification failure or
presence in the actual active benchmark set determines slot release.

## Recovery and held evidence

Every collector must be configured with the same pool player ID, so its pending
feed is saved even before the pool enters OPoW. Complete observations replay in
height order after service downtime; qualifying credits are recorded once.
Missing or inconsistent blocks remain held, and failed attempts rotate through
the queue so later complete blocks can progress. Ineligible compute offers also
rotate rather than permanently occupying the first queue page.

Activations and confirmed verification failures free slots while keeping all
collateral held for X+2 finalization. Disappearing records, elapsed wall time,
ambiguous HTTP failures and missing reports do not release funds. Explicit
definitive-rejection handling exists for a trusted protocol adapter; generic
HTTP 4xx responses are deliberately insufficient evidence. One narrow
late-results rule applies: the exact archived HTTP-400 missing-
precommit response, or the exact HTTP-503 rate-limit refusal, must match the
configured TIG origin, endpoint and raw hash (and, for the former, the
benchmark ID); a complete later pool feed must contain no record for it; the
original challenge lifetime must have elapsed. Any successful prior result
response or proof intent prevents this rule. It atomically marks the
reservation expired and result intent rejected (`tig-late-results-rejection-v1`
or `tig-rate-limited-results-v1`), freeing the slot while preserving collateral.
Other rejection/expiry paths still require their own definitive evidence.

### A member never returns results

Handed-over work also expires when its original challenge lifetime has strictly
elapsed in a validated later block, the challenge lifetime is unchanged, and a
complete pool feed contains no precommit, benchmark, proof or fraud record for
it. The original block must still replay and match the assignment's creation
height and block ID. An active benchmark prevents expiry.

Under the same reservation lock used by uploads and first sends, reconciliation
checks that no results could have been submitted: there is either no result
intent, or a `ready` intent with no send timestamp or response. Any proof intent,
authoritative sampled-nonce record, or possibly sent/accepted result prevents
this rule. Thus an HTTP timeout cannot be converted into an abandoned benchmark.

The transaction records `tig-unsent-results-expiry-v1`, marks the reservation
`expired`, frees its slot, and cancels any queued unsent result intent. Stored
payloads remain immutable. A first upload after expiry is rejected; a retry of
an identical already-stored upload remains idempotent and cannot restart its
cancelled intent. The coordinator checks the latest complete block before
dispatching payloads, including while new work is paused.

The financial consequences match the explicit rejected-late-result path:
collateral stays held until normal finalization after the end of X+2. Because
the handed-over benchmark never activated, finalization forfeits its original
held amount once into its creation round's pot. No qualifying credit is earned
and submission fees remain the operator's expense. This rule does not expire
an assignment that was never acknowledged or release funds early.

### TIG never received a precommit

A precommit send can time out without TIG recording anything. TIG confirms a
received precommit in the following block and keeps it in the pool player's
pending feed until the challenge lifetime ends, so absence from a complete
later feed becomes definitive once enough blocks have passed. The coordinator
applies `tig-unsent-precommit-v1` when a complete observed block is at least
five blocks past the preflight head and inside the first half of the challenge
lifetime recorded in that block, no positive identity was ever received, and
the feed holds no new precommit matching the immutable settings. The intent is
marked `rejected` and the reservation is released exactly like a definitive
rejection with no fee charged: collateral and the fee commitment return, the
slot frees, and the member's work request reports `rejected` so its worker
offers again. One matching candidate recovers the identity instead and several
keep the intent uncertain. Earlier blocks leave it untouched. Once the current
block is past the window, the newest archived complete block inside the window
supplies the feed instead, so the outcome does not depend on when the
coordinator runs; historical replay applies the same rule block by block.

Pausing new work cancels unsent precommits and permits existing payload recovery.
An uncertain operation is retained until that rule resolves it. Unused, unsent result/proof intents can be
retired only after a definitive benchmark outcome. A service lease never
reclassifies an external write as unsent.

## Development entry points

The database must first be migrated using the separate migration privilege.
Provide `POOL_V2_DATABASE_DSN` for the pool database only. The coordinator
entry point is:

```sh
python3 tools/run_tig_v2.py --config /absolute/path/coordinator.json
```

`submission_timeout_seconds` (default 20) bounds each TIG write; a longer value
only reduces lost responses, because the send fence does not depend on it.
`precommit_interval_seconds` (default 6, 0 disables) spaces precommit sends.
`release_manifest_file` names the served release manifest; new work requires it,
because the coordinator pins each active challenge's official runtime image at
the `runtime_version` it records and offers members only pinned challenges.
`runtime_image_refresh_seconds` (default 3600) is how often it asks the
registry again.
The JSON file includes `tig_api_url`, `pool_player_id`, `public_origin` (the
member API's HTTPS origin), `submissions_enabled` and `new_work_enabled`.
Both boolean flags default to false. Enabling new work requires enabled
submissions and a public artifact origin. Only the submission service receives
`POOL_V2_TIG_API_KEY` through its environment. The key is never included in
response evidence, logs, member assignments or runtime containers. `--once`
performs one cycle for development checks; it does not change the flags.

The observer is `tools/observe_tig_v2.py`, with
`--pool-player-id <configured-address>`. It needs no TIG API key. Neither entry
point performs startup migrations.

The writer supports only `/submit-precommit`, `/submit-benchmark` and
`/submit-proof`. Authenticated writes never follow redirects. Public artifact
GETs permit configured HTTPS hosts; the observed default redirects are limited
to TIG's own monorepo under `media.githubusercontent.com`. Credential headers
are never forwarded. The worker reads the pool's saved copy directly and
checks its SHA-256 before using it.

## Validation

Database tests exercise two simultaneous senders, delayed acceptance metadata,
lost responses, ambiguous matches, stale choices, queue fairness, pause,
recorded verification failures, delayed activation and persistent collateral.
They also cover artifact integrity and serving, restricted public redirects,
and replay that continues past held blocks. The paired worker commit is pinned
in pool CI. No test writes to TIG or Ethereum.

Recorded read-only downloads verified these live artifacts:

| Algorithm | Library name | Compressed bytes | SHA-256 |
|---|---|---:|---|
| `c008_a050` (CPU) | `titan_killer` | 4,804,855 | `efc1913b60cd83908e1132fd2bc58ee9282bff2f3840c71a715fe6d0f971b967` |
| `c006_a047` (GPU) | `parallax_vega` | 2,754,088 | `88c59421861e560194003e8ca6e80beee00d173406ec01c83c258dda52931253` |

Both include AMD64 and ARM64 libraries; the GPU archive also includes PTX.
The sources were TIG's public `get-binary-blob` endpoint, with the 307 redirect
into the corresponding TIG GitHub artifact. The saved manifest is
`tests/v2/fixtures/artifact-manifest.json`; the actual archives are temporary
local verification files, not committed binaries.

`confirmed-verification-failure.json.gz` retains the complete compact records
for public benchmark `df559c2aa698bfb4d5aa04df761a3263`, captured at height
1,351,102. Proof and fraud confirmation at 1,351,066 precede its planned
activation at 1,351,079, demonstrating why proof receipt alone is insufficient.
Its owner and public source are retained in the fixture. This failure feed is
separate from later per-nonce arbitrations.

Real CPU/GPU benchmark execution on the members' hardware, intended-account
submission/rejection and expiry validation, final reward receipt mapping and
deployment configuration are each deployment's own checks;
[ROADMAP.md](ROADMAP.md) lists which have been validated and which have not.
