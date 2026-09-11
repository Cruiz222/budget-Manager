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
├── currency is Currency                       ✅
├── status is WalletStatus                     ✅
├── available_balance is Money                 ✅
├── funds is a tuple of Fund                   ✅
├── available currency == wallet currency      ✅
├── every fund's currency == wallet currency   ✅
└── every fund's name is unique in the wallet  ✅

locked_balance is NOT initialized. It is derived - sum(fund.balance) - so there
is no second number to keep in step with the pots. See decision 33.

## What Wallet Knows and Does
Can I deposit? → Wallet decides.
Can I withdraw? → Wallet decides.
Can I lock money into a pot? → Wallet decides.
Can I release money out of a pot? → Wallet decides.
May this pot be released or paid yet? → Wallet decides, and the date is the answer.
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

 # Its either available balance and a pot balance updates successfully together, or the operation fails - the two move as one or not at all.

                 WALLET
                  │
       ┌──────────┴──────────┐
       │                     │
    INVARIANTS            BEHAVIOR
       │                     │
       ├─ currency           ├─ deposit
       ├─ status             ├─ withdraw
       ├─ Money types        ├─ open_fund
       ├─ currency match     ├─ deposit_into_fund
       └─ unique fund names  ├─ lock_into_fund
                             ├─ release_from_fund
                             ├─ extend_fund
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

*Worked example, added in Phase 6.* `OutboundMessageStatus` was renamed to
`DeliveryStatus` when a second kind of message arrived and the old name stopped
describing what the enum held. **No migration was needed**, and it is worth
having a concrete case for why: `enum_to_text` stores a member's *name*, the
member names (`PENDING` / `SENT` / `EXPIRED`) did not change, and so not one byte
already on disk was affected. Renaming the *class* is free; renaming a *member*
is the migration. The two look identical in a diff.

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

### Saying what happened

The five decisions the receipts were built on. Phase 5 gave the *warning* a
destination; it did not give the *payout* one. At noon on 2 March the tick printed
`succeeded` and emailed nothing, because the warning had already gone out half an
hour earlier and a warning is claimed once per occurrence - the message a user
most wants, **"the money just moved"**, was never sent at all. This is that
message, for every plan event and every wallet event, and for the wallet commands
immediately rather than at the next tick.

The mechanism is deliberately the warning's mechanism one queue over: the same
`NotificationChannel` port, the same SMTP adapter, the same retry-and-record rule,
the same drain shape. What could *not* be reused is the record, and the pair
`OutboundMessage` / `Notification` is where this phase's thinking actually lives.

**28. A receipt is a consequence of the payment, never a condition on it.**
The receipt is enqueued inside the run's own transaction, before the commit that
makes the money real, so **no payout can commit without its receipt**. The usual
instinct about side effects says the opposite - a message must never be able to
stop a payment - and here it can, deliberately. The failure is survivable rather
than dangerous, because a blocked run does not advance the plan's counter: if
composing the message raises, the run rolls back, the next tick derives the *same*
occurrence, and it pays then. Fail-loud costs one tick and buys the stronger
claim, which is **"the money moved" implies "the user was told"** - not the weaker
"a message was composed, somewhere". For a wallet command there is no retry behind
the rollback, but the outcome is still the safe one: nothing moved, and nothing
claims it did.

**29. One message per run, not per instruction.** A payroll to three accounts is
one email, not three. The plan's instructions are listed in the body instead, in
the order they were executed - the same information, arriving as one thing that
happened rather than three that have to be reassembled by hand to answer "did the
run happen?". `for_plan_event` is the enforcement: it is keyed on the plan and the
occurrence, with no index in it.

**30. A notification has no deadline.** A warning stops being worth sending when
its occurrence arrives (decision 25); a receipt never does, because "your payout
went out" does not become false. That one difference is the whole reason the two
live in separate tables rather than in one with a type column: feeding a receipt
through `DeliverPendingMessages`' `due_at <= as_of -> expire` rule would expire it
on the very tick it was written, since a receipt is *created at* its moment. So
`DeliverNotifications` has no expiry branch at all, and `notifications` has no
`expires_at` column - a nullable column that no code writes would advertise expiry
as a live concept. The migration is cheap when the first genuinely time-limited
kind arrives, and *that* is when the rule's real shape will be knowable.

