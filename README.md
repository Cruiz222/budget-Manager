# Budget Manager

## Vision

One of the most important habit to have is financial discipline, but with the increased hardship and unsettlement around us, it becomes almost impossible to make smart savings and financial decision relying only on our will power, Budget manager aim to bridge the gap between your sheer will power and enforcing secondary and smart financial discipline. 

## Objectives
**To enforce financial discipline**
**Help users make smart financial decisions**
**Help users make proactive financial plans**
**Help users make scheduled / automated payments**

## Architecture

For the architectural designs, we are using simple systemic and domain design decisions, to support readability, scalability and all round efficiency

Wallet initialization
│
├── currency is Currency                  ✅
├── status is WalletStatus                ✅
├── available_balance is Money            ✅
├── locked_balance is Money               ✅
├── available currency == wallet currency ✅
└── locked currency == wallet currency    ✅

## What Wallet Knows and Does
Can I deposit? → Wallet decides.
Can I withdraw? → Wallet decides.
Can I lock funds? → Wallet decides.
Can I release funds? → Wallet decides.
Is the wallet frozen? → Wallet knows.
Is the amount in the correct currency? → Wallet verifies.

## Wallet shouldn't know 
Paystack
Database
HTTP requests
Webhooks
Email
Notifications
Transaction repository
Ledger repository

User
 │
 ▼
Application Service
 │
 ├── create transaction
 │
 ├── communicate with Paystack
 │
 ├── verify payment
 │
 ├── record financial effect
 │
 └── tell Wallet to apply deposit
 │
 ▼
Wallet
 │
 └── available_balance += ₦50,000

 # Its either available balance and locked funds updates successfully or the operation fails during lock funds and release funds

                 WALLET
                  │
       ┌──────────┴──────────┐
       │                     │
    INVARIANTS            BEHAVIOR
       │                     │
       ├─ currency           ├─ deposit
       ├─ status             ├─ withdraw
       ├─ Money types        ├─ lock
       └─ currency match     ├─ release
                             ├─ freeze
                             └─ unfreeze



## TRANSACTION
    Transaction should be immutable like money   
    Money
├── Immutable
├── Represents a monetary value
└── Arithmetic creates new Money

Transaction
├── Immutable
├── Represents a financial event/record
└── Cannot be edited after creation

Wallet
├── Mutable
├── Represents current account state
└── Changes through controlled operations

              ┌──────────────┐
              │    PENDING   │
              └──────┬───────┘
                     │
            ┌────────┴────────┐
            ▼                 ▼
      SUCCESSFUL            FAILED
            │
            ▼
         REVERSED



         Transaction
│
├── transaction_id              → generated automatically
├── wallet_id                   → supplied
├── type                        → supplied
├── amount: Money               → supplied
├── status                      → PENDING automatically
├── internal_reference          → supplied by Application
├── provider_reference          → optional
├── narration                   → supplied
├── metadata                    → optional
├── created_at                  → generated automatically
└── completed_at                → None initially



| #  | Invariant                              | Test |
| -- | -------------------------------------- | ---- |
| 1  | wallet ID must be UUID                 | ✅    |
| 2  | type must be TransactionType           | ✅    |
| 3  | amount must be Money                   | ✅    |
| 4  | amount cannot be zero                  | ✅    |
| 5  | amount cannot be negative              | ✅    |
| 6  | internal reference cannot be empty     | ✅    |
| 7  | provider reference must be string/None | ✅    |
| 8  | narration must be string/None          | ✅    |
| 9  | metadata must be dict                  | ✅    |
| 10 | PENDING → completed_at must be None    | ✅    |
| 11 | SUCCESSFUL → completed_at required     | ✅    |
| 12 | FAILED → completed_at required         | ✅    |
| 13 | RVERSEED → completed_at required       | ✅    |




Your Transaction cannot be created with these invalid states:

❌ Invalid wallet ID
❌ Invalid transaction type
❌ Invalid amount object
❌ Zero amount
❌ Negative amount
❌ Empty internal reference
❌ Invalid provider reference
❌ Invalid narration
❌ Invalid metadata
❌ PENDING + completed_at
❌ SUCCESSFUL + no completed_at
❌ FAILED + no completed_at
❌ REVERSED + no completed_at


### ACHITECTURE

┌─────────────────────────────────────┐
│           PRESENTATION              │
│        HTTP / API / CLI             │
└──────────────────┬──────────────────┘
                   ↓
