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


## Signing in

Every command except `signup`, `login`, `logout` and `plan tick` acts as somebody,
and that somebody is proved by a token rather than named by a flag. So a fresh
install starts here:

```
.venv/bin/python -m app.presentation.cli --db budget.db signup me@example.com
.venv/bin/python -m app.presentation.cli --db budget.db login  me@example.com
.venv/bin/python -m app.presentation.cli --db budget.db whoami
```

Three things about that are worth knowing:

- **`signup` does not sign you in.** It creates the identity; `login` is what
  proves you hold it, and it is the *only* command that writes the session file.
  Keeping them apart is the whole point of the phase (decision 85) - a registration
  that silently authenticated would mean the first token on a machine came from a
  command that never checked the password.
- **The password is typed, never passed.** There is no `--password` flag, because an
  argument lands in the shell's history file, in the process table while the command
  runs, and in whatever the terminal is recording. `signup` asks twice; `login`
  asks once (decision 83). `getpass` reads the controlling terminal in preference to
  stdin and falls back to it only when there is none, so the prompt cannot be fed
  from a pipe either - a script that tries gets `echo … |` sitting unread while the
  prompt waits at the terminal.
- **The token lives at `--session`**, defaulting to `$BUDGET_SESSION` and then
  `~/.config/budget/session`, created `0600`. `logout` ends the session on the
  server first and deletes the file second, so a failure in between leaves a token
  that still works rather than one that works with no copy of it anywhere
  (decision 89).

`logout` on a machine that was never signed in is not an error - it exits 0 and
prints a note. The postcondition is "there is no usable session at this path", and
it is already true.

Three commands are worth knowing by name when something goes wrong. `whoami` says
who the current token belongs to, which is the first question anybody debugging an
authentication problem asks and which needs no database access to answer.
`BUDGET_SESSION` overrides where the token is read from, so a second identity on one
machine is a second file. And a session file that is corrupt or stale - a token from
another installation, a database restored without its sessions - is refused as
`InvalidSessionError` with exit 1, and signing in again overwrites it, because
`login` is dispatched before anything reads the file.

## Running the scheduler

The tick is one command that does one pass: it warns about plans that are nearly
due, runs the ones that are, then delivers any queued warnings. Nothing keeps
running between invocations, so "automated" means pairing it with something that
calls it on a timer.

```
*/5 * * * * cd /path/to/budget-Manager && SMTP_HOST=smtp.example.com SMTP_USER=me@example.com SMTP_PASSWORD=... BUDGET_NOTIFY_TO=chinedu@example.com .venv/bin/python -m app.presentation.cli --db budget.db plan tick >> tick.log 2>&1
```

Five things about that line are load-bearing:

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
- **No session, and it would not use one if there were.** The tick is not a user: it
  runs every user's plans by acting as each plan's owner in turn (decision 56),
  which is why `build_scheduler` takes no actor at all. Since Phase 2a the CLI's
  `main` dispatches `plan tick` *above* the line that resolves an actor (decision
  91), so a cron entry runs on a machine where nobody has ever logged in and no
  session file exists. What this bullet used to say — that the first tick on a fresh
  database wrote a `dev@localhost` row on its way past — is no longer true: there is
  no default identity left for anything to create.

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
dependency, and one worth checking before planning anything else. At the time
this was written `requirements.txt` contained exactly one line, `pytest`; there
was no web framework at all, and the whole presentation layer was 1,300 lines of
argparse. (That single line was itself a mistake, since corrected: `pytest` is a
*test* dependency, and the file now holds what the system needs to **run** -
`fastapi` and `uvicorn` - with `requirements-dev.txt` adding `pytest` and `httpx`
for a checkout. A production install should not carry the suite.)
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

**53. A user is an identity, not a credential.** `User` holds a `user_id`, an
`email`, an optional Google subject id and a `created_at`. There is no password,
no hash, no salt and no token, and the absence is the design rather than a stage
of work. How a person proves they are this user is a question about transport and
storage, and it changes with the transport - a password today, a Google subject
tomorrow, a passkey the year after. The identity that survives all three is the
one in that list, and it is the only part the domain has an opinion about.
Credentials arrive with the adapter that can verify them, which is why argon2 is
in Phase 2 and not here.

Two smaller rules live inside the aggregate because nowhere else can enforce them.
**The email is folded to lowercase and trimmed on construction**, because
`Chinedu@Example.com` and `chinedu@example.com` are the same address and therefore
the same account - which means a `UNIQUE` column only means what it looks like it
means if one address has one spelling by the time it reaches the database. The
fold is one module function shared by the aggregate and `find_by_email`, because
that lookup runs *before* a `User` exists (deciding whether one exists is the
question it is asking) and so cannot obtain the fold by constructing one; two
implementations would be two chances to disagree, and the disagreement would
surface as a person unable to log in to an account that plainly exists. And
**`google_subject` refuses `''` while accepting `None`**: the column is `UNIQUE`,
so every account that arrived without a Google identity would collide on the same
empty string and the second signup would fail against the first. `None` is the
honest value for "no Google identity", and SQLite permits any number of NULLs -
so the two cases stay distinct.

What is deliberately *not* on the aggregate is an `owns(wallet)` method.
Ownership is answered by the store - a scoped read either returns the wallet or
reports it absent - and a Python check beside that would be a second place for the
same rule to live, free to disagree with the query that actually decides.

**54. Ownership is enforced by the read, and there is no unscoped wallet read
anywhere in the codebase.** `get_by_id` was **removed** from `WalletRepository`
and `SavingsPlanRepository`, not deprecated, and replaced by
`get_owned(id, user_id)`. That removal *is* the guarantee: a method with an
owner-shaped hole in its signature is a method somebody eventually calls, and with
no such method left to reach for, "read someone else's wallet" is not a mistake
that can be made - only one that would have to be written, reviewed and committed.
`grep -rn "get_by_id" app/domain/repositories/wallet_repository.py` returns only
the docstring explaining why it is gone.

The alternative, and the one most systems take, is to leave `get_by_id` and put a
policy layer above it - a check in the service, a decorator, a middleware. That
concentrates the rule in one readable place, which is its real appeal, but it
leaves the unscoped read in the signature for anyone to call and it makes the
question "is this read authorised?" something a reviewer has to reconstruct rather
than something the type system answers. Here the port answers it: every call site
of `get_owned` names an owner, so `grep` for it produces a complete audit of where
money is read and on whose behalf. That audit is the point, and it is only
complete because the unscoped alternative does not exist.

The actor is bound at **service construction**, not passed per call
(`WalletService(factory, actor=user_id)`), and it is a required keyword-only
argument rather than defaulted. Required because there is no unscoped read left to
fall back to - a `None` actor would fail closed, so the default would be safe, but
making it required means every construction site *states* who it acts for instead
of inheriting silence. Bound once rather than passed per method because there are
248 existing service-method call sites and a per-call actor would have changed all
of them to express something that never varies within a request. The one signature
that did change is `open_wallet(currency)`, which no longer takes a `user_id`: a
service must not be able to open a wallet for somebody else, and the owner now
comes from the actor.

**55. A wallet you do not own is not found.** `get_owned` raises the same
`WalletNotFoundError`, with the same message, for a wallet that belongs to someone
else and for one that does not exist. There is no branch between the two cases,
so there is nothing to leak.

This is a deliberate choice against the more "helpful" alternative of a distinct
`NotAuthorisedError`. Telling a stranger "that wallet exists, it just isn't yours"
answers a question they have no standing to ask, and turns a guessable id into a
way to enumerate who banks here - a probe that costs one request and returns a
boolean. Collapsing the two failures means a probe returns the same answer whether
the id is real or not, and the information simply is not available. The cost is
that a legitimate user with a typo gets a slightly less specific message, which is
the trade being made on purpose.