**31. A notification is claimed by an insert on a derived key.** Decision 21
again, for the same reason. The key is `<kind>:<scope>:<identity>` - the event's
own facts, derived and never generated (decision 7) - and it is the table's
primary key, so a second enqueue of the same event inserts nothing rather than
being rejected by a check somewhere. Two parts of that key are load-bearing rather
than decorative. The **kind** is first: a run blocked at noon and retried
successfully in April shares one `plan_runs` row and one `due_at`, and those
really are two things that happened to the user, so they must derive two keys -
while the *same* outcome reached twice, such as a plan blocked, resumed unfunded
and blocked again, collapses to one and is announced once. The **scope** keeps a
plan event and a wallet event apart, since both subjects are UUIDs. Getting either
wrong is not loud; it is a message that silently never sends.

**32. The notice set is boundary-crossing events.** Deposit, withdrawal, payout,
and plan run succeeded / blocked. Locking and releasing are **absent on purpose**:
they move money between the wallet's own two balances, so nothing the owner holds
changes, and they are typed at a terminal that has already printed the result.
Freeze and unfreeze are absent for that reason plus one more - they change a
status flag and no money at all. Which operations speak is `WalletService.ANNOUNCED`,
a dict rather than four branches in the flow every operation shares, so adding one
later is a line and "does a release send an email?" has one answer in one place.
It is a reversible bet, not a gate.

Named funds did not move that line, and it is worth saying so because *six*
operations now sit under `fund` where two top-level commands used to. `fund lock`
and `fund release` reshuffle the wallet's own money exactly as `lock` and
`release` did, so they stay silent; `fund open` and `fund extend` change the
wallet's shape but move nothing at all, so they are silent with even less
argument; and **`fund deposit` is the one that speaks** - it is money arriving
from outside, which is the boundary the whole notice set is drawn around. Note
that this is not "the fund commands are quiet": it is the same rule as before,
applied to a command group that happens to contain one crossing and five
non-crossings. `ANNOUNCED` therefore gained exactly one entry in this phase and
lost none.

The wallet half adds one thing the plan half did not need: **"immediately" means
the command delivers after itself.** `deposit`, `withdraw` and `payout` drain the
receipt queue before they exit, where previously only `plan tick` drained
anything. That puts a ten-second SMTP timeout in front of an interactive command,
which is acceptable *only* because the money has already committed before any
socket opens - a hang costs latency and can never cost correctness.

### Named funds

Locking used to be one number. `Wallet` held an `available_balance` and a
`locked_balance`, both stored, and "lock" was a one-shot move into that single
pool whose release was unconditional - no date, no condition, nothing.

The request that changed it was this:

> a locked fund cannot be reversed until its scheduled conditions are met, but it
> should still be able to accept additions or further deposits. Like if I lock
> 50,000 I should still be able to add money continuously, instead of having to
> create another plan for each deposit.

The answer is **named pots** - "Vacation", "Salary", "Personal savings" - each
with its own balance, its own kind, and its own maturity date:

| | Deposits | Release → available | Payout → external account |
|---|---|---|---|
| **Personal** pot | always allowed | blocked until the date | blocked until the date |
| **Business** pot | always allowed | blocked until the date | **allowed on schedule** |

The business row is the exception the whole feature was built for, and it is
narrower than "a business pot pays early". It pays **scheduled** payments before
its date - a plan committed to it beforehand - and it refuses a hand-typed
`payout --fund` on exactly the same day, because a payment someone typed just now
is not a scheduled one. Release is refused either way: an early release puts the
money back in the available balance, where it can go anywhere, and that is the
move the lock exists to prevent.

So what makes an early payment legitimate is an **ordering**, and it is one
sentence:

> **seal the pot → commit money to it → fund it**

Concretely: open the pot with its date, create the plan that pays out of it, and
only then put money in. A pot sealed on 1 January, committed to by a plan on the
2nd, and funded on the 3rd will pay an external account in March against a June
maturity date. Fund it first and then invent a plan, and the pot waits for June -
which is the manoeuvre the ordering exists to stop, because the person holding
the money could otherwise lock it, get tempted, and *then* write the plan that
unlocks it. Two dates on the pot record it: `sealed_at` (when its current date
came into force) and `first_funded_at` (when money first arrived, stamped once
and never moved). The rule is then one comparison, and it reads as the ordering:

```python
self.sealed_at <= committed_at <= self.first_funded_at
```

The anchor is **first** funding rather than most recent, and that choice is the
security rather than a detail. Anchoring on the latest deposit would let anyone
unlock a sealed pot by depositing a token amount after creating the plan. The
cost is the conservative direction: a pot that already held money must wait for
its date, and the exemption is obtained by opening an empty pot, committing to
it, and funding it after. All or nothing - there are no per-deposit lots.

Because `sealed_at` moves on `fund extend`, pushing a pot's date out **re-seals
it against a plan that has already committed**. That was a deliberate ruling
against the opposite recommendation, and it is coherent because of what the
owner gets: not a payment quietly skipped, but a blocked run that pauses the plan
and queues a `payout_blocked` receipt naming the pot. The owner runs the tick, so
the machine cannot defend a payee against the owner - what it can do is make the
owner's change of mind loud.

Continuous deposits are not a feature bolted onto a lock; they are the reason
the pots are named at all. A pot that could not be fed would mean a new plan per
top-up, which is exactly the thing being designed away.

**33. A pot is part of the `Wallet` aggregate, and `locked_balance` is derived
from it.** `locked_balance = sum(pot.balance)`. Funds are not a second aggregate
with their own transaction: a rule spans both ("no pot may exceed the locked
balance"), and one aggregate is one transaction is one truth. The alternative -
keeping `wallets.locked_balance` as a written cache beside a `funds` table -
would be two records of one fact with a "they must agree" rule enforced by hope,
and every "the pot says 5,000 but the wallet says 4,900" bug lives in exactly
that gap. A derived number cannot drift. The read path loads the pots anyway
(every release and every payout needs them), so the column would have bought
nothing. So `locked_balance` stops being a stored field and becomes a property,
and decision 18's "no column that no code writes" gets a second half: **no number
that no code writes either.**

**34. `maturity_date = None` means *no maturity* - always open.** Not "unknown",
not "infinite", not an epoch date. It is a real value with one meaning, and it is
the state every pre-existing locked balance migrates into, which is what makes
that migration invisible: money locked before pots existed stays exactly as
releasable as it was. `is_open` asks that question ("never had a date"), which is
a different question from `is_matured` ("may money leave yet") - a pot with a past
date is matured without being open, and `extend_to` is the one place that needs
to tell them apart.

**35. A maturity date is a day, and it opens the pot for the whole of that day.**
`as_of.date() >= maturity_date`. The same reduction `SavingsPlan` already makes
of a run's moment against `ends_on`, and for the same reason: a date a human
wrote is a whole day, not a midnight in some other timezone's sense of it. So a
pot maturing on 1 June refuses a release at 23:59 on 31 May and allows one at
00:00 on 1 June.

**36. Extending is forward-only.** A date can move later; never earlier. Moving a
date forward is an early release wearing a different hat, and refusing it is the
entire product promise - so `extend_to` refuses a date that is not in the future
*and* refuses one that is not after the current date. Setting a date on a pot
that has none **is** allowed: that is how an open pot is re-sealed, and it is the
only way the migrated `"Locked"` pot becomes a real commitment. Rendered on the
command line as `fund extend W N --to 2026-06-01`.

**37. A pot's name is unique within its wallet.** Validated in the aggregate *and*
enforced by `UNIQUE (wallet_id, name)`. The name is the handle a human types -
`fund release Vacation 5000` - so two "Vacation" pots would not fail as "no such
pot" but as "some pot, chosen by the wrong rule". This is decision 21's
claim-by-insert reasoning again: a check-then-write in the service leaves a gap
that a constraint closes, and the aggregate check is for the good error message
rather than for the guarantee.

**38. In Phase A a payout draws from matured pots in creation order and records
no pot.** *Superseded by 41 - kept for the record, because the reasoning is why
41 is shaped the way it is.* A payout *cannot* avoid choosing a pot once pots are the locked balance,
and "payouts untouched" is therefore not literally available - so the honest thing
is to say which rule was picked rather than to leave it implicit. "Which pot" has
no single true answer while several may fund one payment, so Phase A does not
answer it: it spends matured pots oldest-first (`created_at`, then `rowid` in the
store) and writes **no fund onto the PAYOUT ledger row**. The row still says what
it always said - value left the wallet for a named account. Phase B makes the pot
explicit ("the payout names its pot"), which is both stricter and simpler, and
which is when `transactions.fund_id` starts being written on payout rows.

**39. The commitment anchor is the pot's *first* funding, not its latest.**
`Fund.first_funded_at` is stamped by the first `deposit` and never moves. The
alternative - asking when the pot was most recently funded - is exploitable in
one line: deposit a token amount after creating the plan and the exemption is
yours. A moment that cannot move later cannot be gamed that way, so the only
lever left is the ordering itself. A lock also stamps it, since
`Wallet.lock_into_fund` calls `Fund.deposit` - the money was already in the
available balance and already spendable, so setting it aside grants nothing that
was not already granted. **The cost is that the exemption is all-or-nothing**:
there is no notion of "the money that arrived after the plan", so a pot that
already held anything must wait for its date. Ruled with the user, deliberately,
over per-deposit lots.

**40. `sealed_at` moves on `extend`, so extending re-seals against a committed
plan.** `extend_to` sets `sealed_at` to the moment of the change, which pushes it
past an already-made commitment and lapses the exemption - for free, with no
extra state and no second rule to keep in step. I argued the other way (an owner
could starve an external payee by extending forever); the ruling went the other
way, and it is coherent for a reason worth writing down: **the owner runs the
tick**, so the machine cannot defend a payee against the owner regardless. What
it can do is make the change of mind *loud* - the run blocks, the plan pauses,
and a `payout_blocked` receipt names the pot, so extending does not silently skip
a payment. It also keeps `extend` uniform: a date moving later always re-seals,
for every kind of pot.

**41. A plan that spends the locked balance must name its pot, and the pot is
fixed at creation.** `savings_plans.fund_id` is set at `plan create` via
`--from-fund NAME` (required iff `--source locked`, refused for `available`) and
there is deliberately no way to change it afterwards, because pointing an
existing commitment at a different pot is the redirect the commitment test
exists to stop - arriving through the one door that test cannot see, since the
new pot would be judged against the plan's *old* creation moment. Getting it
wrong is recoverable: `plan cancel` frees nothing, so the money stays sealed to
its own maturity date and a new plan can be made against it. `plan edit` may
therefore change a payout's destination and amount but **never remove the last
payout line** - only removal is refused. The aggregate holds the check it can
make about itself (`fund_id` only on a `LOCKED` source); the use case holds the
one a pre-existing row is allowed to violate ("a `LOCKED` plan must name a pot"),
because enforcing that in the aggregate would make every plan already on disk
unhydratable. `create_plan` takes the pot's **name** rather than its id so the
source/pot pairing is refused *before* the name is looked up.

**42. An ad-hoc `payout --fund` is never exempt, and says so by passing nothing.**
The ruling covers *scheduled* payments to external accounts. A hand-typed payout
has no plan behind it, so it passes no `committed_at`, `authorises_early_payout`
returns `False`, and a business pot must have matured before it will spend it.
Closed at the pot rather than at the command - the command has no way to know
whether the caller is tempted, and a pot asked without a commitment has a
definite answer.

**44. A pot asks about its date before it asks about the amount.** In
`Fund.pay`, `Fund.release` and everywhere else a pot is asked to give money up,
the maturity gate runs first and the amount and currency checks run after. Both
refusals are true of `pay(-500)` on a pot maturing in June, so the order decides
which one the user is told - and "locked until 2026-06-01" is the one they can
act on. A pot that may not be spent at all cannot be helped by a well-formed
amount, so telling someone to fix the number would send them off to repair a
typo that would not have freed the money. The reverse reading is defensible (a
malformed request is malformed whatever the date), which is exactly why this is
written down rather than left to the order two `if` statements happened to be
typed in: reversing them changes what a user is told, silently, and no test
would catch it unless one was written to. A plan saved before pots could be named gets no backfill: its wallet's
locked money may be spread across several pots, and picking one would silently
commit money the user never committed. So `NULL` means exactly what is true -
"this plan predates naming, draw on the pool as it always did" - and
`Wallet.payout_from_locked` still implements that draw, reaching it only from
such a plan. It is invisible in practice, because every pot that existed before
this phase is the one open `"Locked"` pot the migration created
(`maturity_date IS NULL`), so the pooled rule can only ever see a pot that is
always matured. The two new pot columns are backfilled the conservative way:
`sealed_at` from `created_at` unconditionally (a date is as old as its pot), and
`first_funded_at` from `created_at` **only where the balance is non-zero**, since
an earlier anchor makes the exemption harder to obtain and an empty pot has never
been funded at all. "Is this balance zero?" is asked in SQL with a cast, which is
the one place arithmetic on a money column is defensible - the question is
yes/no and the answer is thrown away.

**Scheduling has never required locking, and pots do not change that.**
`PlanSource.AVAILABLE` exists precisely so a plan can pay an external account out
of the available balance, and `ExecutePlanRun` already funds such a run from
there. Pots add a richer place to *put* locked money; they do not unlock the
ability to schedule. A user who wants a monthly payout to a landlord and no
commitment to anything locks nothing and creates the plan today.

Two consequences of decision 33 are worth naming, because they are the kind of
thing that only shows up later. First, **`ExecutePlanRun` had to learn which
locked balance is *spendable***: its pre-flight asked
`wallet.matured_locked_balance(as_of)` for a `LOCKED` plan, not the full sum. Had
it kept asking for the full sum, a run funded only by an immature pot would pass
the pre-flight and then have `PayoutFromLocked` raise mid-run - the half-executed
run the pre-flight exists to prevent. Phase B replaced that arithmetic with the
pot the plan names, and the reason `INSUFFICIENT_BALANCE` becomes
`RunBlockReason.FUND_NOT_MATURED` - "your pot has not matured" is now
distinguishable from "you have no money", and they have different remedies
(wait for the date, versus top the pot up). One member, not two: "the pot has not
come due" and "the commitment no longer authorises an early payment" have the
*same* remedy, and `RunBlockReason`'s own docstring warns that widening one
member to carry two meanings is the quiet danger while adding one is free. The
maturity test also runs *before* the balance test, and the order is the advice -
a pot that may not be spent at all cannot be helped by the size of its balance,
so a user is never told to top up a pot that topping up would not free.

The pre-flight and the operation that moves the money call the **same method**,
`Fund.authorises_early_payout`, and that is the only thing that makes the
pre-flight worth anything: if they could answer differently, the run would either
approve a payment the pot then refuses (half-executed) or block one the pot would
have allowed. The predicate lives on `Fund` because it is the pot's own rule
about its own money; the *fact* it needs - the plan's creation moment - is
supplied by the use case, because the pot cannot see a plan and the plan cannot
see a pot's funding history. A plan holding a `RELEASE` is gated out of the
exemption by `plan.is_irreversible`, which is exactly "this plan releases locked
funds" - grant it and the pre-flight would approve a run the release then refuses.

Second, **the old locked balance is migrated, not abandoned.** A database that
predates pots has `wallets.locked_balance` holding real money that was releasable
unconditionally. Opening it writes one pot per wallet - named `"Locked"`, kind
`PERSONAL`, `maturity_date = NULL` - and drops the column. Per decision 18 the
`funds` table itself costs nothing: `CREATE TABLE IF NOT EXISTS` builds it on an
older database without touching the tables already there, so a brand-new table is
free and only a changed one is a migration. The `transactions.fund_id`,
`savings_plans.fund_id`, `funds.sealed_at` and `funds.first_funded_at` columns are
therefore what need the PRAGMA-guarded `ALTER` (the `destination` idiom), and
dropping `locked_balance` is the one *real* migration in this phase,
because `ALTER TABLE ... DROP COLUMN` needs SQLite 3.35.0. The drop
checks `sqlite3.sqlite_version_info` **before** writing any pot, and raises a
message naming the requirement rather than half-migrating a database on an older
install. `funds.sealed_at` has to exist *before* the pot-creating INSERT above
runs, since that row carries a `NOT NULL sealed_at` - so the order of the
migration functions is load-bearing, not alphabetical. `NOT NULL` needs a
`DEFAULT`, which SQLite gives the existing rows only briefly before the backfill
replaces it; a brand-new database never sees the default at all, because the
`SCHEMA` already carries the real columns. Migrations run in autocommit before
the unit of work's `BEGIN`, so a
process killed between the inserts and the drop leaves pots written and the
column standing - which is why the inset is guarded by `WHERE NOT EXISTS` and not
by a PRAGMA check on the column: rerunning it must be a no-op, and the second run
is the one that has to cope.

### One drain for two queues

Phase 6 ended with the two delivery passes deliberately duplicated, and the
reason written down rather than left implied: the risky half of that phase was a
receipt landing inside a payout transaction, and moving the well-tested warning
path at the same time would have meant debugging two things at once. The *Still
open* entry said the merge wanted the moment when "both shapes are known rather
than predicted". They are both known now, so this is that moment.

It is worth saying why a working duplication was worth removing at all. The cost
was never the thirty duplicated lines of `_attempt` - it was that the *reasons*
were duplicated too. Two docstrings stated the same two delivery rules in
slightly different words, and the next person to change a rule would have had two
places to change it and no way to know there were two. A second copy of a rule is
where a rule starts to drift.

**45. The two drains are one pass, and the only difference is which queue they
read.** `DeliverQueue` holds the algorithm once: send what is owed, record a
failure rather than raising it, hold no transaction across the send. What were
two classes are now subclasses of it whose entire body is `_queue` - returning
`uow.outbound_messages` or `uow.notifications`. Everything else the messages
answer for themselves.

The merge is deliberately **invisible from outside the package**. Both class
names, both module paths and both builders survive unchanged, so the CLI, the
composition root and all 95 existing tests were untouched. That is the evidence
the seam was drawn in the right place, rather than being a rewrite wearing a
refactor's clothes: the diff that mattered is inside three files, and no caller
can tell it happened. What did change shape is `DeliveryReport.expired` - it was
populated by one drain and defaulted by the other, and is now structurally
present for both. For a receipt it is always empty, because nothing expires one,
and a slot that means "nothing was dropped" is more honest than a report with no
way to say it.

**46. Staleness is the message's answer, not the drain's flag.** This is what
made the merge safe rather than merely tidy, and it is decision 30 taken
seriously. That decision refused to share a pass because a receipt fed through
the warning's `due_at <= as_of` rule would be expired on the very tick it was
written - and the leak would be **silent**. The obvious way to share is a flag on
the drain ("this queue expires things"), and that flag is exactly the trap: a
switch that can be set the wrong way, failing without a word.

So the rule went on the message instead. `is_stale(as_of)` is asked of every
message the drain holds, and each aggregate answers for itself: a warning returns
`due_at <= as_of`, strictly, and a receipt returns a flat `False`. A receipt
therefore **cannot** be expired, however the drain is built or configured, for
the same reason a double enqueue cannot become a second warning - its own class
says it cannot go stale. The failure is unrepresentable rather than avoided,
which is the trade decision 8 makes with a primary key and decision 31 makes with
a derived event key.

It is a domain rule by the test decision 1 gives - it still makes sense with the
database thrown away - and it stores nothing, so decision 5 is satisfied too. It
is *not* a spanning rule, which is why decision 3 does not apply: this is one
aggregate and a clock, not two aggregates. `Notification.is_stale` takes `as_of`
and never reads it, and that is deliberate rather than sloppy - both messages must
answer the same question in the same shape, or the drain would have to know which
queue it was holding in order to ask. It is the same shape of choice as
`Notification.mark_expired`, which exists with no caller so that the lifecycle is
not half-built.

**47. The queue port is a `Protocol`, not an `ABC`.** Every other port in
`domain/repositories` is an ABC, and this one deliberately is not.
`OutboundMessageRepository` and `NotificationRepository` already have `pending()`
and `save()` in exactly the shapes a drain needs; an ABC would mean editing both
of them *and* both SQLite implementations to declare a relationship that already
holds, buying explicitness and nothing else. A `Protocol` says *look like this*
rather than *inherit from me*, so nothing in infrastructure moves at all, and the
conformance is checked by the type checker rather than at import.

The cost is real and accepted: a reader of `NotificationRepository` learns it is
a queue from the port and from the drain, not from an inheritance list. `enqueue`
is deliberately **outside** the port - a drain never queues anything, and a port
describing the whole repository would be two ports in one coat. If a third queue
ever appears and inheriting starts to carry its weight, promoting this to an ABC
is a line per store.

### The road to a product

Everything so far has been domain and application work reached through a CLI. The
system does what it was designed to do and has no way for anyone but its author to
touch it: no HTTP layer, no `User`, no authentication, no deployment. This is the
first phase whose subject is turning it into a product rather than making it
correct - and it is recorded as decisions because the *order* is one, and so is
everything being deliberately **not** built.

**48. An HTTP API comes before authentication.** Not a preference - a
dependency, and one worth checking before planning anything else.
`requirements.txt` contains exactly one line, `pytest`; there is no web framework
at all, and the whole presentation layer is 1,300 lines of argparse.
Authentication, session tokens, rate limiting and Google signup are all
properties of an API, and there is no API for them to be properties of.

There is a second gap underneath that one. `wallets.user_id` is a `NOT NULL`
column with **no table behind it** - every wallet already knows who owns it, and
nothing in the system knows what a user is, because there is no `User` aggregate
and no `users` table. So the system has an ownership field it never checks
against a caller, which is not authentication's problem but authorization's.
Identity and the API are one phase, and they come first because every other item
on this list stands on them.

**49. Sessions are opaque and server-side, not JWT.** JWT is the popular default
and it is the wrong one here, for a reason specific to moving money: **a JWT is
valid until it expires and cannot be withdrawn.** A stolen laptop or a
compromised account cannot be cut off - the only fixes are a blocklist, which
reintroduces the server-side state JWT was adopted to avoid, or waiting out the
expiry while the attacker keeps the token. For a product that pays people, "this
session is revoked, now" is a requirement rather than a nicety.

An opaque random token, whose hash is stored server-side, makes revocation a
delete, gives an audit trail of live sessions for free, and is harder to get
subtly wrong - which matters more than it sounds, because a broken JWT
implementation looks exactly like a working one right up until it is exploited.
JWT keeps its real use case, short-lived access tokens in a distributed system
and service-to-service calls; this product is one server.

**50. Redis is not a cache, and money reads are never cached.** The instinct to
put Redis in front of the database is the instinct to break this system's central
property. Decisions 23, 28 and 30 are all guarantees about *when a fact becomes
true*, and a cache in front of a balance is a machine for serving facts that are
no longer true - precisely the class of bug six phases were spent designing out.
There is also nothing to win: the reads are local SQLite, sub-millisecond, on a
single node.

Redis does earn its place later, for two things that are **not** caching:
rate-limit counters and session storage, both of which are state that wants to
expire and is not a duplicate of the ledger. It gets bought for those or not at
all.

**51. No Kafka.** The outbox already *is* the coordination mechanism. Decisions
23 and 28 write a message in the same transaction as the fact it describes, which
is what makes "we decided to warn" imply "the warning will be delivered" and "the
money moved" imply "the user was told" - and that guarantee comes from a
`COMMIT`, not from a broker. Kafka solves coordination across many services at
high throughput; this is one process on one node. Adopting it would add a broker,
topics, consumer groups and offset debugging, in exchange for a guarantee already
held. If asynchronous job processing is ever genuinely needed, the answer is a
Postgres-backed queue or Redis/RQ, not a log.

**52. Docker and a VPS, not Kubernetes.** Kubernetes solves orchestration across
many services with independent scaling needs and a team to run them. There is one
service, one deploy target and one author. `docker compose` on a VPS behind a
reverse proxy with TLS is the whole of what this needs - a deployment that can be
understood in an afternoon, rather than an operational surface that would have to
be learned before it could be used.

### Still open

- Putting the instruction `label` into `Transaction.narration`, so the ledger
  reads "salary" instead of a bare internal reference.
- Renaming `MoneyError`. It has quietly become "any domain rejection" and the
  name no longer describes what it holds - which is why
  `app/domain/notifications/exception.py` hangs off it rather than off a fresh
  root. The rename touches every domain package and decision 6, so it wants a day
  when nothing else is in flight.
- Generalising the outbox beyond plan warnings (a generated `message_id`, a
  `dedupe_key`), and a `notify status` / `notify retry` command. A second kind of
  message now exists, so the question is live rather than hypothetical - and it
  was still *not* taken in Phase 6. See the next bullet for why.
- **A long SMTP outage delivers a backlog of true-but-late receipts.** This is a
  consequence of decision 30 and is accepted, not overlooked: a receipt never
  expires, so an install that loses its mail account accumulates them and sends
  the whole pile the day email works again - a month of deposits arriving as one
  burst of twenty emails. Every one is still true, which is why nothing is
  dropped, but a digest ("12 receipts from the last week") is the shape the fix
  would take. It wants a real outage to design against.
- Renaming `OutboundMessage`, whose name is now narrower than its job - it is
  specifically the *warning* queue, sitting beside `Notification`. The pairing is
  itself the tension: two names that describe a lifetime difference rather than
  what each one carries. Worth resolving once, when nothing else is in flight.
- Any channel other than SMTP. `NotificationChannel` is the port that makes one a
  drop-in; building it now would be guessing at the second case.

## Roadmap

Where the project goes next, and in what order. The **order** is the part worth
arguing about - each phase is listed with what it makes possible, so a phase that
stops earning its place can be dropped rather than finished out of momentum.

The platform decisions above shape the sequencing, and it is worth reading them
together: an API before auth (48), opaque sessions over JWT (49), no broker (51),
no orchestrator (52), and no cache in front of money (50). Three of those are
decisions *not* to build something, which is the half of a roadmap that is
usually missing and the half that decides whether the other half ships.

### Phase 1 - Identity and the HTTP API

The foundation, and nothing else on this list works before it.

- A `User` aggregate and a `users` table, so `wallets.user_id` finally points at
  something. Email, a Google subject id, a created-at moment.
- **Authorization, not just authentication**: every wallet read and write checked
  against the calling user. Today the field exists and is never consulted, which
  is the largest actual hole in the system.
- A FastAPI application exposing what the CLI already exposes. This is a *new
  presentation layer*, not a rewrite: `WalletService` and `PlanService` are the
  seam, and the API calls the same use cases the CLI does.
- A health endpoint, because a VPS deploy without one cannot be checked.

### Phase 2 - Authentication

Everything here is a property of the API built in Phase 1.

- Opaque session tokens, hashed at rest, revocable by deletion (decision 49).
- Password hashing with argon2, if email and password signup is wanted at all.
- Google OIDC signup, matching on the Google subject id rather than the email.
- Rate limiting on the auth endpoints first - login and signup are the ones worth
  brute-forcing - then on the API generally.
- TLS. Non-negotiable, and the reason a reverse proxy sits in Phase 4.

### Phase 3 - Paystack

The phase that makes this a product rather than an exercise. It is also the phase
where the existing domain work pays off, because the hard parts are already built.

- Payment initiation, writing a `PENDING` transaction with an
  `internal_reference` - the idempotency key decision 7 derives rather than
  generates.
- A webhook endpoint with **signature verification**. This is the highest-risk
  item in the whole roadmap: without HMAC verification of Paystack's signature,
  anyone who learns the endpoint URL can POST themselves a deposit. It is the
  first thing to build here and the first thing to test.
- The callback wired to the existing `provider_reference` path, so a webhook that
  arrives twice is already a solved problem.
- A reconciliation job comparing the ledger against Paystack's records. Webhooks
  get lost and payments get reversed; drift has to be detected, not assumed away.

### Phase 4 - Production

- Migrations through a tool (Alembic), replacing the hand-rolled PRAGMA-guarded
  `ALTER`s that `sqlite_unit_of_work.py` already flags as the manual version.
- `docker compose`, a reverse proxy with TLS, and a VPS.
- **Backups with a tested restore.** A backup nobody has restored is a belief,
  not a backup, and this is the one item whose absence is unrecoverable.
- Structured logging and error tracking.
- CI running the suite on every push. 1,191 tests that nobody runs automatically
  are a liability that feels like an asset.

### Phase 5 - Scale, when a real constraint asks for it

Deliberately last, and each item waits for the thing that justifies it.

- **Postgres**, when concurrent writers make SQLite's single-writer lock bite.
  The `UnitOfWork` abstraction is what makes this contained - the swap happens in
  the composition root and the repository implementations, and the domain does not
  move at all. That abstraction is about to pay for itself.
- **Redis**, for session storage and rate-limit counters (decision 50).

Kafka and Kubernetes are not on this list and probably never will be (decisions
51 and 52). They are answers to problems this product does not have.

### What "MVP" means here

> A person signs up with Google, gets a wallet, deposits real money through
> Paystack, creates a savings plan, and receives an email receipt - over HTTPS,
> on a VPS, with backups that have been restored once.

That is Phases 1 through 4, and it is a genuine product rather than a demo. It is
also, honestly, **months** of part-time work for someone learning as they go, not
weeks. The order is arranged so that this sentence becomes true as early as it
can: Phase 3 is what a user would call the product, and Phases 1 and 2 are what
make it safe to let anyone near it.