┌─────────────────────────────────────┐
│           APPLICATION               │
│                                     │
│  DepositMoney                       │
│  WithdrawMoney                      │
│  LockFunds                          │
│  ReleaseFunds                       │
│                                     │
│  Orchestrates the workflow          │
└──────────────────┬──────────────────┘
                   ↓
┌─────────────────────────────────────┐
│             DOMAIN                  │
│                                     │
│  Money                              │
│  Wallet                             │
│  Transaction                        │
│                                     │
│  Enforces business rules            │
└──────────────────┬──────────────────┘
                   ↓
┌─────────────────────────────────────┐
│          INFRASTRUCTURE             │
│                                     │
│  PostgreSQL                         │
│  Paystack                           │
│  Redis                              │
│  Email/SMS                          │
└─────────────────────────────────────┘


Caller
  │
  │ wallet_id + amount
  ↓
DepositMoney
  │
  ├── create Transaction(PENDING)
  │
  ├── process payment
  │
  ├── mark transaction SUCCESSFUL
  │
  └── update Wallet


  | Component      | Responsibility                    |
| -------------- | --------------------------------- |
| `Money`        | Money arithmetic & currency rules |
| `Wallet`       | Balance rules                     |
| `Transaction`  | Financial event + lifecycle       |
| `DepositMoney` | Coordinates the operation         |


## testing business rules at domain level and aplpication level, but doman level owns the rules, application level endorses it.

                    APPLICATION
                         │
              DepositMoney.execute()
                         │
          ┌──────────────┴──────────────┐
          ▼                             ▼
       Wallet                       Transaction
          │                             │
   business rules                 lifecycle rules
          │                             │
          └──────────────┬──────────────┘
                         ▼
                 TransactionRepository
                         │
                         ▼
                      DATABASE



                      TransactionRepository
│
├── save(transaction)
├── get_by_id(transaction_id)
├── get_by_provider_reference(provider_reference)
└── get_by_wallet_id(wallet_id)

DOMAIN
app/domain/repositories/
└── transaction_repository.py
        │
        └── defines WHAT a repository must do

        AND


INFRASTRUCTURE
app/infrastructure/repositories/
└── in_memory_transaction_repository.py
        │
        └── defines HOW it actually stores transactions 


        Our rule

Think of the transaction as having two categories:

Immutable facts — set once at creation:

transaction_id
wallet_id
type
amount
internal_reference
provider_reference
narration
created_at

Lifecycle state — changes only through domain methods:

status
completed_at
reversed_at       


Transaction
│
├── Facts — cannot be changed
│   ├── transaction_id
│   ├── wallet_id
│   ├── type
│   ├── amount
│   ├── internal_reference
│   ├── provider_reference
│   ├── narration
│   └── created_at
│
└── Lifecycle — controlled changes
    ├── status
    ├── completed_at
    └── reversed_at


## Running the scheduler

The tick is one command that does one pass: it warns about plans that are nearly
due, runs the ones that are, then delivers any queued warnings. Nothing keeps
running between invocations, so "automated" means pairing it with something that
calls it on a timer.

```
*/5 * * * * cd /path/to/budget-Manager && SMTP_HOST=smtp.example.com SMTP_USER=me@example.com SMTP_PASSWORD=... BUDGET_NOTIFY_TO=chinedu@example.com .venv/bin/python -m app.presentation.cli --db budget.db plan tick >> tick.log 2>&1
```

Four things about that line are load-bearing:

- **The absolute path.** cron does not run in your shell: its `PATH` is minimal
  and its working directory is your home. Interpreter and database path are both
  spelled out so the job does not depend on either.
- **Five minutes, not an hour.** The warning window is thirty minutes wide, so a
  tick that runs less often than that can step straight over the window and the
  warning silently never happens. See decision 22.
- **The environment is set inline, not in `.bashrc`.** cron does not source your
  shell's startup files, so exported variables do not reach it - see decision 27.
  Every variable is optional; see the table below.
- **A quiet tick exits 0.** "Nothing due" is the ordinary case for a job that runs
  far more often than plans come due. It is printed, not raised, so a log full of
  `nothing due as of ...` is a healthy one. Delivery failures are the same: a dead
  mail server is recorded and retried, never raised, so a broken mailbox can never
  turn a working payout into a failed cron job (decision 24).

The variables email delivery reads, and nothing else does:

| variable | default | note |
|---|---|---|
| `SMTP_HOST` | — | required; without it there is no email |
| `SMTP_PORT` | `587` | the STARTTLS submission port |
| `SMTP_USER` | — | optional; a relay on the local network needs no login |
| `SMTP_PASSWORD` | — | optional, with `SMTP_USER` |
| `BUDGET_NOTIFY_TO` | — | required; where the warnings are addressed |
| `BUDGET_NOTIFY_FROM` | `SMTP_USER` | who they are from |
| `SMTP_STARTTLS` | `1` | `0` only for a local server with no certificate |