What this does *not* mean is that absence is unremarkable. A caller reaching this
path in the ordinary case has a real wallet id in hand, so absence is still
unexpected - which is why it raises rather than returning `None`. Same split as
before the phase, now with a second way to arrive at it.

**56. A plan carries its owner, so the scheduler is not a privileged actor.**
`savings_plans.user_id` duplicates `wallets.user_id`, which looks redundant and is
not. The tick serves every user in the installation, so it is the one caller that
cannot be built for a single actor - and the tempting answer, the one most systems
take, is to give it an authority of its own: a system user, a skipped check, a
flag saying "this read is internal". Every one of those is a bypass, and a bypass
is a thing that exists at runtime whether or not anyone currently calls it.

So the design gives the tick no authority at all. `RunDuePlans` holds a **builder**
rather than a built executor - `build_execute_plan_run: Callable[[UUID],
ExecutePlanRun]` - and mints one per plan, acting as that plan's `user_id`:

```python
for plan in self._due_plans(as_of):
    executor = self._build_execute_plan_run(plan.user_id)
    run = executor.execute(plan.plan_id, as_of)
```

Every read inside that execution is scoped like any other - `plan =
uow.plans.get_owned(plan_id, self._actor)`, then `wallet =
uow.wallets.get_owned(plan.wallet_id, self._actor)` - so the scheduler never names
a wallet without naming whose it is. It is not an exception to decision 54, it is a
loop over single-user executions. And the composition root's `build_scheduler`
takes **no actor parameter**, which is the strongest statement available in that
file: there is no actor to pass because the tick does not have one.

`ExecutePlanRun` is the class that makes this true, which is why it takes an actor
even though it is never called by a user directly. It also re-raises any exception
after `uow.rollback()` rather than recording it as BLOCKED, so a mis-scoped read
propagates loudly instead of being written down as a plan that could not be paid.

The duplicate column is checked by the read rather than trusted: an executor built
for a plan's owner reads the wallet scoped to *that* owner, so a plan whose
`user_id` disagrees with its wallet's finds no wallet and raises. A duplicate can
always disagree with its original, and the safe half is that no money moves and no
run is recorded.

**57. `list_by_status` is cross-owner *discovery*, not a bypass - and it has two
callers, both installation-wide background jobs.** It is the one read in the
codebase that crosses owners, and it earns that by three properties taken
together: it returns plans **carrying their `user_id`** rather than wallets, every
subsequent wallet read is scoped (decision 56), and **no user-facing code path can
reach it**. `PlanService` - the service the API and CLI call - does not expose it.
The two callers are `RunDuePlans` and `NotifyUpcomingRuns`, and both are jobs that
must see every user's plans because that is what they are *for*: a tick that
served one user would not be a scheduler, and a notifier that warned one user would
not be a notifier.

`NotifyUpcomingRuns` needs no actor and no ownership change, and it is worth
stating why rather than leaving it as an omission: **it never loads a wallet.** It
reads plans, writes notices and queues messages, and touches no money at all -
which is also why `build_notifier` is the one builder in the composition root
taking no actor. Discovery is a read of *intentions*; the money is only reachable
one scoped hop later.

The boundary worth watching is that the exception is bounded by its callers, not
by its signature. A third caller would be a genuine hole if it were reachable from
a request - so the test is not "who calls this" but "can a user reach it", and
today the answer is no.

**58. A `NOT NULL` column added by migration carries `DEFAULT ''`, and nothing
reaches that default.** `ALTER TABLE` cannot add a `NOT NULL` column without one,
so a migrated database ends up with `DEFAULT ''` on `savings_plans.user_id` where
a fresh one has none - a real asymmetry, and the same corner
`_migrate_add_plan_name_column` was pushed into before. On such a database an
`INSERT` that forgot the owner would write an empty string and produce a plan that
**no actor can ever be scoped to find**: a row present in the table and invisible
to every query the application can make, which looks exactly like a plan that was
deleted.

It is unreachable through the application for two reasons, and both are tested
rather than asserted. `SavingsPlan` refuses a plan with no owner at construction,
and the repository always writes the column - so the only way to hit the default is
to write SQL by hand. The tests are
`test_a_plan_written_after_the_migration_still_carries_an_owner`, which runs
against a deliberately *migrated* database because that is the only shape where
the claim means anything, and
`test_the_owner_backfill_leaves_no_plan_orphaned`, which asserts no row has an
empty owner and re-opens the database twice so the backfill is pinned as
re-runnable.

**The backfill is derived, not invented.** Every existing plan already has an
owner - it is on the wallet the plan draws on - so the migration copies a recorded
fact rather than guessing one:

```sql
ALTER TABLE savings_plans ADD COLUMN user_id TEXT NOT NULL DEFAULT ''
UPDATE savings_plans SET user_id = (
    SELECT wallets.user_id FROM wallets
     WHERE wallets.wallet_id = savings_plans.wallet_id)
```

That subquery cannot come back empty: `savings_plans.wallet_id` references
`wallets(wallet_id)` and `PRAGMA foreign_keys = ON` is set before it runs, so no
plan can name a wallet that does not exist. **That totality is what makes `NOT
NULL` safe here.** On a database where the reference could dangle, this `UPDATE`
would have to tolerate NULLs and the column would have to accept them - and the
honest design would then be a nullable column plus a scheduler that refused to run
an ownerless plan, rather than a constraint that quietly failed. Contrast
`_migrate_add_plan_fund_id_column`, which deliberately backfills nothing: there
was no recorded fact to copy, only a choice somebody would have had to invent.

The `users` table needs no migration at all: `CREATE TABLE IF NOT EXISTS` in
`SCHEMA` already creates a missing table on an existing database, which is the
comment the migration sequence already carries two lines up.

**59. `wallets.user_id` has no foreign key to `users`, and that is a SQLite
limitation rather than a preference.** The column predates the table, and SQLite
cannot add a foreign key to an existing column without rebuilding the table -
copying every row, dropping the original and renaming, with `PRAGMA
foreign_keys` off for the duration. For a money table that is a genuinely risky
operation to run on a database somebody's savings live in, and it buys an
integrity guarantee the repository already provides: a wallet is only ever written
with a `user_id` that came from a real `User`, and the new
`savings_plans.user_id` follows the same precedent. `transactions.fund_id` makes
the identical trade, so this is a pattern rather than a compromise invented here.
A table rebuild is a candidate for a later phase, when there is a backup story
worth trusting, not for the phase that first makes ownership matter.

**60. Every identity rejection derives from `MoneyError`.** `IdentityError` hangs
off `MoneyError` rather than off a fresh root, continuing the rule
`app.domain.planning.exception` states and `app.domain.notifications.exception`
repeats: **a new exception belongs under the existing root, or every catch site in
the codebase has to be revisited.** The CLI catches `MoneyError` once, at the top
of `main`, and turns it into `error: ...` with exit code 1.

Identity is where that rule earns its keep rather than merely inheriting it. A user
is rejected at the *boundary* - a malformed address at sign-up, a token that no
longer resolves - which is exactly the point at which a human is watching and a
traceback is least acceptable. A separate root would have made the most
user-facing refusals in the codebase the only ones that escaped the handler. The
package's own test walks its exceptions and asserts every one is a `MoneyError`, so
a class added later without the right parent fails there rather than as a traceback
in someone's terminal. The name `MoneyError` describes this least of all - there is
no money in a malformed email address - which is why renaming it stays on the open
list below.

