# Known limitations and roadmap

This page is the single list of what the pool does not do yet, what has and
has not been validated and the evidence behind that, and which decisions every
operator has to make. Other documents point here instead of carrying their own
validation records or "remaining work" notes.

## Not implemented

| Area | Limitation |
|---|---|
| Reward intake | The operator claims and unlocks TIG rewards outside the software and records the receipt by hand; there is no claim/unlock/send workflow and no automatic attribution of a receipt to a reward round. |
| Operator corrections | Reconciliation of unexpected custody movements is recorded through audited manual entries; there is no operator correction interface. |
| Member login | Only externally owned accounts can sign in. Contract-wallet signature verification and payments to smart accounts are not supported; relayed smart-account withdrawals are recovered but new ones are held. |
| Member administration | There is no per-member suspend or ban control. The operator can change a member's collateral multiplier and pause all new work. |
| Failover | The recovery host holds verified backups and a staged application, but takeover is a manual, rehearsed procedure, not automatic failover. |
| Worker deadlines | The worker's per-nonce timeout is not a whole-benchmark deadline. Operators must measure each challenge and hardware combination against TIG's benchmark lifetime before offering it to members. TIG delays activation by about 1.2 times the submission delay, so results must reach TIG within roughly 54 blocks of the precommit in a 120-block lifetime; a slower benchmark expires without activating (see the lifetime-end rule in [SUBMISSION_RECOVERY.md](SUBMISSION_RECOVERY.md)) and its fee is lost. |
| Helper provisioning | The member master mode uses helper machines the member has already provisioned with SSH access, Docker and the pinned worker; the installer does not set helpers up. |
| Database space | Compaction frees rows, not disk; reclaiming space after large deletions needs a `VACUUM FULL` or `pg_repack` maintenance window. |
| Resend after a refused send | A results or proofs submission that TIG refuses (its per-player rate limit answers HTTP 503) is not resent: the schema keeps one intent per reservation and kind and never returns a potentially sent intent to ready. The coordinator spaces sends to avoid the refusal; a refused results send ends the work through the late-results rule at the end of the challenge lifetime, with the member's collateral following the expired-work policy. Allowing a retry intent after a pinned refusal is a schema change for a later release. |

## Validated so far, and not yet

| Check | State |
|---|---|
| CPU benchmarks on mainnet | Completed end to end with the official pinned runtimes, on a single machine and on a master with one SSH helper, including TIG activation and credit recording. Rewards for those benchmarks were zero, so only the zero-earnings settlement path has been exercised in production. |
| GPU benchmarks | Completed end to end for vector search on 19 October 2026: benchmark 9e884acbc2d3160fb6b69874c667d256 (68 nonces, track n_queries=9000) ran on a member's RTX 3070 through the pool, was accepted by TIG with four active bundles and no fraud, and became active at block 1391173. The neural-net optimiser was timed on the same hardware and passed the local verifier but has not yet been submitted through the pool; hypergraph is too slow for TIG's benchmark lifetime and stays unoffered. |
| Challenge timing | Implemented: the operator ticks the challenges timed on members' hardware in the Offered challenges card, and selection skips every other challenge. Without a saved list the pool still offers every active challenge of a resource family. |
| Settlement with real rewards | Mapping an actual token receipt to a reward round, and the full reward cycle through the TokenLocker's pending period, have not been exercised with a qualifying benchmark. |
| Verified-binary reuse | The coordinator records checked archives for reuse; the reuse path has not yet served a production assignment. |
| Interruption during live compute | Worker restart and helper disconnect recovery passed rehearsals; interruption of a paid benchmark in flight has not been exercised in production. |
| Expiry without activation | Rule written from two live cases of 19 October 2026 and not yet validated live: CPU job-shop benchmark 262238615af60e3e736a17f0e4aa9fd2 (precommit at block 1391793, proof confirmed at 1391880 with activation set for 1391984, past the lifetime ending at 1391913) and GPU vector-search benchmark 8ab005fb73b8425e375e624e1f1baa65 (results confirmed at 1391800, the proof send timed out after 60 seconds and TIG never confirmed a proof). Both reservations stayed `accepted` with their slots held until the rule shipped. |
| Never-received precommit | Validated live on 19 October 2026: a CPU precommit send timed out at 14:59 UTC and TIG never recorded it; the release that shipped the rule released the reservation on the coordinator's first cycle from archived block 1391379 (evaluated at block 1391427), returning the member's collateral and slot with no fee charged, and the member's worker was reserved new work within a minute of resuming. |
| Funded takeover | Restoring the recovery host's backup and starting the application there has been rehearsed in isolation, not as a funded takeover with members connected. |
| Member master with SSH helpers | A two-server rehearsal on ARM64 hosts, six local plus six SSH helper workers on the same pinned source and images, completed a 200-nonce knapsack assignment and three sampled proofs in about fifteen minutes through a deliberate helper disconnect, a master kill and a restart; every saved output was reused without recomputation. Paid mainnet benchmarks have since completed on a master with one helper. |
| Restore rehearsal | A restore from a real primary backup replayed its post-backup transaction; the evidence audit verified every chunk digest and manifest reference of a raw evidence copy on the recovery host; a migration was applied and replayed against a restored funded pilot database with balances, collateral holds, pending withdrawals and journal history unchanged. None of these moved a token. |
| Protocol inputs and credit arithmetic | Two consecutive mainnet blocks from round 135 are recorded in `tests/v2/fixtures` with every active benchmark covered; the validator reproduces exact equal-sharing credit from them and parses two rounds of public reports and arbitrations. These are recorded fixtures, not observations of a live deployment; see [PROTOCOL_VALIDATION.md](PROTOCOL_VALIDATION.md). |
| Custody observation | Offline verification reproduces a recorded public token transfer and wallet balance change from archived Base RPC responses; see [CUSTODY_OBSERVER.md](CUSTODY_OBSERVER.md). |
| Pilot resource limits | An offline Docker test showed CPU throttling and a container-local out-of-memory termination under the pilot slice; it submitted no TIG work and touched no collateral. |