**With none of them set the tick still works.** The warnings are printed to the
log and the payouts happen exactly as before - the warnings just have nowhere to
go, and the tick says so once per warning rather than failing. Configure email
whenever you want it and the next warning is delivered.

To see a message actually leave without a mail account, run a local server
(`.venv/bin/pip install aiosmtpd`, then
`.venv/bin/python -m aiosmtpd -n -l localhost:1025`) and tick with
`SMTP_HOST=localhost SMTP_PORT=1025 SMTP_STARTTLS=0 BUDGET_NOTIFY_FROM=me@example.com BUDGET_NOTIFY_TO=you@example.com`.
Note that Python's stdlib `smtpd` debug server was removed in 3.12, so the
familiar `python -m smtpd -c DebuggingServer` no longer exists.

## Decisions

A running log of the architectural and business decisions this project runs on,
written as they were made. Each entry says what was decided, and the reason -
because the reason is the part that is impossible to recover from the code six
months later. Where a decision forces something else, the consequence is noted
too.

New decisions are appended here rather than edited into the sections above, so
the log reads as the history of the thinking.

### Architectural

**1. Four layers, and each one has one job.**
Presentation → Application → Domain → Infrastructure. The Wallet decides. The
application coordinates. The infrastructure obeys. The test of whether a rule is
in the right place is simple: *does this rule still make sense if we throw the
database away?* If yes it is a domain rule; if no it is infrastructure.

**2. Aggregates reference each other by identity, never by object.**
A `SavingsPlan` holds a `wallet_id`, not a `Wallet`. If it held the wallet, every
plan would drag a copy of it into memory, and two copies could disagree. Passing
around a UUID makes it impossible to hold a stale one by accident.

**3. Rules that span two aggregates live in the use case.**
"Is the plan's currency the wallet's currency?" and "which balance funds this
plan?" involve a plan *and* a wallet. Neither aggregate owns the other, so
neither can enforce it. The use case is the only place both are loaded, so it is
the only place the check can honestly live.

**4. The transaction boundary follows the aggregate, not the batch.**
One plan run is one unit of work. A tick that finds ten due plans is a *loop*
over ten transactions, not one big one. If the whole tick shared a transaction,
the ninth plan raising would roll back the eight that had already paid - money
that really moved, forgotten by the ledger. Losing a record of a payment is worse
than not making it.

**5. Derived values are not stored.**
A plan stores its *anchor* and its run count; every due date is recomputed from
those. Storing "next due" instead would mean a plan anchored on the 31st drifts
to the 28th forever the first time it passes through February, because each run
would be calculated from the last one rather than from the truth.

**6. Enums for fixed concepts - and what changing them costs.**
`Currency`, `WalletStatus`, `RunStatus` and the rest are enums, not bare strings,
so a typo is an error at the boundary instead of a mystery at run time. The
consequence is easy to miss: enums are stored in the database **by name**, so
**renaming a member is a data migration**, not a refactor. Adding a member is
free, and widening a column is free only while the values already in it stay
true.

**7. Idempotency keys are derived, never generated.**
A transaction's `internal_reference` is `plan:{plan_id}:{due_at}:{index}` - the
same inputs always produce the same key. A generated key (a uuid, a timestamp)
would make a retry look like a new payment. Derived keys make "did this already
happen?" answerable by arithmetic instead of by a lookup.

**8. One run per plan per occurrence, enforced by the primary key.**
`plan_runs` is keyed on `(plan_id, due_at)`. A run has no identity of its own -
it was never anything but "this plan, this occurrence" - so the natural key is
the real key. This is what makes a retry coherent: a blocked run that later
succeeds *updates* its row rather than appending a second, contradictory one.
The consequence: changing the *text* stored in a keyed column (a date becoming a
moment) is a data migration, because two different strings do not collide.

**9. Adding a field to a model is a schema migration.**
`CREATE TABLE IF NOT EXISTS` creates a missing table and does absolutely nothing
to one that already exists. The `SCHEMA` constant describes a *new* database;
the migration functions describe the path from an old one to it. Two idempotence
techniques are in use and both are deliberate: a `PRAGMA table_info` guard when
the *shape* changes, and a `WHERE` clause when only a *value* needs widening.