Phase 2a added the CLI's one exception to this, and it is the *presentation* that
made it: `NotSignedInError` derives from `CliError` and not from `MoneyError`,
because "there is no session file at this path" is not a fact about money or
identity (decision 92).

**61. Transactions keep an unscoped `get_by_id`, and it is not reachable from a
service.** `TransactionRepository.get_by_id(transaction_id)` survived the phase
untouched, which looks like an oversight next to decision 54 and is worth
justifying explicitly. A transaction has no owner column: it belongs to a wallet,
and the wallet is what ownership is checked against. The application reaches
transactions through exactly one door - `transactions_for_wallet(wallet_id)` -
and that method calls `self._wallet(uow, wallet_id)` **before** reading the ledger,
so the ownership proof happens first and an unknown or foreign wallet raises
`WalletNotFoundError` before a single transaction is loaded. The unscoped read is
never reached with an id that has not already been authorised.

That is a weaker guarantee than decision 54 and it should be described as what it
is: a property of the call graph rather than of the signature. Nothing in
`TransactionRepository` prevents a future method from calling `get_by_id`
directly, and that would be a real hole. The mitigations are that no such method
exists, that the port has exactly one application-level caller, and that the
repository tests calling `get_by_id` directly are tests. If a second caller
appears, the right fix is the one decision 54 already chose - scope the read, or
remove it - rather than a policy check above it.

### The API, before there is a session

Phase 1a made ownership a fact. Every wallet read names its owner, a foreign
wallet is indistinguishable from a missing one, and the actor is bound when a
service is constructed rather than handed to each call. None of it was reachable
except through 1,300 lines of argparse.

Phase 1b exposes the same use cases over HTTP. It is a **presentation layer, not
a redesign**, which is decision 48's ordering paying off rather than a claim about
it: there is no new domain rule in this phase, and no use case was changed to suit
a transport. The decisions that were worth taking are not about FastAPI. They are
about what an API may expose when nobody has proved who they are, and where the
answer to *who is asking?* is allowed to live.

**62. The boundary is at balance changes, not at writes.** This is the decision
the whole phase rests on, and it is the only thing that makes an API before
authentication (48) something other than a hazard. With no sessions, an actor is
an email in a request header: anyone who can reach the port can claim to be
anyone. Under that, a *read* leaks the claimed user's own data and nothing else -
a stranger naming somebody's address sees that person's wallets, which they could
equally have seen by naming their own. A *write* is different in kind: it moves a
person's money under a name they never proved, and no amount of care in the
handler changes that.

So the line is drawn at **balance changes**, and drawing it there rather than at
"writes" is the part worth getting right. `POST /wallets` is a write and is
exposed, because opening a wallet moves nothing. `POST /plans` is a write and is
exposed, because creating a plan moves nothing either - a plan is a description of
future intent, and the run that acts on it is the scheduler's, which has no actor
and is not reachable from here (decision 56). Held until Phase 2 is every
operation that changes a balance: `deposit`, `withdraw`, `payout_from_locked`,
`payout_from_available`, `deposit_into_fund`, `lock_into_fund`,
`release_from_fund`, `extend_fund`, `freeze_wallet`, `unfreeze_wallet` and the
close transition. Also held, for a different reason with the same consequence:
`plan tick` and `notify deliver`, which are installation-wide jobs. An endpoint
that ran the tick would have to be told who it acts as, and the tick does not have
an actor by design - making one reachable over HTTP would undo decision 56.

**The consequence, stated rather than discovered:** no money can enter the system
through this API. A pot can be opened and named, but nothing can fill it; a wallet
can be opened, but it stays at zero; and a plan can be created against a balance
that will never be there, so its first run blocks for insufficient funds. That
last part is not a break, and it is worth saying why because the first draft of
this phase said the opposite. `PlanService.create_plan` deliberately does not
check whether a pot holds enough or whether it has matured - a savings plan's
whole purpose is to *become* affordable, and checking at creation would mean a
plan could not exist before it was affordable. So a locked-source plan **is**
creatable here, against an empty pot, and that is the existing rule working rather
than an exception to it. Clients in this phase can build a *shape* and not a
balance, and the alternative - exposing a deposit so the shape looks complete -
would hand over the one operation the phase exists to withhold.

**63. The actor is `X-User-Email`, and it is a shim with a named deletion date.**
It resolves or creates a user by address, exactly as the CLI's `--user` does, and
it proves nothing whatsoever. It is safe only because of decision 62: with no
endpoint that moves a balance, the worst a forged header achieves is reading the
data of the account it names - data the forger could have had by asking for their
own.

> **Gone, in Phase 2a.** The header and `--user` were deleted together, in one
> commit, as this entry promised. The actor is now the account an
> `Authorization: Bearer <token>` was issued to, resolved by
> `ResolveActorFromSession` (decisions 80 and 89). The reasoning below is kept
> because it is the argument for *why the shim was tolerable for a phase*, which is
> the part worth remembering - not because any of it still describes the API.

**A missing header is a 400, not a 401**, and the choice is about honesty rather
than semantics. A 401 says "authenticate and come back", which implies there is an
authentication scheme to satisfy; there is not one yet, and pointing a client at a
non-existent challenge would be a lie in a status code. A 400 says "this request
is missing something it must carry", which is exactly true. The status changes to
401 in Phase 2, along with the header that replaces this one - and the two
deletions are the same commit, because they are one decision.

> **And it did.** `MissingActorHeaderError` is now `MissingCredentialsError` with
> `status_code = 401`, which is what its own docstring had promised; the invalid
> token cases joined it in `UNAUTHORIZED` (decision 87).

Nothing about the header's *contents* is this layer's business. An address with no
`@` is refused by `User.__post_init__` and arrives at the client through the same
handler as every other domain rejection, so there is exactly one rule about what
an address is and it belongs to the aggregate. A presentation that validated,
lowercased or trimmed the header itself would be a second implementation, free to
disagree with the fold decision 53 exists to guarantee.

> **The principle outlived the header.** The token is passed through untouched for
> the same reason: `current_actor` does not decide what a token means, it hands it
> to `ResolveActorFromSession`, and the rule about which hash and what counts as
> expired lives in the domain behind that (decision 80).

**64. Resolving an actor is a use case, because the API needs the identical
thing.** `ResolveUserByEmail` moved out of `app/presentation/cli.py` into
`app/application/identity/`, and the move is the decision rather than a tidy-up.
The function's own docstring claimed it was *"the one place in the codebase that
acts before it is told who is acting"* and that this was safe because there was
exactly one of it. A second presentation needing the same behaviour is precisely
the moment that claim stops being true - and the tempting answer, a copy in
`dependencies.py`, would have made the docstring false while leaving it on screen
for the next reader.

It is a use case and not a domain rule: finding an account by address is a
question about the store, and creating one when it is missing is a decision about
onboarding rather than about identity. What stays in the domain is what a user
*is* - the fold, the optional Google subject, the refusal of a blank address.

> **The location survived; the class did not.** Phase 2a deleted
> `ResolveUserByEmail` and put `ResolveActorFromSession` in its place, in the same
> module (decision 80). The argument above is *why* there is one place at all - and
> it is the argument that decided where the new class went, since two presentations
> resolve a token now rather than one.