## Decisions every operator makes

| Decision | Notes |
|---|---|
| Spending cap | Required before any fee or gas is committed. Choose the rolling window, the per-window ceilings for TIG submission fees and native gas, and the per-attempt ceilings. |
| Operator payouts | Optional. Configure an income wallet and a retained operating reserve to withdraw operator earnings. |
| Offered resources | CPU and/or GPU, after validating execution on the hardware members will use. GPU on AMD64 with an NVIDIA runtime has been validated for vector search. |
| Offered challenges | Which challenges of those resources members receive; only ones timed against TIG's benchmark lifetime on the members' hardware, set from the operator page. |
| External alerts | The health check writes a status file; forwarding it to email, chat or a pager is the operator's choice. |
| Evidence copies | Whether the recovery host also keeps a raw copy of the primary's block and report spools, in addition to the database backups and pending-file snapshots. |
| Independent recovery collector | Whether the recovery host observes TIG independently so that a takeover has its own evidence stream. |

## Identifiers kept for compatibility

The pool is Community Pool. Some identifiers still say `innopool`, `v2` or
`legacy` because deployed hosts, member workers, backups and recorded data
depend on them. Renaming any of them is a paired release with its own rollback
plan, not a text edit. Until that release, they stay as listed here.

| Identifier | Where it lives | Why it stays |
|---|---|---|
| `X-InnoPool-Version` header | Member protocol, website, every worker | Every deployed worker sends it on every work route. |
| `innopoolv2mainnet-*` units and `innopoolv2mainnet.slice` | Both hosts, `deploy/v2-mainnet/` templates | Installed units; the release and operations scripts name them. |
| `/opt/innopool-v2-mainnet`, `/etc/innopool-v2-mainnet`, `/var/lib/innopool-v2-mainnet*` | Both hosts | Release, settings, spool and backup paths. |
| Database `innopool_v2_mainnet`, schema `pool_v2`, roles `innopool_owner`, `innopool_runtime`, `innopool_observer`, `innopool_replication`, slot `innopool_germany_archive` | Primary and recovery databases | They hold the ledger and the backups. |
| Unix account `innopoolmainnet`, group `innopoolbackup` | Both hosts | File ownership of releases, spools and backups. |
| `innopool-v2-member` install directory, `installation-v2.json`, marker kind `innopool-v2-worker` | Every member machine | The installer refuses a directory whose marker differs. |
| Container labels `innopool.v2.owner` and `innopool.v2.benchmark`, container names `innopool-v2-<namespace>-<key>`, transient units `innopool-member-helper-*` | Member machines and helpers | A worker manages only containers carrying its own labels; renaming orphans benchmarks in flight. |
| Packages `pool_manager.pool_v2` and `worker_v2`, `tools/*_v2.py`, `deploy/v2-mainnet/`, `tests/v2/`, `requirements-v2.txt` | Both repositories | Unit `ExecStart` lines, the installer's launcher, members' helper commands, CI and the release tooling reference these paths. |
| `POOL_V2_*` environment variables | Host environment files, members' `POOL_V2_EXECUTION_TOKEN` | Configuration keys on every host and member machine. |
| Advisory lock seed `innopool-v2:` in `database.py`, `application_name="innopool-v2"` | Pool processes | Every process must derive the same lock keys; change only in a release that restarts all of them together. |
| Logger names and User-Agent strings (`innopool-v2-*`), cache-buster `_innopool_read` | Pool and worker processes | Harmless to rename, but worker strings ship only through a paired release. |
| Stored values `legacy-compact-record-order-v1`, `legacy_*` proof fields, `pool-scoped-v2`, `pool-network-v1`, `evidence-retention-v2`, `copy-best-active-bundle-v2`, `tig-testnet-starter`, `testnet-cpu`, `innopool-pending-evidence-v1` | Database rows and archived evidence | Recorded data; migrations are checksummed and cannot be edited. |
| nginx variables `$innopool_cloudflare_peer`, `$innopool_asset_cache_control` | `deploy/v2-mainnet/nginx.conf.example`, host nginx | Defined by the host-generated Cloudflare include. |
| Branch `release/v2` | Both repositories | CI triggers and the release procedure follow it. |
| `pool_manager/requirements-v2.txt`, `deploy/v2-mainnet/grant-runtime-observer-privileges.sql`, `tools/install_worker_v2.py` | Both repositories | The release step checks the first two are byte-identical to the previous release and records the installer's checksum, so even a comment change waits for a release that expects it. |