**10. `isinstance` must name the subclass it means.**
`datetime` subclasses `date`, and `bool` subclasses `int`. So
`isinstance(x, date)` is *true* for a datetime, and `Duration(True, DAYS)` would
otherwise be a legitimate one-day term. Every check of this kind in the codebase
names the narrower type explicitly, and the tests assert the trap is still real
so the check cannot be quietly simplified later.

**11. Datetimes are naive and local. No timezone.**
Chosen deliberately for a single-user personal app: the moment a person types is
the moment they mean. This is a real limitation, not an oversight - a product
serving people across timezones would need timezone-aware values and somewhere
to store the zone, and moving to that later is a migration, not a flag.

### Business

**12. The scheduler does not check affordability.**
A plan may be created before it is affordable. The shortfall is discovered at run
time and recorded as a **blocked run** with its reason - so a plan that cannot be
paid stalls visibly instead of failing silently or refusing to exist.

**13. Locking is a commitment device.**
Its whole value is that it is hard to undo. So a plan containing a release is
irreversible: it cannot be cancelled, and it cannot be edited. A release plan
must also carry an end date - open-ended *and* uncancellable would mean money
locked with no way out.

**14. A frozen wallet permits a release; a closed one permits nothing.**
Freezing means "no money leaves without a human", so a release is still allowed -
it moves money between balances inside the wallet, and the total holding does not
change. Every payout is refused. A closed wallet refuses everything.

**15. A blocked run pauses the plan rather than skipping it.**
The missed occurrence stays owed. When the wallet is topped up and the plan
resumed, it pays the oldest unpaid occurrence - not the current one. Money that
was promised is not quietly forgotten because the process was checked a day late.

**16. A plan fires at a moment, but ends on a whole day - and the last day counts.**
A plan is anchored at a real time: **12:00 noon on 2 March 2026** is expressible,
and the time survives every step, including the month-end clamp. Its *end* is a
plain date: `--until 2026-07-02` means **through** 2 July, so a noon run on 2 July
happens.

The reason is asymmetrical on purpose, and it is about what a user typed. If the
end were a moment, `--until 2026-07-02` would silently mean midnight on the 2nd -
twelve hours *before* the noon run - and the last payment whose date they typed
would not happen. The rule that keeps it straight: **a moment is a `datetime`, a
deadline is a `date`**, and a moment is reduced to its day at exactly the two
places where the two meet.

**17. The 30-minute pre-payout notification is a courtesy, never a gate.**
It reports; it never delays or blocks. If no action is taken, the automated
payout still executes at its set time. If the process was down and the window was
missed, there is no warning - and the payout still runs. This is the decision
that keeps "automated" meaning automated, and it tells the notification feature
what *not* to build: anything that can hold a payout up.

### The pre-payout notice

The five decisions the warning itself was built on. They continue the numbering
and are grouped by the phase that produced them rather than filed under
Architectural and Business above, so the log still reads in order.

**18. A new table is free; a new column on an old table is a migration.**
`CREATE TABLE IF NOT EXISTS` creates a *missing* table on a database that already
exists, so `plan_notices` reaches an existing `budget.db` with no migration
function at all. That is the exact opposite of `savings_plans.name` and
`plan_runs.due_at`, which each needed one because they changed a table that was
already on disk. Decision 9 is about columns and this narrows it: adding a table
is not a migration, changing a table is.

**19. The notice record makes the *notice* idempotent, never the payout.**
The two are easy to conflate. If the notice table were ever read to decide whether
a run may proceed, the courtesy would have become the gate decision 17 forbids.
What protects against that is structural rather than a rule someone has to
remember: the notifier never loads a wallet, so it has no balance in hand and
nothing for a payment to be conditional on.

**20. The notice window is not a new rule.**
A plan is "coming up" at moment `m` exactly when
`is_due_at(m + window) and not is_due_at(m)` - the existing due-check, asked about
a later moment. So `SavingsPlan` and `Schedule` needed no changes at all. The
lower bound is **strict**: at `as_of == due_at` the plan is *due*, not upcoming,
and the tick should pay rather than warn. An already-overdue plan fails the
second half too and warns nothing - it is not coming up, it is owed.

**21. A notice is claimed by an insert, not checked and then written.**
`INSERT ... ON CONFLICT(plan_id, due_at) DO NOTHING`, and only the tick that
actually created the row reports it. A read-then-write would leave a gap between
the look and the write, and two overlapping cron runs could both look, both find
nothing and both warn. The write itself answers "was this mine to report?".
`DO NOTHING` rather than the run store's `DO UPDATE`, because a run has a truer
second version and a notice does not.