**65. One service per request, built for the resolved actor.** A FastAPI
dependency constructs `WalletService` and `PlanService` per request with
`actor=user_id` taken from the header. This is the payoff of binding the actor at
construction rather than passing it per method (decision 54), and it is worth
being explicit about what the alternative would have been: one long-lived service
with a settable actor, mutated at the start of each request. That object is shared
mutable state on a threadpool, and a request that failed to set it would act as
whoever set it last - the bypass Phase 1a removed, reintroduced by a server that
happens to be long-running. A service is a factory and a UUID, so building one per
request costs a function call, and the actor cannot leak because it is never
stored anywhere shared.

**66. A foreign wallet is a 404, and its body is the one a typo gets.** Decision
55 collapsed the foreign case into the missing one inside the domain. A
presentation layer is exactly where that gets undone, because a handler's whole
job is turning one thing into another and "that wallet belongs to someone else"
is a helpful-sounding sentence that would restore the enumeration oracle in one
line. So the handler has nothing to distinguish: it prints the exception's class
name and message, both of which are identical for the two cases because the
domain never made them different.

The test is stronger than a 404 assertion, and it has to be. `test_isolation.py`
asks for a stranger's wallet and for a UUID that does not exist, and asserts the
two responses are *identical* - status and body. A 404 with a different body is a
404 that tells an attacker the wallet is real.

**67. Refusals are graded by what kind of question was refused.** The CLI collapses
the whole exception tree into one `error: ...` line, which is right for a terminal
with one person watching. HTTP has to be more precise, because the status code is
what a client acts on:

    404  the resource is not there *for this actor*
    409  it is there, and its current state refuses this
    400  a value in the request is not acceptable
    422  the request did not have the shape the endpoint declares
    500  a bug

Three of those five are defaults rather than lists. `NOT_FOUND` and `CONFLICT` are
the exceptions worth naming, and **everything else under `MoneyError` is a 400** -
which is the safe direction to be wrong in, because a refusal this module has
never heard of is far likelier to be about a value than about a resource's state.
Nothing new has to be registered for a new domain exception to be graded sensibly;
only a genuinely state-shaped refusal has to be added to `CONFLICT`, and forgetting
to is a milder failure than the alternative.

Keeping domain rejections at 400 and schema validation at 422 is deliberate: they
are different layers, and a client that gets one should not have to guess which.
Which layer owns a refusal is therefore a real question with a visible answer -
`CreatePlanIn.instructions` has no `min_length`, so an empty plan comes back as a
400 in the domain's own words rather than a 422 in pydantic's, because
`SavingsPlan` already refuses a plan with no lines and a second rule at the edge
would give one question two answers.

The 500 says nothing at all about what went wrong. Every other handler echoes the
domain's sentence, which is written for a user and names a pot or an amount; an
unexpected exception is written for whoever is debugging it and can carry a file
path, a SQL fragment, or a value out of somebody else's row. So the response is a
constant and the exception goes to the log.

**68. Money is a string on the wire, in both directions.**
`{"amount": "2500.00", "currency": "NGN"}`, never a JSON number. `Money` refuses a
float at its centre because binary floats are not exact - and a request that
accepted `0.1` would hand the domain a value that had already lost the argument
before any check could see it. Reading the string with `Decimal` is exact, and it
is the same direction the CLI already reads bare amounts in, so neither
presentation has a rounding story of its own.

The format spec is `f"{money.amount:.2f}"`, which is what `Money.__str__` uses - so
a person reading `8000.00 NGN` at a terminal and a client reading `"8000.00"` are
looking at the same number written the same way. The `:.2f` is doing real work
rather than tidying: `Decimal` keeps the exponent it was built with, so `"8000"`
and `"8000.00"` are the same money that format differently, and a wire format that
varied with how a value happened to be constructed is one no client could rely on.
It is formatted in exactly one function, which is what stops one endpoint sending
`"8000"` and another `"8000.0"`.

**69. Enums travel as their `.value`, which is the spelling the CLI already
accepts.** `"NGN"`, `"locked"`, `"monthly"`, `"months"` - the same strings
argparse's `choices` lists, so a value read out of a response can be pasted
straight into a command, and the two presentations cannot drift into two
vocabularies for the same concept.

There is a trap underneath this one and it is worth writing down, because it is
invisible until it fires. `PlanSource("banana")` raises a plain `ValueError`,
which is *not* a `MoneyError`, so it would fall through every handler in `errors`
and come back as a 500 - telling a client the server is broken when they sent a
typo. The CLI never has this problem because argparse checks `choices` before any
of this code runs. HTTP has no argparse, so the check is written once, in
`translate._member`, which turns an unknown member into the error the domain
already has for that concept. `money_in` is the same trap in the one other place a
string becomes a domain object, and catches `InvalidOperation` for the same
reason.

**70. The endpoints are synchronous, and the handlers are not.** Every endpoint is
a plain `def`, so FastAPI runs it in its threadpool; every exception handler is
`async def`. This is the correct execution model for this codebase rather than a
style, and the two halves have the same justification: SQLite and every service
are blocking, so a `def` endpoint gets a worker thread and a truthfully
non-blocking `async def` would be decoration - while a handler does no I/O at all
(formatting a string), so there is nothing to hand off and keeping it on the event
loop costs nothing. An `async def` endpoint that then called blocking SQLite would
be the one arrangement that is actually wrong: it would block the loop for every
other request while looking like it never does.

**71. `email_settings` generalised into `settings`, rather than a second module
reading `os.environ`.** That module's docstring states **"This is the only module
in the codebase that reads `os.environ`"**, and `cli.py` cites the promise back.
The API needs a database path as well as the SMTP settings, and reading `BUDGET_DB`
inside `app/presentation/api/app.py` would have made that sentence false in a way
nothing would catch - the promise exists so there is *"exactly one place to look
when a message does not arrive"*, and an environment read somewhere else is
exactly what erodes it.

So the module moved to `app/infrastructure/settings.py`, `EmailSettings` and
`from_environment` unchanged, and gained `database_path(environ=None)` using the
same injectable-`environ` signature the rest of the module already had - so it is
testable without patching, like everything else in there. The invariant survives
because the *reader* is still singular; only its name widened from
"notifications" to "settings", which is what it had already become.

The same constant, `DEFAULT_DATABASE_PATH`, is now the default for both the CLI's
`--db` and the API's `create_app`, so the two presentations resolve to one file
rather than to two identical string literals that could drift. `BUDGET_DB`
overrides it, and the test suite clears that variable along with the mail settings
- because a developer with it exported would otherwise have the API tests writing
into a real database.

### Sessions, and the death of both shims

Phase 1b drew its boundary at *balance changes* and held every money-moving
operation, because it could: an actor was an `X-User-Email` header, which says who
is asking and proves nothing. Phase 2a is not a detour before the interesting work
— it is **the lock those thirteen operations are held behind**. Nothing else in the
roadmap can be released until a session proves who is asking: not the money
endpoints, and not Paystack in Phase 3, where a webhook credits a balance and has
to know whose.

2a is the half with the hard property to prove — *an actor that can only be reached
by proving you are it*. 2b spends it on the money endpoints. 2c is Google OIDC and
rate limiting.

The phase is also where the two shims die, together and in one commit, which is what
the "Still open" list promised and why it insisted they were one change rather than
two.

**72. Email and password, with argon2id.** No third-party account needed, works
offline, and there is a hash function designed for exactly this input. The
alternative considered and rejected was starting with Google OIDC (deferred to 2c):
it would have made the *first* identity in the system depend on a network round
trip and a client secret, which is a lot of surface to get right before there is a
single test that a user can be authenticated at all. Password reset is a later
feature and not a blocker on the design — an account that cannot reset its password
is inconvenient; an account that cannot exist is a non-starter.

