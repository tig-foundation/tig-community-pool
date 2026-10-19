# Security policy

Community Pool holds member funds in custody and submits paid work to The
Innovation Game on members' behalf, so security reports matter to us.

## Reporting a vulnerability

Please report vulnerabilities privately rather than in a public issue. Use
GitHub's private vulnerability reporting for this repository:
[report a vulnerability](https://github.com/tig-foundation/tig-community-pool/security/advisories/new).
Reports about the member worker go to the
[worker repository](https://github.com/tig-foundation/tig-community-pool-worker/security/advisories/new)
the same way.

Include what you found, how to reproduce it, and what you believe the impact
is. We will acknowledge the report, keep you informed while we assess and fix
it, and credit you when the fix is published unless you prefer otherwise.

## Scope

- The pool API and website (`pool_manager/pool_v2`), including authentication,
  member funds, withdrawals, work assignment and settlement.
- The operations tools under `tools/` and the deployment templates under
  `deploy/`.
- The [member worker](https://github.com/tig-foundation/tig-community-pool-worker), which
  lives in its own repository.

Findings about a particular deployment's hosting, DNS or hardware belong to
that deployment's operator, not to this project.

## What we ask

Do not test against a live pool with real member funds without the operator's
written permission, and do not access data that is not yours.