**22. The tick period must not exceed the notice window.**
A thirty-minute window checked every hour can be stepped straight over, and the
warning then never happens at all - silently, which is the worst way for a
courtesy to fail. Five minutes is the recommended interval; the constraint, not
the number, is the decision.

### Delivering the notice

The five decisions that gave the warning somewhere to go. For four phases it was
printed to stdout, which cron redirected to `tick.log` - so the feature's stated
purpose, *tell the user thirty minutes ahead*, was never actually met. A courtesy
nobody receives is not a courtesy. It is delivered by email over SMTP, from the
same `plan tick`, best effort.

**23. A message is queued in the same transaction as the notice claim.**
This is the one the whole feature rests on, and it is not tidiness. A claimed
notice is never raised again - that is the whole of `PlanNotice` (decision 21) -
so if the claim could commit while the message was never written, the warning
would be lost **permanently**. No later tick would know to say it, because the
only record that it was owed is the claim that already exists. Writing both rows
in one unit of work is what makes "we decided to warn" imply "the warning will be
delivered". The invariant, stated precisely: **a message is never orphaned from
its claim.** It is also the reason `outbound_messages` sits on the `UnitOfWork`
*for correctness*, while `notices` sits there only for convenience. The send
happens *outside* the transaction, before the unit opens, so no network call is
ever made while a database transaction is held - at the cost of at-least-once
delivery. A duplicate warning is the cheaper mistake than a lost one.

**24. Delivery failure is recorded, not raised.**
A mail server being down is a message that goes out next tick, not an incident.
A failed attempt leaves the message `PENDING` with its text on `last_error`, so
**a broken SMTP server can never make `plan tick` exit non-zero** - which matters
because the tick runs from cron, and a cron job that exits non-zero mails its own
error report. This mirrors how a blocked run is recorded rather than raised, and
it is why there is no `FAILED` status: a failure that advanced the message to a
terminal state would be a warning thrown away.

**25. A warning is never sent late.**
A message whose occurrence has already passed is **expired**, not delivered.
Telling someone a payout is thirty minutes away after it already happened is
worse than saying nothing - it is actively wrong. The comparison is strict at the
lower bound, exactly as decision 20's is: at `due_at` the payout is happening and
a warning about it is no longer news. This also removes any need for an arbitrary
retry cap: the window bounds the attempts naturally, and expiry is
channel-independent, so an install with no email still cleans up stale messages
instead of mailing a batch of them the day SMTP is finally configured.

**26. Delivery state is never consulted about a run.**
The message is a separate row from the notice partly because their lifecycles
differ - a notice is inserted and never touched again, while a message is updated
on every attempt - but mainly so that nothing deciding whether a payout may
proceed can see `last_error` and be tempted. Merge the tables and the next person
to ask "is this plan ready to run?" is reading a table full of delivery failures.
This is decision 19 drawn as a schema: what keeps the courtesy from becoming a
gate is that the run path cannot reach the courtesy's bookkeeping.

**27. Credentials and the recipient come from the environment, never the
database.** The SMTP password cannot live in `budget.db`, so the address travels
with it - one boundary, one place to look. `os.environ` is read in exactly one
module (`infrastructure/notifications/email_settings.py`), and it takes its
environment as an argument, so even that module is testable without touching the
real one. An install with no `SMTP_HOST` or no `BUDGET_NOTIFY_TO` is a **normal
state, not an error**: `from_environment()` returns `None`, the notifier has no
recipient, and the tick still warns, still records the warning and still pays -
the warnings simply have nowhere to go. When a warning is raised in that state
the CLI says so, naming the variable to set, because a user reading `warning:`
in a log every month should not have to guess that these could reach their phone.
The recipient is captured **on the row** at enqueue rather than resolved at send
time, so changing `BUDGET_NOTIFY_TO` cannot retroactively redirect messages that
were queued before the change.

### Still open

- Putting the instruction `label` into `Transaction.narration`, so the ledger
  reads "salary" instead of a bare internal reference.
- Renaming `MoneyError`. It has quietly become "any domain rejection" and the
  name no longer describes what it holds - which is why
  `app/domain/notifications/exception.py` hangs off it rather than off a fresh
  root. The rename touches every domain package and decision 6, so it wants a day
  when nothing else is in flight.
- Generalising the outbox beyond plan warnings (a generated `message_id`, a
  `dedupe_key`), and a `notify status` / `notify retry` command. Worth doing when
  a second kind of message exists, not before.
- Any channel other than SMTP. `NotificationChannel` is the port that makes one a
  drop-in; building it now would be guessing at the second case.