**73. The credential does not live on `User`, and the first reason is not the one
people expect.** `app/domain/identity/user.py` already said *"This is an identity,
not a credential"* as a design position rather than a stage of work, so the hash
goes in its own table with its own aggregate. The second reason is sharper, and it
is the one that decided it: `translate.user_out` **already** has to *deliberately*
omit `google_subject` and explain why. A hash on the aggregate would need the same
deliberate omission, and forgetting it returns a password hash to a client. A `User`
that holds no hash cannot leak one — `user_out` stays correct as written, and no
future edit can break it. See also decision 82.

**74. argon2 for the password, SHA-256 for the session token, and the difference is
the point.** A password is low-entropy and human, so an attacker who steals the
table can guess billions of candidates offline — that is what a memory-hard hash is
for, and the cost is paid once per login. A session token is 256 bits of CSPRNG
output (`secrets.token_urlsafe(32)`): there is no dictionary to run, so
memory-hardness buys nothing, while it would be paid on *every authenticated
request*. Using the expensive function where the input is already unguessable is a
common and costly misreading of the advice.

Worth being precise about what the session hash buys, because it is less than the
password hash buys: **the stored value is not a usable credential.** The server
hashes whatever token arrives and looks *that* up, so presenting the stored hash
hashes the hash and matches nothing. Somebody who reads the session table learns
which sessions exist and cannot use a single one of them — which is exactly the
property a password hash does *not* have, since there the stored value is what an
offline attack is run against. `hash_session_token` is a pure function with exactly
two callers, `LogIn` to store and `ResolveActorFromSession` to look up, which is
`fold_email`'s arrangement: one rule, two callers, no way to drift.

**75. The session token is returned once and there is no endpoint that reads one
back.** A client that loses its token logs in again. That absence is what makes a
leaked session table useless rather than catastrophic, and it is why
`POST /sessions` is the only response in the API carrying a secret. It is a 201
rather than a 200 — a session is a row with an id, an owner and an expiry, and this
request is what brought it into being — and there is deliberately no `Location`
header, because there is no URL at which a session can be fetched and pointing at
one that does not exist would be worse than pointing nowhere.

**76. Expiry is absolute, not sliding.** `SESSION_LIFETIME` is thirty days from the
moment of issue. A sliding window — "extend it on every request" — means a session
used daily never ends and one used monthly ends immediately, which is the opposite
of what a person expects from "stay signed in". It also turns every authenticated
*read* into a write and makes every request contend for SQLite's single writer. An
absolute expiry makes the whole thing a comparison against a stored moment, and
there is one write in a session's life: the one that creates it.

**77. `Session.is_expired(as_of)` takes the clock, it does not read it.** The same
rule as `Fund.is_matured(as_of)`, and for the same reason: a session that read
`datetime.now()` itself would be untestable at its boundary, which is the only
interesting place to test it. It uses `>=` and not `>` — a session is expired *at*
the instant it expires, not a moment later; the alternative is a session that is
valid for an instant its owner was never promised.

The wall clock therefore enters the system at exactly two lines, both at a
presentation boundary: `dependencies.current_actor` and `cli._current_actor`.
Everything underneath — `is_expired`, `LogIn.execute`, `SignUp.execute`,
`ResolveActorFromSession.execute` — is handed a moment.

**78. Revocation is deletion, not a flag.** Decision 49, arriving at sessions. A
`revoked_at` column leaves every query in the codebase obliged to remember to check
it, and one that forgets is a session that never ended. A deleted row is a state
that cannot be misread later. `LogOut` therefore deletes, `SessionRepository`
exposes `delete_by_token_hash`, and `Session` has no transition methods at all —
there is nothing to extend and nothing to revoke in place.

**79. `LogOut` takes a token, not an actor — and it is the one operation authorised
by the thing it destroys.** That is not a loophole; it is what makes the method
correct. Every other use case takes an actor that has already been resolved and asks
whether that actor may touch a resource. Here the caller presents a token and asks
for *that token* to stop working, so proving you hold it and being entitled to end
it are the same act. Giving it an actor would mean resolving the session first —
which can only fail for a token that is expired or unknown, and those are precisely
the tokens a client most needs to be able to discard. `DELETE /sessions/current`
carries the same reasoning over HTTP: an expired token that `/users/me` refuses with
a 401 is still accepted here, and the `logout` command works on a token the server
would no longer resolve.

It is idempotent, and it does not check that the session existed. The postcondition
is "this token does not authenticate", and that is already true if the token was
never valid, expired, or was signed out a moment ago. Reporting those as errors
would make every caller handle a failure indistinguishable from success in every
way that matters.

**80. `ResolveUserByEmail` is deleted, and that deletion is the phase's thesis.**
It answered "who is this?" from an *assertion*: it was handed an address, found or
created the account holding it, and returned it. Its own docstring called it *"the
one place in the codebase that acts before it is told who is acting"* — and the
part that mattered was not the acting-before-being-told, it was the **deciding**.
An unknown address produced a new account, which is how a shim turned an assertion
into a user.

`ResolveActorFromSession` replaces it and is still that one place, with a much
smaller referent: it only looks up. It is handed a token and reports the identity
the store already associates with it, or refuses. There is no branch in which it
creates anything, so a caller who presents a token it made up gets a 401 and never
an account. Its docstring claims this and `tests/application/identity/test_resolve_actor.py`
asserts it from three directions, including that a refused token leaves the user
table exactly as it found it.

It lives in the application layer rather than in a presentation because **two**
presentations resolve a token now — the API from an `Authorization` header, the CLI
from its session file — and two copies of a token-to-identity rule are two chances
to disagree about what counts as expired or which hash turns a token into a lookup
key. Such a disagreement has no visible symptom until somebody is signed in on one
surface and not the other.

**81. `LogIn` raises `InvalidCredentialsError` for both an unknown address and a
wrong password.** Decision 55 — a foreign wallet reporting as a missing one —
applied to the thing that hands out identities. Distinguishing them lets anyone with
a list of addresses learn which ones are registered without ever guessing a
password, and there is nothing a legitimate client would do differently with the two
answers, since both mean "check what you typed". The class name and the message are
both identical, which is what the pair of assertions in
`tests/application/identity/test_log_in.py` checks rather than either one alone —
the class is what the API sends as `error` and the message is its `detail`, so
either one differing is the leak.

`DuplicateEmailError` at sign-up is the deliberate exception: it *does* confirm an
account exists, and that is unavoidable, since the alternative is letting two people
register one address and discover it at the login form.

**The protection is on the words, not on the clock, and the gap is real.** An
unknown address returns without hashing anything, so it answers in microseconds
where a wrong password takes tens of milliseconds, and the difference is measurable
from outside. The fix — verify against a dummy hash when no credential is found — is
four lines, and it belongs in 2c with rate limiting, which addresses the same threat
directly and also covers the sign-up path this cannot. Stated here rather than
discovered in an incident.

**82. `PasswordCredential` is its own aggregate, and the hash is redacted in its
repr.** It is `(user_id, password_hash, updated_at)` with `user_id` as the primary
key — an account has one password at a time, so "the credential for this user" is a
single-valued fact and the schema says so. A surrogate key would allow two rows and
leave every reader to pick one.

The redacted `__repr__` is not decoration, for the same reason `PlainPassword`'s is
not: a dataclass reprs its fields, so a credential reaching a traceback or a log
line writes a value that is exactly what an offline attack is run against into a
place it was never protected in. The exception handler that prints `str(exc)` is
*already in this codebase* (`errors._detail`, `cli._describe`).

