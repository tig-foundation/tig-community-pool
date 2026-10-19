# Community Pool

A member-run mining pool for [The Innovation Game](https://tig.foundation) (TIG).
Members connect their own machines and receive whole benchmarks, which the pool
submits under its own TIG account. The pool watches TIG's blocks to credit the
bundles that qualify, holds member balances and collateral in a custody wallet
on Base, settles each round's rewards with equal credit per qualifying bundle,
and pays operator-reviewed withdrawals. Every movement of money is a balanced,
append-only journal, and nothing is spent beyond an operator-recorded cap.

The system is two repositories: this one, the pool, and the
[member worker](https://github.com/tig-foundation/tig-community-pool-worker),
which members install from the pool's Join page. A deployment is two hosts.
The primary host serves the website and the member API and runs the
coordinator and collectors; the recovery host keeps verified backups and
pending-file snapshots for a manual takeover.

## How it works

- **Whole benchmarks.** A member offers CPU or GPU capacity with a TIG
  verification compute type. The coordinator selects a challenge and algorithm
  from the live network, reserves a fee ceiling and the member's collateral,
  sends the precommit, and hands the complete benchmark to the member. The
  member computes every nonce, keeps durable evidence, and returns the result
  and TIG's sampled proofs through the member API.
- **Observation and credit.** Collectors record the pool's own feed from every
  TIG block and fetch whole-network references on demand. Qualifying bundles
  earn equal credit; conflicts between captures are resolved with appended
  proofs, never by editing history.
- **Funds.** Deposits are matched to verified member wallets, collateral is
  held per benchmark and returned or forfeited on the recorded outcome, and
  withdrawals are reviewed by the operator and reconciled against the chain.
  The software never holds a signing key; the operator signs sends outside it.
- **Settlement.** Reporting rounds are scoped and sealed after TIG's reporting
  delay, earnings are declared with evidence, and each round's pot is allocated
  to members by credit with the remainder to the operator.
- **Safety.** A recorded spending cap bounds submission fees and custody gas
  per rolling window and per attempt. New work can be paused durably. The
  recovery host keeps verified database backups and pending-file snapshots.

## Documentation

Start here:

- [Owner's guide](docs/OWNER_GUIDE.md): what the operator controls, on the
  website and on the server
- [Deployment](docs/MAINNET_DEPLOYMENT.md): preparing both hosts from the
  templates and enabling the controls one at a time
- [Paired releases](docs/PAIRED_RELEASES.md): how a pool commit, its worker
  commit and the checked installer ship together
- [Roadmap](docs/ROADMAP.md): what is not implemented or validated yet, and
  the decisions every operator makes

Operating the pool:

- [Backups and recovery](docs/BACKUP_OPERATIONS.md): database bases, WAL and
  takeover on the recovery host
- [Pending-evidence snapshots](docs/PENDING_EVIDENCE_BACKUP.md): protecting
  protocol files that have not yet entered the database
- [Storage map](docs/STORAGE_MAP.md): where pool data lives and how it reaches
  the recovery host
- [Lean storage](docs/LEAN_STORAGE.md): pool-scoped capture, selection
  references and cleanup of the earlier full-network archives
- [Evidence retention](docs/EVIDENCE_RETENTION.md): the cleanup policy for
  retained captures
- [Cloudflare in front of the pool](docs/CLOUDFLARE_SETUP.md): proxying the
  website and API hostnames
- [Pool structure](docs/POOL_STRUCTURE.md): how the addresses, proxy and
  services fit together
- [Operator income](docs/OPERATOR_INCOME.md): paying the operator's share to a
  separate income wallet
- [Protocol fee funding](docs/PROTOCOL_FUNDING.md): topping up the TIG
  account's prepaid submission balance
- [Custody observer](docs/CUSTODY_OBSERVER.md): recording and reconciling
  finalized transfers at the custody wallet
- [Submission recovery](docs/SUBMISSION_RECOVERY.md): the coordinator's durable
  submission intents and reconciliation
- [Protocol probe](docs/PROTOCOL_VALIDATION.md): capturing TIG protocol inputs
  and checking the credit arithmetic against them
- [Dashboard](docs/DASHBOARD.md): the member, join and operator screens
- [Branding](docs/BRANDING.md): name, mark, colours and wording rules

For members:

- [Member setup](docs/MEMBER_SETUP.md): connecting your own master and
  receiving a benchmark
- [Member API](docs/MEMBER_API.md): the benchmark protocol between the pool and
  a member's master
- [Member master contract](docs/MEMBER_MASTER.md): splitting one benchmark
  across your own machines
- [One bounded worker on a shared machine](docs/LOCAL_CPU_PILOT.md): the
  optional CPU pilot policy on TIG testnet

Design and money:

- [Design](docs/DESIGN.md): the rules the pool enforces, the architecture and
  the tested scenarios
- [Observation and selection](docs/OBSERVATION_AND_SELECTION.md): block
  collection, replay, credit attribution and work selection
- [Member funds](docs/MEMBER_FUNDS.md): the accounting behind deposits,
  balances and collateral
- [Withdrawals](docs/WITHDRAWALS.md): operator-reviewed withdrawals with no
  signing key in the software
- [Round settlement](docs/ROUND_SETTLEMENT.md): arbitration evidence, round
  sealing and allocation
- [Reward receipts](docs/REWARD_RECEIPTS.md): reading finalized reward
  distributions on Base

## Repository layout

| Path | Contents |
|---|---|
| `pool_manager/pool_v2/` | The pool: API and website, coordinator, collectors, ledger, settlement, controls, migrations |
| `tools/` | Operations scripts run by the systemd units: collectors, coordinator, backups, health checks, retention, conflict resolution |
| `deploy/v2-mainnet/` | systemd unit, nginx and configuration templates for a two-host deployment |
| `tests/v2/` | The test suite, which is the specification for the money and protocol behaviour |
| `docs/` | Operator, member and design documentation |

## Development

Python 3.12 and PostgreSQL 18. See [CONTRIBUTING.md](CONTRIBUTING.md) for the
test setup, the branch policy and the rules for changes that touch money or
the member protocol. Security reports follow [SECURITY.md](SECURITY.md).
Releases are fixed tags; `release/v2` points at the pool commit production
serves and `main` is the integration branch.

## Status

The pool has run on TIG mainnet with CPU benchmarks end to end: assignment,
member computation across two machines, TIG activation, credit recording and
collateral handling. GPU execution and a reward cycle with non-zero earnings
are still being validated; the [roadmap](docs/ROADMAP.md) has the details.

## License

[MIT](LICENSE).
