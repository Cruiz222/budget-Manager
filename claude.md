# Financial System Engineering Rules

This application is a fintech wallet and financial system.

Financial correctness, security, consistency, auditability and
recoverability take priority over implementation speed and code
simplicity.

## Money

- Never use floating-point numbers for monetary amounts.
- Monetary amounts must have explicit currency.
- Never silently round monetary values.
- Define rounding rules explicitly.
- Never allow negative balances unless the account type explicitly
  permits them.
- Every financial mutation must be auditable.

## Ledger

- The ledger is the source of truth for financial state.
- Prefer double-entry accounting.
- Every posted transaction must balance.
- Ledger entries must be immutable.
- Corrections must be represented as compensating/reversal entries.
- Never modify historical ledger entries to "fix" balances.

## Idempotency

Every externally-triggered financial operation must be designed
for retries.

Examples include:

- deposits
- withdrawals
- transfers
- scheduled releases
- payment-provider webhooks
- refunds
- reversals

Repeated requests must not move money twice.

## Concurrency

Assume concurrent requests will happen.

Explicitly reason about:

- race conditions
- duplicate workers
- simultaneous withdrawals
- simultaneous releases
- balance checks
- database isolation
- row locking
- optimistic/pessimistic concurrency
- duplicate webhooks

Never assume a check followed by an update is atomic.

## Wallet Locks

A locked balance is not the same thing as an available balance.

Every operation must explicitly define whether it affects:

- total balance
- available balance
- locked/reserved balance

Do not represent these concepts only through mutable counters
without an authoritative ledger/state model.

## Scheduled Operations

Scheduled releases must have explicit state machines.

Example:

PENDING → PROCESSING → COMPLETED

with explicit failure/retry states.

A scheduler must be safe to run more than once.

## Security

Never weaken:

- authentication
- authorization
- input validation
- rate limiting
- audit logging
- secrets management
- encryption
- transaction validation

for the sake of simplicity.

Never trust client-provided balances, account ownership,
transaction status or financial state.

## Architecture

Before implementing significant financial functionality:

1. Explain the domain model.
2. Identify invariants.
3. Identify failure modes.
4. Identify concurrency risks.
5. Identify security risks.
6. Define transaction boundaries.
7. Define idempotency behavior.
8. Define recovery behavior.
9. Define tests.
10. Then implement.

Do not make architectural changes silently.

When requirements are ambiguous, identify the ambiguity before
writing financial logic.

## Testing

Financial functionality requires tests for:

- normal operation
- duplicate requests
- retries
- concurrent requests
- partial failures
- database failures
- worker crashes
- provider failures
- webhook duplication
- insufficient funds
- authorization failures
- boundary conditions
- reconciliation

Prefer invariant-based tests for financial logic.

## Code Quality

Do not optimize for minimum lines of code.

Prefer:

- explicit domain models
- boring code
- strong types
- database constraints
- explicit state transitions
- clear transaction boundaries
- deterministic behavior
- testability

Complexity is acceptable when it provides a measurable
correctness, security, reliability or auditability benefit.