**83. `PlainPassword` owns the whole password policy, and it is not stripped.** At
least eight characters, at most 1024, non-empty, and a value of the wrong type is
refused as *malformed* rather than as *weak* — the split `User` already makes
between a value of the wrong kind and one of the wrong shape. A three-character
password refused at login comes back as `WeakPasswordError` (a 400) rather than as
`InvalidCredentialsError` (a 401), and it leaks nothing: the length policy is public
and every stored password satisfies it, so a password failing it was never
anybody's. Collapsing the two would send somebody to reset a password they had typed
correctly.

The no-strip rule is the one worth marking. `"hunter2 "` and `"hunter2"` are
different passwords, so a password is compared against nothing but itself — no trim,
no case fold, unlike the address, which is folded precisely because it is a handle
people retype. `SignUpIn` and `LogInIn` carry **no** length rule, so a bad password
arrives as a 400 in the domain's vocabulary rather than as a 422 in pydantic's, and
there is no second copy of the policy free to disagree with `PlainPassword`.

The password is read with `getpass` and never as a positional argument: `--password
hunter2` lands in the shell's history file, in the process table while the command
runs, and in whatever the terminal is recording. `signup` asks twice and `login`
once, and the asymmetry is deliberate — a mistyped password at sign-up creates an
account whose password nobody knows, including its owner; a mistyped one at login
simply does not match, which is a refusal the user understands immediately.

**84. Sign-up writes the user and the credential in one unit of work.** The pairing
argument `unit_of_work.py` already makes for notices and receipts. A user with no
credential is an account nobody can ever log into, and it would be **invisible** —
the sign-up would have reported success, the address would already be taken, and the
person could neither log in nor try again. The `identity` group on `UnitOfWork` is
correctness, not convenience, and says so where the others do.

Note what the *database* does not do here: `password_credentials.user_id` is a
primary key and **not** a foreign key into `users`, and `sessions` has no foreign
key either. Identity-table integrity is guaranteed by the single unit of work, not
by the schema — a real constraint belongs with the `wallets` foreign-key rebuild the
roadmap already carries, and
`test_sqlite_password_credential_repository.py` asserts the current behaviour rather
than leaving a reader to assume the database is doing work it is not.

**85. `SignUp` does not sign anybody in.** The password is in hand and a session
could be minted immediately, which is exactly why the separation is worth stating:
creating an identity and *proving* you hold it are different acts. A `signup` that
also wrote a session file would mean the first token on a machine came from a
command that never checked the password it had just been given. `POST /users`
returns the account and no token, for the same reason and with the same
consequence: a client that wants both makes both requests, and the second one is the
one that could have failed — which is the distinction worth keeping, because "the
address was free" and "the password works" are different facts.

**86. `POST /users` and `POST /sessions` are the only unauthenticated writes in the
API.** That is inherent to being the way in, and it is why rate limiting is a real
2c item rather than a nicety. Every other endpoint resolves an actor.

**87. The grade table gains a 401, and `MissingActorHeaderError` becomes
`MissingCredentialsError`.** Its own docstring had already promised exactly this —
*"The status changes to 401 in Phase 2, along with the header that replaces this
one."* `UNAUTHORIZED = (InvalidSessionError, InvalidCredentialsError)`, which are
one class for three situations (unknown token, expired token, orphaned session) and
one class for two (unknown address, wrong password) respectively. `DuplicateEmailError`
joins `CONFLICT`.

An orphaned session — a valid token whose user row is gone — is the interesting one:
`get_by_id` raises `UserNotFoundError`, which would reach a client as a **404**, a
different answer to a question with one answer. `ResolveActorFromSession` catches it
and re-raises `InvalidSessionError` `from None`, and
`test_resolve_actor.py` asserts the two are indistinguishable in class *and* message
rather than only that the right one is raised.

**88. Endpoints stay `def`, and this is the phase that makes it matter.** FastAPI
runs a synchronous endpoint in a threadpool, so argon2's verify — tens of
milliseconds by design — blocks a worker thread rather than the event loop. It is
the first genuinely slow call in this API, and moving the handlers to `async def`
would be the change that turns a login into a stall for every other request in
flight. Decision 70 said the endpoints were synchronous and the handlers were not;
this is the reason that was not merely tidy.

**89. `--user` and `DEV_USER_EMAIL` are deleted, together with the header.** The
README already committed to the pairing, and 1b set it up so that there was **one**
seam to change rather than two. What replaced them is `--session PATH`, defaulting
to `$BUDGET_SESSION` and then `~/.config/budget/session`, mirroring how `--db`
defaults to `DEFAULT_DATABASE_PATH` — both read in `settings.py`, which stays the
one reader of `os.environ`.

Where `DEV_USER_EMAIL` used to be there is now a comment explaining why there is no
default: *leaving a default here would have been the whole bypass in one line.*

`signup`, `login` and `logout` are the new commands. `login` is **the only command
that writes the session file**, which is worth knowing when something goes wrong
with the CLI's identity: there is exactly one place a token can come from, and it is
a password typed at a prompt. `logout` calls the server **first and then** deletes
the file, and the order is the whole of the safety — deleting the file first would
mean a failure in between leaves a token that still works with no copy of it
anywhere, so the session is not ended and the user cannot end it, because the thing
that names it is gone.

**90. The token file is created `0600` by `os.open`, not written and then
`chmod`ed.** The two-step version leaves a window between the `open` and the
`chmod` in which the file exists with whatever the umask allows, and a token that is
world-readable for ten milliseconds on a shared machine is a token that was
world-readable. `os.open(path, O_CREAT|O_WRONLY|O_TRUNC, 0o600)` takes the mode as an
argument, so the file is born private. The mode is subject to the umask, which can
only ever *remove* bits — so the result is at most `0600` and possibly stricter, and
that asymmetry is the reason this is the safe direction to be wrong in. The test
asserts "no group or other access" rather than equality with `0o600`, for the same
reason.

The file holds the token and nothing else, with a trailing newline. There is no
format to parse, deliberately: a file with fields in it invites a reader to trust
one of them, and the only field worth trusting here is the token, which is the one
thing that cannot be checked locally anyway.

**91. `main` resolves the actor only for the commands that need one.** Today it
resolved before dispatch, unconditionally, and the README's "Still open" list
already carried the complaint. `signup`, `login`, `logout` and `plan tick` are now
dispatched **above** the line that resolves an actor, and `plan tick` is the one
that made it required rather than tidy: it is the only command that runs as nobody,
serving every user with a plan, and a scheduler that needed an actor would need a
privileged account to run under — which is exactly the seat this arrangement
removes. It works on a machine where nobody has ever logged in.

The three identity commands run there for a different reason: they are how a session
comes to exist or cease to, so none of them can require one. It also means a corrupt
session file cannot stop somebody signing *in* over it, which is the ordinary repair.

**92. `NotSignedInError` is a `CliError`, not a `MoneyError`.** "There is no file at
this path" is not a fact about money or identity; it is a fact about this
presentation's storage, and it belongs to the CLI's own root — which is why the CLI
catches two roots where the API catches `MoneyError` and `ApiError`. Both render at
the same line of output with the same exit code, because from a terminal they are
the same event: the command did not do the thing, and here is why.

The message names the path and the fix (*"not signed in: no session at
/… - run 'login' first"*), because a wrong `--session` and never having logged in
are different mistakes and the fix for each is different.

**93. The test suite injects `FakePasswordHasher`, and argon2 really runs in exactly
two files.** Argon2 is *designed* to be slow, and the suite has roughly two hundred
sign-up and login operations across it — at tens of milliseconds each, that is
twenty to thirty seconds of wall clock spent proving that arithmetic works. The fake
is injected at the two seams the composition root exposes:
`create_app(password_hasher=...)` for the API and `build_sign_up` /
`build_log_in` for the CLI helpers.

The trade is only honest if the thing being skipped is tested somewhere, and
`tests/infrastructure/security/test_argon2_password_hasher.py` is where: that a hash
is not the password, that two hashes of one password differ (so `LogIn` can never be
written to compare hashes rather than call `verify`), that verify is false for the
wrong one, that the variant is `argon2id` and the parameters travel inside the
encoded string, and that a value which is not a hash at all *raises* rather than
returning false — because swallowing that as a false would present database
corruption as a user who cannot remember their password.

That last claim turned out to have **two shapes, and they share no ancestor**.
`argon2.exceptions` puts `VerifyMismatchError` under `VerificationError` under
`Argon2Error`, but `InvalidHashError` — the one raised when the *header* is not
recognised, before the library is even called — descends from `ValueError`. Both
propagate through `verify` untouched, which is the behaviour that was wanted, but a
single `except` can never name both, and the first version of this test asserted
`VerificationError` for the `InvalidHashError` case and failed. There is now one test
per shape, and the adapter's docstring says why the net is drawn where it is:
`VerifyMismatchError` is the only class that means "the password was wrong".

`tests/presentation/test_cli_identity.py` is the one presentation file that uses the
real adapter, because `signup` and `login` are the two commands whose entire job is
a password, and a fake here would mean nothing in the suite ever proved the CLI can
verify a password the CLI wrote.

**One API test uses it too, and only because it crosses between the two doors.**
`test_actor.py::test_the_cli_and_the_api_resolve_one_address_to_one_account`
registers through the CLI — which builds its own composition root, so it hashes with
argon2 — and then signs in over HTTP through a client that by default verifies with
the fake. Both sides are individually correct and the pair cannot work, and the
failure presents as `InvalidCredentialsError` for a password that was typed
correctly, which is the one thing that test exists to rule out. It therefore asks for
a new `argon2_client` fixture instead of the standard one. This is worth recording
rather than quietly fixing: the fake is invisible when a test stays on one side of
it, so **any test that straddles the CLI and the API needs the real hasher**, and the
symptom of forgetting looks exactly like the bug being tested for.

**94. `as_user(email)` kept its signature, and that was the goal.** The API fixture
used to return `{"X-User-Email": email}`; it now registers the address, logs in, and
returns `{"Authorization": "Bearer …"}` — and roughly two hundred call sites across
nine files were left alone. The reason it could be is that the fixture was already
the right *shape*, a function from an address to headers, and only its contents were
a lie. What changed is everything the contents mean: `as_user(BOB)` used to *assert*
the caller was Bob and now has to **prove** it.

Two things deliberately do not happen in it. The address is not folded before being
used as a cache key, because folding is the application's rule and a fixture that
did it would hide the one place it can be got wrong. And the header is spelled out
rather than imported from `dependencies`, because a fixture built from the server's
own constant would pass every test even if that constant were wrong — both sides
would be wrong together.

**95. `no_settings_environment` clears `BUDGET_SESSION` as well as `BUDGET_DB`.** A
developer with it exported has a token in that file, and the suite would otherwise
authenticate as them against a `tmp_path` database. The existing comment already
described this failure mode for the database; this is the same hazard arriving
through a second variable.

**96. The loopback bind kept its conclusion and lost its reason.** `__main__.py`
justified binding `127.0.0.1` by the shim: the header named a user and proved
nothing, so a process listening on every interface was one anyone on the network
could act as anyone through. Phase 2a deleted the header and the sentence outlived
it. The bind is still right, and for the opposite kind of reason - this API speaks
plain HTTP, so a wider bind now means passwords and session tokens crossing the
network in the clear. Rewritten rather than deleted, because a stale rationale is
worse than none: the next reader takes "the shim's blast radius" as a constraint
that has lifted, and concludes the default is free to widen.

#### The manual verification, against a real server

The plan's six checks were then run against a **real uvicorn process on a socket**
rather than starlette's in-process `TestClient`, which is a genuinely different
claim: the client the suite uses skips the socket, the ASGI server and the process
boundary. The script built its own database in a temp directory and deleted it on
exit, so it could not touch `budget.db` or a developer's real session file.

Three results are worth keeping:

- `X-User-Email: whoever@example.com` answers **401**, as does a fabricated bearer
  token - the same answer a missing token gets. The endpoint cannot be asked which
  tokens are real, which is decision 55 arriving in the authentication layer.
- Bob's 404 on alice's wallet is **byte-identical** to a random UUID's 404 *after
  both crossed uvicorn, the serializer and a socket*. The suite compares two bodies
  it built in-process; this compares two that went over the wire.
- `plan tick` exits 0 with **no session file at all**, which is decision 91's claim
  that the scheduler is not a privileged actor. The token file came out mode `0600`
  (decision 90).

One check needed a human, and it is a property of the design rather than a defect:
**`getpass` prefers `/dev/tty` to stdin**, falling back to stdin only when there is
no controlling terminal. A script that pipes the password in does not work - the
prompt ignores the pipe and waits at the terminal, which is what happened on the
first attempt. That is decision 83's refusal seen from the other side: the password
cannot be smuggled in through an argument, and it cannot be smuggled in through a
file descriptor either. Driving the CLI non-interactively therefore means taking the
controlling terminal away first, and nothing in this project does. The suite never
noticed the distinction and could not have: it answers the prompt through a
monkeypatched `getpass` (`typed_password` in `tests/conftest.py`), which substitutes
for the reading rather than performing it - so the one place the terminal's
preference matters is the one place no test stands in for.

A second thing that attempt surfaced, and it was a mistake in the harness rather
than in the code: the API has no module-level `app` to point uvicorn at. The
correct target is the factory, `app.presentation.api.app:create_app --factory`, as
`__main__.py` documents. A module-level `app = create_app()` would build the whole
composition root at *import* time against whatever the environment held at that
moment, and hand a live application to anything that imported the module for
another reason.

### Still open

- **Rebuilding `wallets` to carry a foreign key to `users`** (decision 59). A
  SQLite table rebuild with `PRAGMA foreign_keys` off, on a table holding real
  balances, which wants a backup story worth trusting before it is attempted. The
  repository provides the integrity in the meantime.
- ~~**Removing the CLI's `--user` shim and the API's `X-User-Email` header** once
  there are real sessions.~~ **Done, in Phase 2a** - both deleted in one commit, as
  this entry said they would be, along with `ResolveUserByEmail` and
  `DEV_USER_EMAIL`. The reasoning is kept because it is the argument for why they
  had to go *together*: deleting `--user` while the API still resolved an actor from
  an unverified header would have removed the honest, obviously-development door and
  kept the one that looks like a real one. The single seam the entry predicted -
  "`ResolveUserByEmail` is the single thing both doors call, so that commit has one
  place to change and not two" - is exactly how it went. See decisions 80 and 89.
- **Rate limiting on `POST /users` and `POST /sessions`** (decision 86). They are
  the only unauthenticated writes in the API, which is inherent to being the way in
  - and it makes them the only endpoints where an unauthenticated caller can spend
  server resources. Two things ride on this: the argon2 work a login costs, and
  **the timing gap decision 81 documents**, where an unknown address returns without
  hashing anything and so answers measurably faster than a wrong password. The fix
  for the second is four lines - verify against a dummy hash when no credential is
  found - and it belongs here rather than on its own, because rate limiting
  addresses the same threat directly *and* covers the sign-up path, which the dummy
  hash cannot.
- **Rebuilding the identity tables to carry foreign keys** (decision 84).
  `password_credentials.user_id` and `sessions.user_id` are plain columns, so the
  pairing between an account and its credential is guaranteed by `SignUp` writing
  both rows in one unit rather than by the schema. Same shape as the `wallets`
  rebuild above, same blocker, and worth doing in the same pass.
- **Re-hashing a password whose parameters have fallen behind.** Found while pinning
  `argon2-cffi`, and it is a gap rather than a detail. Every encoded hash carries the
  cost parameters it was made with, which is what makes raising the cost *safe* — old
  passwords keep verifying. But `PasswordHasher` is a two-method port, `hash` and
  `verify`, and neither re-hashes, so a password set under an old cost stays at that
  old cost until its owner changes it. The migration is re-hash-on-next-successful-login,
  which is the only moment the plaintext is in hand — a third port method, or a third
  return value from `verify`, and either one is a change to a port rather than a line
  in the adapter. Not urgent: nothing has raised the cost yet, and the pin in
  `requirements.txt` is what makes that true on purpose. Also written into the
  `Argon2PasswordHasher` docstring, because "the parameters travel inside the hash" is
  otherwise very easy to read as "the upgrade is handled".
- **A second caller of `list_by_status` is a hole if it is user-reachable**
  (decision 57). Today the two callers are both installation-wide background jobs
  and `PlanService` does not expose it. That boundary is enforced by convention
  rather than by a signature, so it is worth re-reading when a third caller appears.
- ~~**`main` resolves the development user for every command, including the ones
  that do not act as anybody.**~~ **Done, in Phase 2a** (decision 91). The identity
  commands and `plan tick` are now dispatched above the line that resolves an actor,
  so a fresh database no longer grows a `dev@localhost` row on the way past a tick -
  there is no default identity left for anything to create.
- **Password reset and email verification.** Neither blocks the design: an account
  that cannot reset its password is inconvenient, an account that cannot exist is a
  non-starter. But a password that is forgotten today is an account that is gone,
  and the confirmation mail is also what would make an address *verified* rather
  than merely claimed.

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

The foundation, and nothing else on this list works before it. Split in two,
because the two halves have different risks and the first one is done.

**Phase 1a - ownership in the domain and the store. Complete.** The question this
answered was never "is there a `users` table", it was "can any code read a wallet
without naming whose it is" - because the answer used to be yes, and it would have
stayed yes under an API that merely *passed* a user id around. What shipped:

- A `User` aggregate and a `users` table, so `wallets.user_id` finally points at
  something: an email folded to one spelling, an optional Google subject id, a
  created-at moment, and no credential of any kind (decision 53).
- **Authorization by construction rather than by policy.** `get_by_id` is gone
  from the wallet and plan ports and `get_owned(id, user_id)` replaced it, so there
  is no unscoped wallet read left to call and "read someone else's wallet" is not a
  reachable mistake (decision 54).
- A foreign wallet reports as *not found*, indistinguishable from a nonexistent
  one, so a guessable id is not an enumeration oracle (decision 55).
- Plans carry their owner, which is what lets the tick run every user's plans
  without being granted authority over any of them (decision 56).
- The service's actor bound at construction, so the 248 existing call sites kept
  their signatures and a request cannot change who it acts as halfway through.

`tests/application/planning/test_run_due_plans.py::TestTheSchedulerIsNotAPrivilegedActor`
is the load-bearing test: two users, both plans due, one tick, each run reading its
own wallet as its own owner.

**Phase 1b - the FastAPI application. Complete.** It had nothing left to invent,
which is the whole reason 1a came first: the API calls the same use cases the CLI
does, and every one of them already knows who is asking. What shipped:

- A FastAPI application under `app/presentation/api/`, a sibling of `cli.py`, with
  `create_app(unit_of_work_factory=..., settings=..., database_path=...)` following
  the composition root's existing optional-injection pattern. `WalletService` and
  `PlanService` are the seam, untouched (decision 48).
- **The boundary is at balance changes, not at writes** (decision 62) - reads,
  wallet and pot creation, and plan steering. Every operation that moves money,
  plus the two installation-wide jobs, is held until Phase 2.
- The actor is an `X-User-Email` header (decision 63), resolved through the same
  `ResolveUserByEmail` use case the CLI's `--user` calls (decision 64). One
  service is built per request, for that actor (decision 65).
- A foreign wallet is a 404 with the body a typo gets, asserted by asking for both
  and comparing (decision 66).
- Refusals graded 404 / 409 / 400 / 422 / 500 by what kind of question was refused
  (decision 67), money as a string on the wire (68), enums as their `.value` (69).
- `GET /health`, which needs no actor and says nothing about the installation's
  contents.

The CLI's `--user EMAIL` is a **development shim**, not a design - and so is the
header, which is the same shim by another transport rather than a replacement for
it. The earlier claim that `--user` "must not survive into the API" is corrected
above: both survive until the real actor arrives from an authenticated session in
Phase 2, and both are deleted in that commit. Deleting the CLI's half early would
have removed the honestly-labelled development door and kept the one that looks
like production.

**That is exactly what happened, one phase later** (decisions 80 and 89): both went
together, in the commit that introduced sessions.

### Phase 2 - Authentication

The phase that turns Phase 1b's honest shim into a real actor. Split in two,
because the first half is the one with the hard property to prove and the second is
the one that spends it.

**Phase 2a - sessions, and the death of both shims. Complete.** The lock the
thirteen money endpoints are held behind, and the half of Phase 2 with the hard
property to prove: an actor that can only be reached by proving you are it. What
shipped:

- Email and password with argon2id (decision 72), the credential in its own table
  rather than on `User` (73), SHA-256 for the session token and the difference from
  argon2 stated rather than assumed (74).
- An opaque session token, returned once and never readable again (75), expiring
  absolutely thirty days out (76), checked rather than self-reading its clock (77),
  revocable by **deletion** rather than a flag (78).
- `LogOut` taking a token instead of an actor (79), `ResolveUserByEmail` deleted and
  replaced by `ResolveActorFromSession` (80), and one refusal for both an unknown
  address and a wrong password (81).
- `POST /users`, `POST /sessions` and `DELETE /sessions/current`; the 401 row in the
  grade table; `MissingActorHeaderError` becoming `MissingCredentialsError` (87).
- `signup`, `login` and `logout` in the CLI, `--user` and `DEV_USER_EMAIL` deleted,
  a `0600` session file created with `os.open` rather than `chmod`ed (89, 90), and
  `main` resolving an actor only for the commands that need one (91).

The load-bearing tests are
`tests/presentation/api/test_actor.py::TestTheShimIsDead` (the header that used to
work now achieves nothing, and neither does a token shaped like an address) and
`tests/application/identity/test_log_in.py::TestTheSameRefusalForBoth` (the
account-enumeration oracle, closed).

**Phase 2b - the money endpoints.** Every operation held by decision 62, now that
there is an actor worth spending a session on.

**Phase 2c - the rest of it:**

- Google OIDC signup, matching on the Google subject id rather than the email. This
  is why `User` carries an optional `google_subject` and why `LogIn` already has a
  branch for an account with no password credential.
- Rate limiting on the auth endpoints first - login and signup are the ones worth
  brute-forcing - then on the API generally. It also carries the timing fix
  decision 81 documents.
- Password reset and email verification.
- Session listing and per-device revocation. The store already supports it, since a
  session is a row and revocation is a deletion; what is missing is the endpoints.
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
