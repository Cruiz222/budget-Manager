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

Every command except `signup`, `signup-google`, `signup-phone`, `confirm-phone`,
`login`, `login-google`, `login-phone`, `logout`, `confirm-email`, `reset-password`,
`confirm-password-reset`, `plan tick` and `reconcile` acts as somebody, and that
somebody is proved by a token rather than named by a flag. So a fresh install starts
here:

```
.venv/bin/python -m app.presentation.cli --db budget.db signup me@example.com
.venv/bin/python -m app.presentation.cli --db budget.db login  me@example.com
.venv/bin/python -m app.presentation.cli --db budget.db whoami
```

Three things about that are worth knowing:

- **`signup` does not sign you in.** It creates the identity; `login` is what
  proves you hold it, and it is one of the three commands that write the session
  file - the others being `login-phone` and `login-google`. Keeping signing up and
  signing in apart is the whole point of the phase (decision 85) - a registration
  that silently authenticated would mean the first token on a machine came from a
  command that never checked anything the person knew.
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

**Moving the address an account holds takes two commands, and they do not both act
as somebody.** `change-email` needs a session and asks for the current password;
`confirm-email` needs neither, because the whole of its authorisation is the code
that was mailed to the address being moved *to*:

```
.venv/bin/python -m app.presentation.cli --db budget.db change-email me@example.com
    # the current password is asked for; then one of two things happens
.venv/bin/python -m app.presentation.cli --db budget.db confirm-email
    # the code is asked for; nothing else is
```

The split is the point rather than an implementation detail. Somebody can ask at a
desk and answer from a phone, on a machine that has never logged in - `confirm-email`
reads no session file and is dispatched above the line that resolves an actor, so it
works where `whoami` would not. What it costs is that the password proof and the
mailbox proof have to be held by whoever completes the change, which is the
trade decision 165 records.

**With no mail account configured the second command is not needed.** `change-email`
applies the change on the password proof alone and says which variable is missing,
so a fresh install can still fix an address no provider will bill (decision 173).

**Forgetting the password takes two commands of the same shape, and here neither acts
as somebody.** `reset-password` takes an address and asks for nothing; the second
takes the code that was mailed *to* that address:

```
.venv/bin/python -m app.presentation.cli --db budget.db reset-password me@example.com
    # nothing is asked for; the code goes to whatever that address names
.venv/bin/python -m app.presentation.cli --db budget.db confirm-password-reset
    # the code, then the new password twice
```

Both are dispatched above the actor line, and that is the feature rather than a
convenience: a person who has forgotten their password cannot log in, so a command
that needed a session would refuse exactly the person it exists for. The new password
is asked for **twice** where the code is asked for once, because a mistyped password
here is accepted, stored, and silently becomes the one they have - which recreates
the lockout the command was run to end, while reporting success (decision 191).

Two differences from `change-email` are deliberate and worth knowing before either
surprises you. `reset-password` **prints whether an account was found** - the API
answers identically either way and cannot afford not to, but a terminal that can write
to this database is not a public surface, and the person who mistyped their own address
is the one who would otherwise be left guessing (decision 185). And confirming a reset
**signs every session on the account out**, including the one on the machine running
it, because the premise of the whole flow is that somebody else may know the old
password (decision 188). Both commands need a mail account; with none configured the
first refuses and names the variable, which is the one place this flow is stricter than
the address change (decision 186).

**Signing up with Google takes two commands of the same shape, and they are the only
pair here that needs configuration before it can read its own input.** The token is
read at a hidden prompt rather than passed - it is a live bearer credential for about
an hour, so the history file is the last place it should be - and both commands refuse
naming the missing variable when the installation has no `GOOGLE_CLIENT_ID`, because
there is no version of either that could work without one:

```
.venv/bin/python -m app.presentation.cli --db budget.db signup-google
    # the id_token is asked for; nothing else is
.venv/bin/python -m app.presentation.cli --db budget.db login-google
    # the id_token again, and the session file is written
```

Neither creates the other's outcome, and that is the design rather than a missing
convenience: `signup-google` registers an account and starts no session, exactly as
`signup` does, and `login-google` never registers - a token naming an account this
installation does not have is refused in `login`'s own words, and the remedy is the
command above it. A person who runs the wrong one is told which they wanted rather
than quietly given the other.

**The token itself comes from outside this system today**, which is worth knowing
before running either command: Google's OAuth 2.0 Playground, or the operator's own
client. The browser flow that would obtain one *for a person* needs a redirect
address, and this installation deliberately has no setting for one - so that half is
Phase 4, and both commands' own docstrings say so rather than implying they can
produce a token themselves.

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

### Reconciling the payments whose webhook never arrived

The webhook is the fast path and it assumes the message arrives. When it does not -
a proxy swallowed it, the process was down, the endpoint was misconfigured for an
afternoon - the payer's money has left their account, the ledger row still says
`PENDING`, and nothing in this system is capable of noticing. `reconcile` is the
thing that notices: it asks Paystack about every deposit still in flight and
settles what Paystack says arrived, through the same `SettlePayment` the webhook
uses, so a recovered payment and a delivered one leave identical state.

It is a second cron line, with its own cadence:

```
17 * * * * cd /path/to/budget-Manager && PAYSTACK_SECRET_KEY=sk_test_... .venv/bin/python -m app.presentation.cli --db budget.db reconcile >> reconcile.log 2>&1
```

Five things about that line are worth reading, and two of them differ from the tick:

- **Hourly, not every five minutes.** The tick's five minutes exists because a
  thirty-minute warning window can be stepped straight over (decision 22), and
  there is no equivalent window here. A webhook normally settles a deposit within
  seconds, so anything this job finds is already an anomaly - once an hour is
  frequent enough that a lost webhook is noticed the same working day, and rare
  enough that a healthy installation spends almost no provider calls on it. The
  minute is offset from the tick's, so a busy database does not have both jobs
  waking at once.
- **No session, and it needs none - for a different reason than the tick's.** Like
  `plan tick` it is dispatched above the line that resolves an actor (decision 91).
  The tick is actorless because it serves *everybody*, minting an executor per
  plan; this one is actorless because it serves nobody in particular and reads no
  wallet at all. It hands a reference to `SettlePayment`, which derives the owner
  from the ledger row it is settling.
- **A quiet run prints one line and exits 0**, and the line is the point.
  `nothing in flight` is what an operator *wants* to see, because it means the
  webhook endpoint is keeping up - the opposite of the drains, which stay silent
  when there is nothing to do. "Nothing was in flight" and "this job is not
  running" otherwise look identical in a log.
- **A provider that cannot be reached is recorded, not raised.** That row is
  reported as `failed`, the run continues to the next one, and the next run asks
  again. One unreachable reference says nothing about the next.
- **Nothing is ever marked `FAILED` by this job.** A checkout Paystack reports as
  `abandoned` is reported and left `PENDING`, for the hazard decision 142 gives:
  a row failed a minute before its payment arrives would take the payer's money
  and never credit it.

The one variable it reads - the email variables above are unchanged and unrelated:

| variable | default | note |
|---|---|---|
| `PAYSTACK_SECRET_KEY` | — | required; without it there is nothing to ask with |

**With no key set the run still exits 0.** It prints
`note: nothing to reconcile as of ... - PAYSTACK_SECRET_KEY is not set` and stops,
which is the tick's missing-mailbox rule one provider over: a deployment that does
not take card payments should not have a cron line mailing its operator every
hour. The variable is named in the note so that somebody who *expected*
reconciliation to be running finds out that it is not.

To watch a recovery happen without waiting for a real webhook to be lost, start a
deposit over HTTP (`POST /wallets/{id}/deposits`, with the same test key), then
**do not** post the webhook, then run `reconcile`. The balance moves, the row is
`SUCCESSFUL`, and the receipt is queued - exactly what a delivered webhook would
have left. Run it a second time and it says `nothing in flight`, because the row
is no longer in flight.

**That recipe is worth more than it was written for, and it is the reason to keep
it.** Running it against a real test key is how the deposit route turned out never
to have worked at all: the first `POST` came back

```
{"status":false,"message":"Invalid character in transaction reference",
 "code":"invalid_character_in_reference"}
```

- the reference carried a colon, Paystack refuses colons, and every deposit this
system had ever attempted had been refused at the far end in the same way. Two
thousand passing tests had nothing to say about it, because the fake provider
accepted whatever string it was handed (decisions 151 and 155). The step that
found it was not a test and could not have been: it was the one instruction in
this file that talks to the *real* provider, and everything in flight above it was
self-consistent without being true. Any future step that reaches the real service
earns its place the same way.

**The same run found a second one, and it is the better argument for the recipe.**
With the reference fixed, the deposit opened - and then the account it was opened
for, `live@localhost`, came back

```
{"status":false,"message":"Invalid Email Address Passed",
 "code":"invalid_email_address"}
```

because a deposit is billed under the account's own address and this system's
entire email rule is that it contains an `@`. The fix was two `curl`s that differed
in one field, which is not something any test in this repository could have been
written to do: the suite's own accounts were at `@localhost` too, so the tests had
the same blind spot as the code and agreed with it (decisions 156 to 160). What a
live run buys is not coverage. It is an oracle for the rules this codebase does not
own.

**The third recipe is the one that undoes the second finding, and it is the first
thing in this file built against that oracle rather than discovered by it.** The
account refused a deposit at `live@localhost` cannot simply be re-registered -
sign-up now refuses the address - so the way to watch the whole slice work is to
start from a stranded row, which is what a real installation's affected accounts
already are:

```
# 1. the account the provider will not bill, and the refusal that proves it
.venv/bin/python -m app.presentation.cli --db budget.db whoami     # nobody@localhost
curl -sX POST localhost:8000/wallets/$W/deposits …                 # 400 PayerEmailRefusedError

# 2. move it, with a mail account configured
.venv/bin/python -m app.presentation.cli --db budget.db change-email me@example.com
#   confirmation code sent to me@example.com | expires 2026-09-13T14:22
#   run 'confirm-email' and enter the code to complete the change
.venv/bin/python -m app.presentation.cli --db budget.db confirm-email
#   email changed to me@example.com | was nobody@localhost
#   nobody@localhost was told about the change

# 3. the same request that was refused above, now accepted
curl -sX POST localhost:8000/wallets/$W/deposits …                 # 201, a checkout_url
```

Step 3 is the whole slice in one line, and it is the same `curl` that came back
`invalid_email_address` the first time. Two things about running it against real
infrastructure are worth watching, and neither is provable from the suite: that the
verification mail actually *arrives* at the new address (a local catcher is enough
to watch it leave the process - `aiosmtpd`, as in "Running the scheduler"), and
that Paystack accepts the *moved* address where it refused the old one, which is
the one claim the whole entry rule rests on (decision 162). If the second ever
fails, the rule is wrong in the direction that matters - it would have let somebody
move an account to an address the provider still refuses - and the remedy is the
pair of `curl`s that found the rule, run again.

**The fourth recipe is a forgotten password, and it is the only one that needs no
`curl` to do the thing it is about.** Both halves are terminal commands, which is the
feature rather than a shortcut: the person running it cannot log in, so a recipe that
began by obtaining a session would be watching a different flow.

```
# 1. signed in on a laptop, so that the sign-out below is a real one
.venv/bin/python -m app.presentation.cli --db budget.db login me@example.com

# 2. ask, with no password and from a machine that has never logged in
.venv/bin/python -m app.presentation.cli --db budget.db reset-password me@example.com
#   reset code sent to me@example.com | expires 2026-09-13T14:22
#   run 'confirm-password-reset' and enter the code to set a new password

# 3. read the code out of the delivered message and answer it
.venv/bin/python -m app.presentation.cli --db budget.db confirm-password-reset
#   reset code: <paste it, whitespace and all>
#   password: <the new one, twice>
#   password changed for me@example.com
#   1 session signed out
#   me@example.com was told about the change

# 4. what the single-use claim and the revocation did
.venv/bin/python -m app.presentation.cli --db budget.db whoami
#   not signed in: ... - run 'login' first
.venv/bin/python -m app.presentation.cli --db budget.db login me@example.com
#   the OLD password is refused; the new one signs in

# 5. the same request again, which supersedes rather than accumulating
.venv/bin/python -m app.presentation.cli --db budget.db reset-password me@example.com
#   one row for the account, and the code from step 2 is now refused as one that
#   never existed
```

Four things are worth watching, and **the suite asserts the shape of all four** - what
a run against real infrastructure adds is the parts a test cannot hold: a live SMTP
conversation rather than a fake channel, a paste typed at an actual terminal rather
than a monkeypatched `getpass`, and a transcript somebody can read. That a code pasted
with the whitespace a terminal selection carries is *accepted* - decision 180's fix,
demonstrated when that decision was written and shipped with this slice. That the mail
arrives at whatever address the account holds, which is the reason this endpoint is not
an account-takeover: the request proves nothing, so everything it can do depends on a
mailbox the caller either owns or has already compromised (decision 189). That the
old password stops working while the *new* one signs in, which is the whole flow in
two commands. And that asking twice leaves one row rather than two, which is the `ON
CONFLICT(user_id)` upsert doing what the schema says rather than a check anybody
enforces.

**Then the half that cannot be seen, and is worth looking for anyway.** With the API
running,

```
curl -sX POST localhost:8000/password-resets -H 'content-type: application/json' \
     -d '{"email":"me@example.com"}'
curl -sX POST localhost:8000/password-resets -H 'content-type: application/json' \
     -d '{"email":"nobody@unknown.invalid"}'
```

answers with the same bytes both times - the same 202, the same
`{"status": "accepted"}`, no header or length to tell them apart (decision 184). What
differs is how long each one takes, and the difference is an SMTP round trip rather
than a rounding error. That gap is the open item decisions 184 and 189 name, and this
pair of calls is where somebody can watch it rather than read about it.

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
    429  the same caller is asking too often, and the answer is to come back later
    500  a bug

Four of those six are defaults rather than lists. `NOT_FOUND` and `CONFLICT` are
the exceptions worth naming, and **everything else under `MoneyError` is a 400** -
which is the safe direction to be wrong in, because a refusal this module has
never heard of is far likelier to be about a value than about a resource's state.
Nothing new has to be registered for a new domain exception to be graded sensibly;
only a genuinely state-shaped refusal has to be added to `CONFLICT`, and forgetting
to is a milder failure than the alternative.

**The 429 is a default in the same sense and a new one in another: no `MoneyError`
can ever reach it** (decisions 230-231). The domain has no opinion about how often
somebody knocked, so it is raised by this layer and lives on `ApiError` beside the
401 and the 503 - and it is worth reading beside 400 rather than beside 503. A 400
sends a client to fix its request and a 503 sends it away from the installation,
while a 429 says the request is fine and the answer is *not yet*.

**The table has grown twice since this was written and the row count above was left
alone**, because what it was counting is the defaults and not the table. **401**
arrived with sessions and sits *above* 404, since a question about a resource cannot
be asked until the caller is known and a 401 says it never got that far; **503**
arrived with payments and is the installation speaking rather than the request. Both
are named where they belong - `test_actor.py` and the payment routes - rather than
back-filled here, so that the five rows above stay the record of what was decided at
the time.

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
rate limiting - of which the first has since shipped (decisions 204-213) and the
second is still the next thing owed.

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

**Amended: the email-change request is a second endpoint that mints a secret, and
it is not a second response carrying one.** It sends a 256-bit token to an address
and answers the caller with `{"status", "email", "expires_at"}` — no token, in
either the pending or the applied case, and a test asserts the code appears
nowhere in the response body. So this entry's sentence survives the new endpoint
with its meaning intact and its scope widened: two endpoints now mint a credential,
one of them hands it to the caller and the other can only post it somewhere. The
reason is the same one, one step further out — a secret that never comes back over
the wire cannot leak from a log, a proxy or a shoulder, and the mailbox is a
channel this system does not have to secure. See decision 176.

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

**Amended: there is now a third, and it is not a way in.** `POST
/email-changes/confirm` takes no credentials at all - the mailed token is the whole
of its authorisation (decision 166) - so "unauthenticated" is true of it in the
sense that matters to a rate limiter and false in the sense this entry meant. It is
not a door into the system; it is the second half of a change somebody already
authenticated for, and the credential it spends was posted to an address rather
than handed to the caller. It is a member of this family for exactly one reason:
an unauthenticated caller can spend server resources here, and the resource is an
SMTP round trip plus a 256-bit token comparison. See decision 167 for why that
token is 256 bits rather than six digits, which is this entry's own argument
reaching the next endpoint.

**Amended once more: the count is five, and the fifth is the first that aims at an
account its caller has no claim on.** Both password-reset routes joined this family
after the change flow, and
between them they split the two ways of being a member. `POST
/password-resets/confirm` is the third entry's twin - authorisation arrives by mail,
the caller proves nothing at the time, and the only thing it can do is spend what a
mailbox delivered - and it differs from the address change's confirm only in which
secret the mailbox lets its reader replace; it is the sharper of the two for exactly
that reason, since every session on the account is deleted by it (decision 188).
`POST /password-resets` is unlike all four of the others, because it needs no proof
of anything at all. It takes an address, writes a row against an account, and
causes a mail - and it answers identically whatever it is given, so it
cannot be used to read an account, change one, or learn whether one exists (decision
184). That is not a hole: every consequence of it requires the mailbox the mail
arrived in, which the caller either owns or has already compromised. But it is the
first route in this list that a stranger can point at somebody else's account and
make this server do work, and it is the strongest argument yet for the rate limiter
this entry has been asking for since 1b. The honest gap is the one decision 189 names
- the response is identical and the *latency* is not.

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

### The money endpoints, and the pending intent

Phase 2b spends decision 62. `HELD_OPERATIONS` had thirteen entries, every balance
change Phase 1b had refused to route, and its docstring said they were held "for a
different reason" - not a design question but a missing actor. The actor arrived in
2a, so 2b was supposed to be mechanical.

It was not, because routing a withdrawal forced a question the ledger had never had
to answer: **when a withdrawal is written down, has the money left?** The old code
said yes, unconditionally, and it was wrong.

**97. A deposit is still held, and the reason is not caution.** Deposits are the
one operation that stay behind, and the shape of the argument is worth stating
because "we did not get to it" is the wrong reason to keep something out. A deposit
is an *inbound* boundary crossing: money arrives from outside, and the only party
who can honestly say it arrived is the party that sent it - Paystack, in Phase 3,
proving itself with an HMAC signature. Exposed now, `POST /wallets/{id}/deposits`
would let any authenticated caller credit their own wallet for free. That is not a
gap a session token can close, because the caller *is* authorised and the request
is still a lie about where the money came from. Authorisation answers "who are
you"; this needs "who sent the money", and no token carries that.

The CLI keeps its `deposit` command. A developer's tool operating on their own
database is not a wire protocol, and nothing it does is reachable by anybody else -
which is the same distinction decision 91 drew for the commands that need no actor.

**98. There were always two boundaries, and 2a drew the wrong one.** Decision 62
put the line at *balance changes*: things that move money were held, things that do
not were routed. The pending intent is what revealed that this was the wrong
question, and the right one is **whether the movement has a far end outside this
system**:

| | crosses the edge? | settles | ledger |
|---|---|---|---|
| freeze, unfreeze, close | no | here, now | no row (a status change) |
| lock, release, extend | no — inside the wallet | here, now | SUCCESSFUL immediately |
| withdrawal, payout | **yes** | Phase 3's provider | **PENDING** |
| deposit (held) | yes | Phase 3's webhook | — |

So the rule is not "outbound is pending". It is: **a movement whose far end is
outside this system cannot be called successful by this system.** A lock is
finished the moment it happens - the money is in a pot, and nothing else needs to
agree. A payout is a claim about a bank account nobody here has contacted, and
writing SUCCESSFUL would be the ledger asserting a transfer that nothing performed.
That is the one thing this codebase refuses everywhere else, and it was being done
on the most consequential path it has.

**99. `WalletOperation.settles_immediately`, one flag on the base class.** The
three crossing operations (`WithdrawMoney`, `PayoutFromAvailable`,
`PayoutFromLocked`) set it `False`; everything else inherits `True`. The change in
`execute` is a branch where there used to be an unconditional call:

```python
if self.settles_immediately:
    transaction.mark_successful()
    self.transaction_repository.save(transaction)
```

Nothing else in the operation moved, and that restraint is the point. The row
written at step 3 was *already* correct - `TransactionStatus.PENDING` is the
constructor default, `completed_at` is absent, and "a pending transaction must not
have `completed_at`" is already enforced. The domain had anticipated this state
since before there was a caller for it; 2b is the first phase to reach it, not the
first to describe it. The `except MoneyError` path is untouched and still correct,
because `_apply`'s contract is to raise *before* mutating, so a rejected operation
leaves no hold to unwind.

**100. The wallet is debited when the intent is recorded, not when it settles.**
The alternative was to leave the balance alone until the provider confirms, and it
is wrong in a way that only shows up under a retry: the money is already spoken
for, so a second withdrawal against an undebited balance would be approved, and the
wallet would be overdrawn by the time both settled. Debiting at instruction time
makes the hold real and makes "can this be spent?" answerable from the balance
alone. The cost is that the money sits in a state with no name - gone from
`available_balance`, not in any pot, not in a transaction that has finished - and
decision 106 accepts that explicitly.

**101. A pending movement sends no receipt, and this was a required change rather
than a tidy-up.** `_run` calls `_announce` before the commit, and
`compose.wallet_movement` builds its prose in the past tense - *"Withdrew
{amount}"*, *"{amount} left the wallet."* - from `created_at=transaction.completed_at`.
On a PENDING row that is `None` and the sentence is a lie, at the same time. Two
failures, and the worse one is not the `None`: if `Notification` rejected it the
raise would land in `_run`'s `except BaseException` and **roll the withdrawal
back**, and if it did not, the user would get an email asserting a payment that has
not happened.

The fix is a guard in `_announce` that skips an operation whose transaction is
still PENDING. The receipt for money leaving is written when the money leaves,
which is Phase 3's settlement - and a real bank notification works the same way,
arriving on settlement rather than on instruction. Every existing sentence stays
true, so no wording changed and no new `NotificationKind` was added. The visible
consequence in this phase is that **a withdrawal is silent**: it holds funds and
says nothing, because nothing has happened yet.

`ExecutePlanRun` needed the same guard on its own receipts, and there it takes a
different form - it skips when not every row in the run is SUCCESSFUL, rather than
screening one transaction. The two are consistent in what they mean and neither is
derived from the other, which is worth knowing before somebody tries to unify them.

**102. `Wallet.close()` refuses a wallet that is not empty, and "empty" means empty
in full.** The available balance *and* every pot, including locked money in a pot
that has not matured. `locked_balance` already sums the pots, so the whole rule is
one expression.

The rule exists because close is the only status change that is not reversible:
every guarded operation refuses on a CLOSED wallet and no transition reopens one,
so money left inside would be unreachable for good. That asymmetry is the entire
argument, and it is worth separating from the convenience argument it resembles -
"you probably meant to empty it first" would not justify refusing a command.

The consequence is deliberate: **a wallet holding an active commitment cannot be
closed until that commitment is honoured or its plan is cancelled.** That is
coherent with the position this codebase takes everywhere else - the promise is the
product - and the alternative strands the money.

`freeze` does not block close, which surprises people because the two statuses feel
similar from a distance. Freezing is a reversible hold; closing is not. A frozen
wallet that is empty is still empty.

**103. The plan check lives in the application layer, and it is the first use case
that reads a second aggregate.** `Wallet.close` answers "is it empty?" from the
wallet alone. It cannot answer "is anything still promised from it?" - the domain
does not import planning, and it should not. So `WalletService.close_wallet` asks
both:

```python
wallet = self._wallet(uow, wallet_id)          # the one door, actor-scoped
self._refuse_close_if_committed(uow, wallet)   # uow.plans, same snapshot
wallet.close()
```

A closed wallet with a live plan would fail on every tick for the rest of that
plan's life, each failure recorded in a `plan_runs` row nobody reads. That is the
failure this prevents, and it is a failure the domain is structurally unable to
see.

Two smaller decisions are inside that snippet. The check runs **inside the unit of
work** rather than before it, so a plan created between the check and the close
cannot slip through - evaluated outside, the window is real and would close a
wallet with a live plan on it. And close is **not** routed through
`_change_status`, because that helper writes a ledger row and a rejected close must
not: a status change is not an operation, so there is no instruction to keep a
record of. Note the contrast with the money operations, which *do* write a FAILED
row when a wallet refuses them - that row is evidence about an instruction somebody
issued, and a refusal to close is evidence about nothing.

**104. PAUSED is not terminal.** `_refuse_close_if_committed` counts ACTIVE and
PAUSED, and the second is the one worth defending. A paused plan is waiting for a
human and can resume; a resume that found its wallet closed would be the same
broken tick, arriving later and more confusingly. The check names the two live
states rather than excluding the two dead ones, which is the version that stays
correct if a fifth `PlanStatus` is ever added - a new state is live until somebody
decides otherwise.

**105. `WalletAlreadyClosedError` and `WalletClosedError` become two names for two
situations, and until 2b they were one name for one.** `Wallet.freeze` raised
`WalletAlreadyClosedError` for a CLOSED wallet while `Wallet.unfreeze` raised
`WalletClosedError` for the same condition. Both graded 409, so nothing showed on
the wire, and it did not matter because CLOSED was unreachable dead code - a state
guarded in eleven places and never once set. 2b gives it a user, and the
inconsistency became a real question.

The answer: freezing a closed wallet is refused because *the wallet is closed*
(`WalletClosedError`); **closing** one is refused because *it was already closed*
(`WalletAlreadyClosedError`). The fact is "this wallet is closed" - the second name
belongs to a second close, not to a freeze. This is the same class of cleanup as
decision 96: a name that was harmless while it was unreachable, and wrong the
moment something reached it.

**106. A pending transaction has no exit, and that is accepted rather than
discovered.** It cannot become SUCCESSFUL and it cannot be refunded, because both
are triggered by a provider event that does not exist until Phase 3. In 2b a
withdrawal's money is held indefinitely.

This is the honest half of the change and it is why the work is split. The
alternative - recording SUCCESSFUL - is a ledger asserting a transfer nothing
performed. What must **not** happen is building the settlement path now, because
its only caller would be a webhook that does not exist: that is `WalletStatus.CLOSED`
again, a state guarded in eleven places and never once set. Phase 3's first item is
therefore the settle-and-refund path, including two gaps that are already visible
from here - `mark_failed()` does not credit the held funds back, and `reverse()`
refuses anything that is not SUCCESSFUL, so a failed pending payout needs a
transition that does not exist yet.

**107. The idempotency key is namespaced to the wallet it is spent from.**
`get_by_internal_reference` is a *global* lookup - a single `WHERE
internal_reference = ?` against a globally UNIQUE column, with no wallet in the
predicate - and `execute` returns whatever row it finds regardless of whose wallet
it belongs to. Unreachable while the key was a server-minted `uuid4`. Behind a
bearer token and a client-supplied key it is a cross-actor leak: actor B posts
`ref="x"` and receives actor A's transaction in the body.

```python
def _scoped_reference(self, wallet, internal_reference: str) -> str:
    return f"{wallet.wallet_id}:{internal_reference}"
```

Scoping to the **wallet** rather than to the actor, as a refinement of the obvious
answer: a wallet has exactly one owner, so a wallet-scoped key is also an
actor-scoped one - and it additionally closes the case where one user posts the
same `ref` to two of *their own* wallets and the second silently returns the
first's transaction with the second wallet untouched. That case is not a security
bug, and it is the same defect.

**108. The namespacing goes in `_run`, not in the route.** The route is where the
client-supplied key arrives, so putting it there is the obvious move and it is the
wrong one: the CLI's `--ref` carries the identical defect, and `_run` is the seam
both presentations already share. Fixing it at the API would have left `budget
withdraw --ref x` posting the same collision from a terminal - a bug that is
invisible rather than absent, because a CLI user attacking their own ledger is not
a threat model, but the *second wallet* case above is a plain correctness bug
either way. `ExecutePlanRun` is untouched: it builds its operations directly and
its references already embed the plan id.

Two consequences are written down rather than discovered. **Stored references
change shape** - rows written before this read `"<uuid>"` and `"plan:{id}:..."`, so
a retry of an old key no longer matches once. Accepted: that is the point of the
change and it is a developer database. And **the reference in a response is not the
key to retry with.** A client sees `"<wallet_uuid>:<their ref>"`, and sending that
back would produce a *new* transaction, which for a withdrawal is a double-spend.
`MovementIn`'s docstring says the key is the one the client generated, in bold,
because this is a footgun that only fires on a retry - exactly when somebody is
already having a bad time.

**109. `fund_name` without `source="locked"` is a 422, not a 400.** `PayoutIn`
carries a `model_validator` that refuses the combination. Leaving it to the domain
would be worse than late: the available-source payout has no use for a pot name at
all, so **nothing would refuse it** and the field would be silently ignored. A
caller who named a pot and was charged from the available balance instead would
have been told nothing. A self-contradicting request is the schema's business, and
this is the same shape as `CreatePlanIn._one_way_to_end`.

Its sibling: `source` is a `Literal["available", "locked"]` where
`InstructionIn.action` is a plain `str`. An action travels to the domain, which has
its own answer for a word it does not know; a source is what the *route* branches
on to pick a method, so an unknown one has no owner left to refuse it and would
fall through to doing nothing.

**110. The boundary list moved rather than shrank.** `EXPECTED_OPERATIONS` goes 18
→ 26, `HELD_OPERATIONS` 13 → 2. The three operations that will *never* be routed
moved into a list of their own, `NEVER_ROUTED_OPERATIONS`, because "not yet" and
"not ever" are different promises and a shared list made them look alike.

`/tick` and `/notifications/deliver` are installation-wide background jobs with no
actor - they run from cron over every plan in the database, and they take nobody
because there is nobody they could be. An endpoint that ran them would have to be
told an actor, and the only actor that could mean anything is "whoever holds the
token", which would make the scheduler reachable by anyone who could reach the
port. Sessions did not change that; they are what makes it answerable.

`POST /plans/{id}/run` is the same shape of answer for a different reason. It is
not actorless - decision 56 gave the scheduler no privilege by minting one
`ExecutePlanRun` per plan acting as that plan's user. It is simply not a request
anybody makes: a plan fires when a clock says so, and letting a client name the
moment would let it pay itself early, run an occurrence twice, or drive a schedule
the user set up and forgot about.

**111. The API's money tests fund wallets through the use case, and that is not a
shortcut.** There is no deposit endpoint in this phase - decision 97 - so a test
file that could only use HTTP would have no way to put money in a wallet, and every
money route would be untestable. The `funded` fixture opens a wallet over the wire
and then credits it through `build_wallet_service` against the same database file,
and it asks `GET /users/me` for the actor's id rather than reaching into the
sessions table.

This is the same seam `tests/conftest.signed_in` uses for accounts, and for the
same reason: the precondition is built through the layer that can build it, and
everything the test is actually about happens over HTTP. The alternative - testing
`WalletService.deposit` and calling the routes untested - would leave the eight new
routes, which are the whole of this phase, covered by nothing.

One consequence is worth naming. Every close test in that file closes a wallet that
was **never funded**, because there is no way to drain one over HTTP: a withdrawal
holds the money rather than removing it, which is decision 100 seen from the test's
side. The reachable empty state in this phase is an unfunded wallet.

**112. A confirmation is not an intent, and the vocabulary split is the first
decision because two opposite states were about to share a word.** Decision 100
already calls the PENDING *transaction* "the pending intent" - a movement that has
happened, whose wallet is already debited, and which cannot yet be called finished.
The record this phase adds is the opposite: a request nothing has been done about.

> A **confirmation** is a request nothing has been done about.
> A **pending transaction** is a movement that has been done and cannot yet be
> called finished.

So the new aggregate is a `Confirmation` with statuses `AWAITING` / `CONFIRMED` /
`EXPIRED`, and the two status vocabularies never touch: `PENDING` is never a
confirmation status and `AWAITING` is never a transaction status. That is why
`TransactionStatus.PENDING` needed no change at all, and why nothing in 2b's
settlement story moved. The failure the split prevents is specific and would have
been silent: a reader who took "intent" to mean one thing when the code meant the
other would conclude that money had already moved.

**113. Three operations are confirmed and three are not, and the line is "money
leaving the wallet".** `withdrawal`, `payout` and `close` record a request;
`lock`, `release` and `extend` stay one step. The three that are *not* confirmed
are not a lesser version of the three that are - they are a different kind of
thing: all three are reversible and stay inside the wallet, so a mis-tap is undone
by typing the opposite command.

A prompt in front of them would not be free. It would teach people to answer
prompts without reading them, and that habit is what the three commands that
*do* need a prompt would pay for. `close` is grouped with the money even though it
moves nothing, because it is the one transition in this API with no way back: the
emptiness rule protects the money from being stranded, and nothing protects an
owner from closing the wrong wallet.

The scheduler is out of scope by construction rather than by exception -
`ExecutePlanRun` builds its operations directly and never calls `WalletService`, so
a plan that fires at 3am still confirms nothing. That is decision 17 holding: the
30-minute pre-payout notification is a courtesy, never a gate.

**114. The four money-out methods are private, and that is the no-bypass
mechanism. This is a deviation from the approved plan and the reason is worth
recording.** The plan made `confirmation` a *required parameter* on the public
`withdraw` / `payout_from_available` / `payout_from_locked` / `close_wallet` -
"there is then no spelling of `withdraw` that moves money without naming the
confirmation it was authorised by". Three holes showed up on implementation:

1. `Confirmation` is a public dataclass, so a caller can *fabricate* one naming a
   `confirmation_id` that exists in no table. A required parameter proves an
   argument was passed; it does not prove the argument is real.
2. A legitimate confirmation whose `amount` differs from the `amount` passed
   alongside it authorises the wrong movement. Two records of one fact, with a
   must-agree rule that nothing enforces.
3. Read-then-check-then-write on `status` has a gap, and two concurrent confirms
   arriving in it would both see `AWAITING` and both move the money.

So the four methods became **private** and `confirm()` is the only door. They take
`(confirmation, as_of)` and read **every argument off the record** - the amount,
the destination, the pot, all of it - which closes (1) and (2) at once: there is no
second value left to disagree with. (3) is closed by decision 115.

The precedent is `open_wallet`'s docstring, which solved the same problem the same
way: *there is no longer a spelling that opens one for anybody else.* Deleting the
spelling beats guarding it, because a guard is a thing somebody can forget to call
and a missing method is a compile error. Verified rather than assumed: `plan tick`
does not go through `WalletService`, so nothing in the scheduler could break.

**115. The spend is one statement, and it is the same idiom as `enqueue`.** The
plan gave `Confirmation` a `consume(as_of)` method - check the status in Python,
then write it. That was dropped in favour of
`ConfirmationRepository.claim(confirmation_id, user_id, kind, as_of)`, which is a
single `UPDATE ... WHERE status = AWAITING AND expires_at > ?` whose `rowcount`
*is* the answer. The second deviation from the plan, and the same reasoning one
table over as `NotificationRepository.enqueue`: the write is the decision, so
there is no interval in which two callers can both be right.

`kind` is a parameter rather than something the method reads, so the claim states
which operation it is authorising - a payout's confirmation cannot authorise a
withdrawal because the `UPDATE` matches no row. That refusal is reported as
`ConfirmationNotFoundError`, and the choice is deliberate: the fall-through read
would otherwise call a wrong-kind request *expired*, which sends a caller to look
at a clock when what is wrong is that they asked the wrong question.

**116. An attempt spends the request, and `close` is the exception that proves the
rule.** A confirm against a frozen wallet, or for more than the balance, writes a
FAILED ledger row *and* spends the request, in one transaction. That looks harsh
and it is the safe direction, for a concrete reason.

`WalletOperation.execute:105` deduplicates on `get_by_internal_reference`, a
**global lookup that returns the row whatever its status, including FAILED**. If a
refused attempt left the request `AWAITING`, the retry would re-enter under the
same spent reference, be handed the old FAILED row back, move nothing and report
success - and `_announce` would compose a receipt for it, in the past tense, for a
payment that did not happen.

So the rule is not "an attempt spends it". It is: **the spend commits whenever the
refusal is recorded.** `close` writes no ledger row when it refuses, so its whole
unit rolls back - the claim included - and the request stays answerable. That is
also the right answer for a person: the refusal says "move your money out first",
and emptying the wallet and answering again is exactly what they are going to do
next.

This asymmetry is what the two error-handling branches in `WalletService` encode:
`_run`'s `except MoneyError: uow.commit()` against `_close_wallet`'s
`except BaseException: uow.rollback()`.

**117. The reference chain: one reference → at most one confirmation → at most one
attempt.** `UNIQUE (wallet_id, internal_reference)` on `confirmations` is
load-bearing rather than tidy, and this is what it buys. A spent reference can
never be read back as an outcome (116), because producing a second attempt under
one reference is impossible: the first attempt already has that reference, and the
confirmation that would authorise a second is the one that is already spent.

**118. `ref` is promoted rather than changed.** It used to key the transaction; it
now keys the request that will produce one. The transaction underneath still
carries `wallet_id:ref` through `_scoped_reference`, so decision 107's namespacing
is untouched, and `MovementIn`'s docstring says what a client should now send. The
one visible consequence: a *withdrawal now takes a `ref`*, where before it
accepted one and had nowhere to put it.

**119. Expiry is checked, not swept, and `EXPIRED` is derived rather than stored.**
`Confirmation.status_as_of(as_of)` returns `EXPIRED` for a stale `AWAITING` row;
nothing ever writes it. Storing it would make a `GET` a write - the same objection
decision 49 makes to re-stamping a session on every request - and it would be a
second record of a fact `expires_at` already holds, free to disagree with it. Here
the disagreement would be worse than redundant: a row saying `EXPIRED` with a
future `expires_at` is a request refused for no reason.

No sweeper deletes abandoned rows, and this is a place where the plan's own option
text ("an expiry policy, and a sweeper") was deliberately not implemented. Three
reasons: the only clocks in this system are the scheduler's and the command's own,
and sweeping on every money command would make a withdrawal write rows it was never
asked to write; deleting destroys the only record that somebody asked for something
and abandoned it; and it is the shape `sessions` already has, where expired rows
accumulate too. A `CONFIRMED` request stays `CONFIRMED` for ever - expiry is about
whether a request may still be *answered*, and one that was answered is not
un-answered by the clock.

**The window is re-checked on the way in from the store, and that turns up in the
tests.** `Confirmation.__post_init__` refuses a row whose `expires_at` is not
strictly after its `created_at` - a request born dead - and the repository builds
one of these on every load, so a hand-edited row that no code could have produced
fails loudly at read time rather than quietly authorising nothing. That is the same
argument the aggregate's field-pairing makes, applied to a different invariant.

The cost is this: a test cannot age a request by writing a past `expires_at` on its
own. The window runs fifteen minutes from the moment the request was made, so every
past expiry is *also* before its creation, the row is refused at load, and what
comes back is a 400 about the window where the test asked for a 409 about expiry.
`test_money.expire` moves both moments for exactly this reason - found by running
the suite, and worth writing down because the failure names the wrong thing.

**120. The request does not check the balance, and the absence is the design.**
A check at request time cannot be relied on - the balance at *confirm* time is the
one that decides - so it would be a second, weaker copy of the wallet's own rule,
free to disagree with it. Its absence is also a feature: a request made against an
empty wallet is answerable once the wallet has been topped up, which is exactly what
somebody who is about to be paid would want. The refusal is not lost; it arrives one
call later, from the wallet, in the wallet's own words.

**121. On the wire, asking and answering are separate and the confirm route takes no
body.** The three money routes keep their URLs, bodies and 201s; their response body
becomes a `ConfirmationOut`. `POST /confirmations/{id}/confirm` is the only route
that spends what one of them recorded, and it carries nothing but the id - because
everything about the movement is already on the request, and a body would be a
second place to say what the movement is.

A reused `ref` answers **200 with the existing request, 201 when it created one** -
three lines on an injected `Response`, and it is how a client that retried a lost
response can tell "here is your request again" from "here is a new one". The two
bodies are identical on purpose: it *is* the same request.

`GET /confirmations/{id}` is exposed rather than held because a request that cannot
be read back is one a client cannot debug, and it costs no write to give. Both
routes grade through the existing `errors.py`: `ConfirmationNotFoundError` → 404,
`ConfirmationExpiredError` and `ConfirmationAlreadyUsedError` → 409. The two 409s
are the plainest members of that list - the resource is exactly what the path names,
it is there, and it is its state that refuses.

**A confirmation 404 does not name the id it was looking for.** `ConfirmationNotFoundError`
is raised bare - like `WalletNotFoundError`, and for the same reason - so
`errors._detail` renders the class name and the two answers are byte-identical:
"this request is not yours" and "this request never was". A message interpolating
the id would have made them distinguishable by body, which is a leak of exactly one
bit - but the whole authorisation for answering a request is *knowing its id*, so an
oracle here would be worth more than an oracle anywhere else in the API. The test
compares both responses whole, against a real request of Alice's and a UUID that
names nothing; whole-body equality is the only shape of test that can see this,
since both answers are a 404 either way.

**122. `--yes` is a statement about the room, not about the rules.** The CLI prints
a preview and asks; `--yes` skips the question. It does not skip the confirmation -
the request is recorded and spent either way, server-side, and a test asserts that
the two paths leave identical rows behind. What the flag declares is that there is
nobody at the terminal to ask, which is a true statement about a script and a false
one about a person.

**An unanswered prompt is not a yes.** `EOFError` and an empty line are both
refusals, and only `y` (case-insensitively) is agreement - not "anything that is not
n". The two mistakes are not symmetric: a mistyped `y` costs a second run of the
command, a mistyped `n` read as agreement costs money that cannot be called back.

Found while writing the tests rather than by reasoning: `OSError` had to be caught
alongside `EOFError`. A closed file descriptor raises one where an exhausted pipe
raises the other, and from the prompt's point of view the two are one fact - nobody
is there to ask. pytest replaces stdin with an object whose `readline` raises
`OSError`, so the first CLI test to reach the prompt without `--yes` failed with a
traceback instead of the refusal the docstring promised.

A refusal exits **0**, not 1. Nothing failed; a person declined. A non-zero exit
would tell a script something went wrong when the system did exactly what it was
told, and a script that read it as failure would retry a command the user had just
refused.

**123. What this does not protect against, in plain words: a stolen session token
confirms as easily as it requests.** This is an accident guard, not a security
control. It makes a person pause; it does not make them prove who they are. Whoever
holds the token can record a request and answer it, and the fifteen-minute window
does not change that - it bounds how long a *recorded* request stays live, which is
a different property.

The real answers are elsewhere and both are already on the roadmap: session lifetime
and rate limiting (2c), and Phase 3's provider, which is the only thing that can
actually settle a payout. Written here rather than left implicit because a
confirmation that read as a security control would be trusted for something it does
not do - and the failure would be discovered by somebody who had relied on it.

### The webhook, and money that arrives from outside

Phase 3's first slice, and the one that cashes decision 97's own sentence: *"the
only party who can honestly say it arrived is the party that sent it - Paystack, in
Phase 3, proving itself with an HMAC signature."* Two gaps close together, because
they were never two gaps. Deposits were held (97) and a pending transaction had no
exit (106), and the single thing that closes both is an **inbound event from the
party that actually moved the money**.

```
POST /wallets/{id}/deposits    -> 201  a PENDING row + a provider_reference
                                       **the wallet is not credited**
POST /webhooks/paystack        -> 200  raw body + x-paystack-signature
                                       the only route that can settle anything
```

Four events are handled, and the middle two are the ones worth reading twice:

| event | row | wallet |
|---|---|---|
| `charge.success` | deposit PENDING → SUCCESSFUL | **credit** the arrived amount |
| `transfer.success` | payout PENDING → SUCCESSFUL | nothing (already debited) |
| `transfer.failed` | payout PENDING → FAILED | **credit the hold back** |
| `transfer.reversed` | payout SUCCESSFUL → REVERSED | **credit the money back** |

**124. A webhook is not an actor, so settlement is not a method on
`WalletService`.** `dependencies.current_actor` is the only function in the
codebase that can produce a `User`, and it requires a token. A provider event
carries none - it carries a *signature*, which is a different kind of authority: not
"who are you" but "who sent this". The answer to a second kind of authority is a
second, narrowly named thing, `SettlePayment`, whose only input is a
`ProviderOutcome`. It is reachable from exactly one route.

This is the zero-bypass rule holding rather than being waived, and the distinction
is worth stating plainly because "the webhook needs a wallet it does not own" is
exactly the pressure that produces a privileged actor. There is no system user here,
no `skip_ownership=True` flag, and no "internal read" path. The use case derives the
owner from the ledger row it is settling - through the narrow read below - and then
loads the wallet through `get_owned`, the same owner-scoped door every other money
path uses. A webhook gets no privilege; it gets a different question answered.

**125. `WalletRepository.owner_of(wallet_id)` is the narrowest read that answers the
question, and a second caller is a hole if it is user-reachable.** It returns a
`UUID` - one column of one row - and never a `Wallet`: no balance, no status, no
pots. That is what distinguishes it from the `get_by_id` decision 54 deleted, which
returned the money. The honest comparison is `list_by_status`, which decision 57
already accepts as a convention-enforced privileged read, and the same warning
applies verbatim: today there is one caller and it is not user-reachable.

It is called from `SettlePayment` and nowhere else, and the reason it cannot simply
be folded into the transaction lookup is that a transaction carries a `wallet_id`
and not a `user_id` - so the store has to be asked, once, and the answer is an id.

**126. The body stays bytes, so the endpoint stays `def` and gains one async
dependency.** `app.py`'s rule is that nothing in this presentation is `async` and
nothing is decorated to look it, because the blocking SQLite work belongs in the
threadpool. FastAPI cannot `await` inside a `def` endpoint, and a signature must be
computed over the bytes that *arrived* - verifying one against a re-serialized
Pydantic model verifies a different byte string, and a difference of one space
presents as a wrong secret key.

So the raw body comes from an **async dependency** (`raw_body`) and the endpoint
stays a plain `def`. The rule survives intact: one line in one file, not a
precedent, and the alternative - an `async def` endpoint - would have put the
settlement query on the event loop, on the endpoint a provider will hammer. Both the
rule and the exception are written down, because the next person to read `app.py`
will otherwise believe the rule.

**127. An unconfigured install refuses every webhook with a 503, and never a 401.**
With no `PAYSTACK_SECRET_KEY` there is nothing to verify against, and "no key" must
never mean "accept" - but *unauthenticated* is the wrong name for it, because it
blames the provider for the installation's state. Paystack's signature may be
perfectly good. A 503 says the server cannot serve this request right now, which is
what we want a provider to hear: the operator who forgot to set the key will set it,
and the events must then land. A 401 tells Paystack to stop, and the money taken
during that window would be settled by nothing.

This is `EmailSettings`' missing-mail state reaching a place where the safe default
is the **opposite** one. Mail's `None` means "say nothing"; this one's means "accept
nothing" - the same value doing opposite jobs, which is why the check lives in one
dependency (`payment_provider`) rather than per route.

**128. Idempotency is the row's own status, so no `provider_events` table is
needed.** A second `charge.success` finds a row that is no longer PENDING and stops.
That is sufficient only because the credit and the status change are written in
**one** unit of work: there is no instant at which the money has moved and the row
still says PENDING, so a retry cannot arrive in a window where it would apply twice.
This is the same argument the confirmation makes for spending a request and moving
money together.

It is also, deliberately, *not* what reconciliation will be. A replay of received
events depends on having received them, and not receiving them is the failure mode
worth catching - so the reconciler polls the provider for PENDING rows instead, which
is the more robust direction. That is why the table is not merely deferred: it would
be the wrong shape.

**129. An amount that disagrees settles nothing, and the row stays PENDING.** The
PENDING row records what was *asked for*; the event carries what *arrived*. If they
differ it is a bug or tampering, never a partial payment - Paystack's checkout does
not do those. So the event is acknowledged with a 200, nothing moves, and the
reconciler is what picks it up. Crediting the arrived amount instead would make the
ledger row's own amount a lie; refusing loudly would make the provider retry
something that can never succeed.

**130. `release_hold` is not `apply_deposit`, and the whole difference is a missing
status check.** It credits the available balance, applies the same currency and
positivity checks - and **deliberately checks no status**, because the money was
always the owner's and refusing to return it is exactly how a hold becomes a hole.

The concrete shape, because it is easy to lose: a pending payout's money is in
neither `available_balance` nor a pot, so `close()` sees an empty wallet and
*succeeds*. A wallet can therefore be CLOSED with a payout still in flight, and when
the provider reports that payout failed, `apply_deposit`'s CLOSED guard would refuse
the money and strand it forever. `apply_deposit` keeps that guard, which is right for
money arriving from outside into a wallet nobody can use. Two methods, two rules, and
the difference is the direction the money came from.

**131. The provider's idempotency key is our scoped internal reference.** The
initiation call passes `_scoped_reference(wallet, ref)` - decision 107's
`"<wallet_id>:<ref>"` - as Paystack's own `reference`, so Paystack refuses a
duplicate at *its* end as well as ours. That closes the window the local dedupe
cannot: two concurrent requests under one client key would otherwise both reach the
provider and each open a collection.

A repeated key is therefore a **409, not a friendly replay of the first
`authorization_url`**. Returning the original looks kinder and is worse: nothing
stores it, and a checkout URL is single-use, so the client would be handed a page the
provider refuses to take money on - and would believe a deposit was live. The
`reference` column and the `provider_reference` column stay separate even though they
hold the same string today, because a provider that minted its own would need a place
to say so, and discovering that later is a migration.

**132. The provider is called between two units of work, not inside one.**
`InitiateDeposit` opens a read-only unit that validates and closes it, calls the
provider, then opens a second unit that writes the PENDING row. Holding one SQLite
write transaction open across a third party's response time would block every other
writer on this database for as long as that party felt like taking - a self-inflicted
outage proportional to an outage elsewhere.

The cost of splitting is a window in which the call can fail after the read and
before the write, and the honest outcome of that is **no row**: a deposit nothing was
asked for is not a deposit, and a row written optimistically would be a PENDING
deposit no webhook will ever settle. The window the split *does* open - two requests
under one key both reaching the provider - is closed by 131 at the far end, not by
luck.

**133. The deposit route is a new use case rather than a `WalletOperation`.**
`WalletOperation.execute` writes the ledger row at step 3 and calls `_apply` at step
4, which for a withdrawal debits the wallet. A provider-backed deposit must record
*without* crediting, so this is the template's **mirror** rather than a variant of
it, and forcing it through would mean a second flag beside `settles_immediately`
whose two settings mean opposite things - "debited, row pending" against "not
debited, row pending". One flag cannot say both without becoming a word that means
nothing.

A CLOSED wallet is refused in `_prepare`, *before* the provider is called. That
ordering is not performance: a collection opened against a wallet nothing can credit
would take a real person's money and have nowhere to put it, so the payer is never
sent anywhere. A **FROZEN** wallet is allowed, deliberately - freezing stops value
leaving, and this is value arriving.

**134. Every answer a webhook can give is a value, and the vocabulary is ten
words.** `SettlementOutcome` holds the four movements and six refusals in one enum.
A webhook answering 5xx makes the provider retry forever, so "unknown reference",
"already settled", "amount disagrees" and "wrong kind" are answers rather than
errors - and they are four separate members rather than one `IGNORED` because they
call for different responses from whoever reads the log.

Two of the ten were not in the plan. **`WALLET_CLOSED`** is a hole the plan did not
cover: money that arrived for a wallet closed between the payer opening the link and
the payer paying it. `apply_deposit` would raise, and raising is a 500, and a 500 is
a retry of something that will never apply - so it is a refusal with a name of its
own, and it is the one of the six that needs a person. **`EVENT_IGNORED`** is the
member no use case produces: an event understood well enough to know this deployment
does not act on it. Keeping it out of the enum would have given the response body two
vocabularies with a boundary nobody could see.

A third thing was found while writing the tests rather than by reasoning, and it was
a genuine hole: **`Currency` is a plain enum**, so an unknown currency name raises a
built-in `ValueError` rather than a `MoneyError` - and it escaped `_outcome_from`'s
handler as a 500, on the one endpoint where a 5xx makes a provider retry forever.
The catch was widened to `(MoneyError, ValueError)` and `ProviderOutcome`'s own
construction moved inside it, so a zero amount takes the same 400.

**135. A settlement that did not move money sends no receipt.** `_announce` is
decision 101 being kept - `WalletService._announce` skips a PENDING row and its
docstring promised "the receipt arrives in the phase that settles these movements" -
but the guard is repeated here, because `compose.wallet_movement` would happily write
*"5000.00 NGN was paid to Chinedu Okafor"* for a FAILED row. A failed transfer did
not pay anybody.

A **reversal** is silent for the sharper version of that reason: the money *was* paid
and then came back, so what the receipt would say is true and no longer the whole
truth - which is worse than saying nothing.

This makes the receipt guards **three**, not two, and that is the count at which the
existing open item stops being tolerable. See `### Still open`.

**136. A deposit is not confirmed.** Decision 113 drew the line at "money leaving
only", and this is that line holding rather than an exception to it: a prompt before
*receiving* money would be a prompt whose only possible answer is yes. The
confirmation feature's whole justification - irreversible, outward-facing, worth a
pause - does not apply to a movement whose worst case is that the owner is richer
than they were.

Note what this does *not* mean. A deposit is still not a `WalletOperation` (133), and
it is still a two-step thing on the wire. What it is not is a *third* step.

**137. A wrong secret key is the caller's sentence to write, not `_post`'s.** Every
other refusal from the provider is reported where the status code is the whole of
what is knowable - "the payment provider refused the call with 500" is a complete
sentence. A `401` is not: it means *this installation's key is wrong*, and the
useful answer is endpoint-shaped, because what a rejected key means depends on what
the call was for. Only the caller knows that.

This was a bug rather than a preference, and both halves had been written: the
`missing data` branch in `initialize_deposit` exists precisely to name the secret
key, its comment says a wrong key is "the common case by a wide margin" - and it was
**unreachable**, because `_post` raised on any non-2xx before the branch could run.
The statuses in `AUTHENTICATION_FAILURES` (`401`, `403`) are now passed through to
be interpreted instead. A wrong key that answers with something other than JSON
still leaves as a `PaymentProviderError`, which is a less specific answer rather
than a crash.

The lesson generalises past this adapter: a shared helper that converts failures
into one shape can *swallow the case its caller was written to handle*, and the
symptom is a less useful error rather than a wrong one - so nothing fails, and
nothing points at it either.

**138. The reconciler settles through `SettlePayment` and adds no rule of its own.**
No new transition, no second settlement path, no domain change at all. Everything
hard about settling a payment was built in 3a; 3b adds a second *caller*, and the
whole of `ReconcilePayments` is deciding which rows to ask about and what to do
with an answer.

The proof is an identity rather than a claim, and it is the strongest assertion
this slice makes: a deposit whose webhook was lost, recovered by this job, ends
with the same balance, the same row status and the same queued receipt as one whose
webhook arrived. That holds because the settler is *injected* - built once at the
composition root, from the same factory the reconciler is (decision 149) - so a
recovered payment reaching a different settlement path is not a bug that could be
introduced quietly; it is a wiring change somebody would have to make on purpose.

**139. `outcome_for(reference)` is the port's third method, and the first one that
asks.** The previous two *tell* the provider to do something - open a collection,
and (locally) prove a signature's author. This one asks it a question about
something that already happened, and that is a new kind of method on this port
rather than a third instance of the old kind.

The port's docstring already argued that "the inbound half of settlement is
deliberately not here at all", and this method does not contradict it: that sentence
is about the **webhook** path, where deciding what a `charge.success` means
genuinely needs no call outward. The reconciler asks a different question, and the
docstring's own definition of a port ("A port is what we say; that is what we
hear") covers it - we say *what happened to this?*. The paragraph was amended to
read "not on the webhook path" rather than "not here".

**140. It is not called `verify`.** Paystack's name for the endpoint is
`GET /transaction/verify/:reference`, and `verify_signature` already owns that word
on this port for proving a byte string's author. Two methods on one port whose names
both begin with "verify" and mean unrelated things is a hazard with no upside, so
the vocabulary rule `ProviderEvent` already established applies: our names, not the
wire's, with the adapter owning the translation. `outcome_for` says what it does -
it asks for an outcome.

**141. Three answers, so the answer is a value and not `ProviderOutcome | None`.**
`ProviderAnswer(status, outcome)`, with `SETTLED`, `NOT_SETTLED` and
`NO_SUCH_REFERENCE`, and `outcome` present *exactly* when the status is `SETTLED` -
enforced in `__post_init__` in both directions, because both are ways to write a
record that cannot mean anything.

The third state is the one a nullable outcome cannot express and the one that
matters most: **"the provider has never heard of this reference"** is unreachable in
a healthy installation - `InitiateDeposit` writes the row only after the provider
accepted the call (decision 132) - which is exactly what makes it the loudest signal
a run can produce. This deployment's rows and this key's transactions do not match,
and somebody needs to know.

`NO_SUCH_REFERENCE` is deliberately *not* named `UNKNOWN_REFERENCE`, which is
already a `SettlementOutcome` and means the opposite end of the wire: "no ledger row
is filed under this reference" is a webhook about a payment this deployment never
made, while "the provider has no transaction under it" is a lookup about a payment
it did. One is a phantom event and the other is a misconfigured key, and a shared
name would make the log lie about which happened.

The reconciler's own vocabulary is then **four** words rather than three, because
"asking failed" is not an answer the provider gave: `SETTLED`, `NOT_SETTLED`,
`NO_SUCH_REFERENCE`, `FAILED`. Keeping `FAILED` in the same enum rather than raising
is `SettlementOutcome`'s decision about its refusals, for the same reason - an
operator reading one line per row should not have to look somewhere else for the
rows that went wrong, and a cron job has nowhere to raise to.

**142. Nothing is ever resolved by guessing: the reconciler reports, and never fails
a row.** Both non-settled answers leave the row PENDING and move nothing, including
a checkout Paystack reports as *abandoned*. An abandoned checkout will very probably
never be paid, so marking that row FAILED would be honest about the deposit - and
dangerous about the money, because `SettlePayment` refuses a `CHARGE_SUCCEEDED` on a
row that is not PENDING (decision 129's ordering, one branch over). A row failed a
minute before its payment arrived would take the payer's money and never credit it,
and there would be nothing left in the system capable of noticing, because the row
is no longer in flight and the reconciler only ever asks about rows that are.

So the price of the policy is a row that can sit PENDING indefinitely, asked about
on every run until somebody looks - a small, visible, recoverable cost. The
alternative is FAILED, which is unrecoverable in exactly one direction and silent in
that direction. That is why sweeping abandoned charges is an open item with an entry
of its own rather than a branch here: it needs a fifth `ProviderEvent` and an answer
to "what does it mean to fail a charge that might still arrive", and neither exists
yet.

**143. The grace window is a courtesy, not the guard.** Fifteen minutes,
`DEFAULT_GRACE`, and nothing about correctness depends on it: the guard is that only
a settled answer makes anything happen, so a payer still on the checkout page is
protected by the answer they get (`NOT_SETTLED`) rather than by the delay. No delay
could make asking unsafe.

What the window buys is not spending provider calls on payments that are seconds old
and almost certainly fine - a webhook is normally what settles a deposit, and this
job exists for the times it is not. Fifteen minutes is *chosen* rather than derived:
long enough that a live checkout is never asked about, short enough that a lost
webhook is noticed inside a coffee break. A deployment that disagrees changes one
number, `--as-of` makes the window exercisable without waiting for it, and the
command says so in its help - the same reasoning that put `--as-of` on the tick.

The boundary is inclusive - a row exactly fifteen minutes old is asked about - which
mirrors `Confirmation.is_expired` treating its own edge the same way. The instant a
window closes is the instant it stops being open, and the two must agree with each
other rather than each being individually defensible.

**144. `list_awaiting_provider()` is the fourth unscoped read in this codebase, and
it is discovery rather than access.** It takes no argument, so it cannot be pointed
at another status or another wallet, and it returns whole rows oldest-first. The
filter is `status = PENDING AND provider_reference IS NOT NULL`, and **both halves
are load-bearing**: the status alone would hand back every CLI withdrawal and every
plan-run payout, which no provider has ever heard of, and the reference alone would
hand back settled deposits. Only `InitiateDeposit` writes a `provider_reference`, so
the second half is what makes the candidate set exactly "payments a provider was
actually told about".

The defence is the one decision 57 and decision 125 already give, restated because
it is the whole argument: it is *discovery*, not access. The reconciler reads
nothing off these rows but the reference; a row names its wallet and the reconciler
does not follow it; and the only wallet read that follows is `SettlePayment`'s,
through `owner_of` and then `get_owned` - the same owner-scoped door every other
money path uses. Nothing user-facing can reach it, which is `PlanService`'s rule
applied to the wallet side. The `list_by_status` entry under `### Still open` asked
to be re-read when a fourth unscoped read appeared; this is that re-reading, and the
answer is that the convention still holds and is still only a convention.

**145. The discovery unit closes before the first call goes out, and the policy lives
in the use case.** The read happens inside a unit rolled back in a `finally`, which
is `_due_plans`' arrangement (decision 56) - and it matters more here than there. The
scheduler follows its read with local work; this method is followed by up to fifty
network calls, and a unit left open holds a SQLite read transaction for the whole of
them - the database locked against its own writers for as long as a third party felt
like taking to answer, multiplied by the size of the batch. Closing the read first is
what makes that impossible rather than merely unlikely.

The grace filter and the batch limit are then applied in Python, after the read,
which is `_due_plans`' argument too: neither the age of a payment nor the size of a
batch is a fact about storage, and materialising either as a column would create a
second source of truth for a policy the use case already owns. It is also what makes
both testable without a clock and without a database.

**146. A status that is an answer: `_request`'s 404 stops being a refusal.** This is
decision 137 recurring one method over in the same file, and it is worth naming as a
recurrence rather than as a second discovery. 137's rule is right for a call that
*creates* something - a refusal is the whole of what is knowable - and wrong for a
call that *asks about* something, where a status is often the answer itself. `404`
from `/transaction/initialize` is a broken deployment; `404` from
`/transaction/verify/:reference` is Paystack saying it has never heard of the
reference.

So the statuses a caller will interpret became a parameter: each call names the ones
that are *answers* to it, and the transport hands the status back alongside the body
instead of raising. `initialize_deposit` names `AUTHENTICATION_FAILURES` and keeps
its present behaviour exactly; `outcome_for` names `LOOKUP_FAILURES`, which is that
set plus `NO_SUCH_REFERENCE_FAILURES`. A wrong key answering `401` on a lookup still
leaves as a `PaymentProviderError` naming the secret key, for 137's reason: only the
caller knows what the call was for.

The generalisable part is the shape of the mistake, and it is now written down
twice. A shared helper that converts failures into one shape can swallow the case
its caller was written to handle, and the symptom is a *less useful* error rather
than a wrong one - so nothing fails, and nothing points at it either.

**147. One run is bounded by a count, and each row settles in its own unit.**
`RunDuePlans` bounds a tick by running one occurrence per plan; this job's natural
bound is a count, because its cost is one network call per row rather than one
execution per row. A provider outage that leaves five thousand rows PENDING must not
make the next run five thousand calls long - it clears a batch of fifty, reports what
it left behind, and the next run continues from there. Fifty is chosen so that an
ordinary run finishes in seconds and a backlog clears inside a few hours at an hourly
cadence.

The batch takes from the *front* of a read that is oldest-first, which is what stops
a row being starved: a backlog always longer than the batch still drains from its
oldest end, so the payment that has been stuck longest is always the one asked about
next.

The transaction boundary follows the row rather than the batch, which here costs
nothing because `settle` already opens its own. What it buys is the property
`RunDuePlans` gets the same way: a run that dies on row nine leaves the first eight
credited for good.

**148. A third party failing is per-row; our own data being wrong is not.** An ask
that fails - the provider unreachable, a response that makes no sense - is recorded
against that row and the run continues, because a provider being briefly unavailable
about one reference says nothing about the next, and a run that stopped at the first
hiccup would leave every row behind it unreconciled. That is decision 24's rule
applied to a job whose whole purpose is noticing things, and it is the same "the next
tick is the retry" rule the drains follow.

A `settle` that *raises* is deliberately not caught, and the asymmetry is the point.
The only way it can raise is a ledger row naming a wallet that does not exist - an
integrity problem rather than a transient one - and reporting that as one row's bad
luck would be exactly the silence this feature exists to prevent. It also cannot
happen: the row was discovered through a provider reference, a reference is written
only by `InitiateDeposit` after a collection was opened, and `transactions.wallet_id`
is a foreign key. A raise here means the database itself is broken, and failing
loudly on that is the right answer.

**149. `build_reconciler` is the first builder that names both settings objects.**
`build_settler`'s docstring says a builder naming both mail and payments "would blur
two providers that share nothing but a wallet", and this one names both because the
reconciler *is* both: it asks Paystack a question and it queues the SMTP receipt that
the answer earns. A builder with a narrower signature would not be narrower, it would
be a lie - the receipts this job produces would have to be composed with a recipient
some other function decided. The rule is amended rather than broken, and it is the
honest measure of what makes this job different from every other actorless one.

It builds the settler itself, from the **same factory** it hands the reconciler. That
is a correctness requirement in `build_scheduler`'s sense: each row settles in its own
transaction, so the two use cases do not share one - but they must share a *database*,
and the failure mode of getting that wrong is silent. The reconciler would ask the
provider about rows in one file and credit wallets in another, reporting recovered
payments that never happened. One factory for both is how that becomes impossible
rather than merely unlikely, and it is the line that makes "recovered and delivered
are indistinguishable" true by wiring rather than by claim.

**150. The CLI verb is `reconcile`: top-level, actorless, and a missing key is a note
and an exit 0.** A top-level verb rather than a `plan` sub-command, and the placement
is the argument - reconciliation has nothing to do with plans, so a reader looking
for "what asks Paystack what happened" would not think to look under `plan`. `plan
tick` is nested because a tick *is* about plans.

It is dispatched above the actor line in `main`, beside `plan tick` (decision 91), and
`_reconcile_command` sits beside `_plan_tick_command` so the missing actor parameter is
visible in the layout. It takes `--as-of` with the tick's parse and the tick's reason,
and it reads `PaystackSettings` through the one reader of the environment,
`paystack_from_environment`.

With no key it prints a `note:` naming `PAYSTACK_SECRET_KEY` and returns 0, which is
the tick's missing-mailbox rule one provider over. The note names the variable because
that is `describe_configuration`'s rule: the failure mode of a job with nothing to ask
with is *silence*, and naming the variable is the difference between a two-minute fix
and an afternoon of guessing.

**151. Every deposit this system had ever attempted against the real provider was
refused, and nothing could see it.** Found by the last step of 3b's verification -
a real test-mode key, a real deposit over HTTP, no webhook - which returned

```
{"status":false,"message":"Invalid character in transaction reference",
 "meta":{"nextStep":"Ensure that you aren't using any characters that aren't
 alphanumeric or contained in \"-,., =\" in your reference"},
 "type":"validation_error","code":"invalid_character_in_reference"}
```

The reference a deposit sends was `"<wallet uuid>:<the caller's key>"`, and Paystack
refuses a colon outright. The route had therefore never once worked: every
`POST /wallets/{id}/deposits` in its history opened no collection, and the only
thing a client ever got back was a 400.

Three things kept it invisible, and two of them were decisions this project had
already made on purpose. **Decision 132 calls the provider *between* two units of
work**, so a rejected call writes no row - the ledger was clean, `reconcile`
correctly kept reporting `nothing in flight`, and a database that had recorded
nothing looked exactly like a database with nothing to record. **The fake provider
validated nothing about the reference it was handed**, so 2,042 passing tests
agreed with the bug; `test_initiate_deposit.py` asserted the reference started with
the wallet id, which it did. And the reference was built in **two** places - see
153 - so the string that reached the wire had never been compared with the string
that did not.

There was a second, reachable instance of the same fault underneath. A client's key
is interpolated into that string raw, and `DepositIn.ref` carried no charset rule,
so anyone holding a token could send `ref: "a:b"` and reproduce the identical 400
through a door they can open. It failed closed, and it failed unreadably.

Nothing was corrupt, and that is the honest whole of the good news: no row was
half-written, no collection was opened, no payer was ever sent to a page. The
deposit route's three phases of design - the pending intent, the boundary, the
credits-nothing rule - were all correct. What was wrong was eleven characters in a
string, and the only thing that could have told anybody was the provider.

**152. The separator is a dot, and the reason is that the stricter end decides.**
For a deposit a reference is two things at once - the ledger's name for a movement
and the idempotency key handed to Paystack (decision 131) - so its shape has to
satisfy the provider, not merely this system. Paystack accepts letters, digits and
`- . , =`; a colon is not in that set.

A dot rather than a hyphen, and the choice is narrower than it looks. The separator
has to be a character a wallet id cannot contain, or the string would not identify
one `(wallet, key)` pair - and wallet ids are UUIDs, which are made of hyphens. A
UUID has no dots in it, so the first dot in the result is always the separator, and
the two halves stay recoverable even though a caller's key is allowed to contain
dots of its own. Nothing parses the string back apart today; this is what makes
that safe to rely on if anything ever does.

What it costs is the consequence decision 107 already documents: stored references
change shape, so a retry of a key minted before the change no longer matches once.
That cost is nil here, for the one reason worth writing down - the faulty shape
never produced a row, because a reference that never reached the provider never
became a ledger row either.

**153. The scoping rule gets one home, in the domain, and the second copy is
deleted.** It existed twice: as `WalletService._scoped_reference`, with the
cross-actor argument written out at length, and inline in `InitiateDeposit._prepare`
with no pointer back to the first. The inline copy is the one that had drifted onto
the wire (151). A rule written twice is a rule that will disagree with itself, and
this one already had.

`app.domain.money.reference.scoped_reference(wallet_id, key)` is now the only place
a caller's key becomes a ledger reference, and both callers import it.
`WalletService._scoped_reference` is gone rather than delegating - a private method
whose body is one call to a public function is an indirection that exists only to
preserve a name.

It lives in the domain because all three layers need it and only the domain can be
imported by all three: the API's schemas import nothing from `app.domain` by design
(see 154), `app.domain.payments` already depends on `app.domain.money` and not the
other way around, and the tests reach for it directly. And it is about the identity
of a ledger row, which is the domain's subject rather than a service's.

**The withdrawal path moved with it**, and that is not scope creep. A withdrawal's
reference never goes on the wire today - no payout reaches a provider yet - so the
colon was harmless there. It changed anyway, for two reasons: leaving one rule at
`:` and one at `.` is precisely the drift this entry is about, and payouts to
external accounts are on the roadmap, at which point that same string becomes a
provider's idempotency key and the bug arrives a second time, in a path nobody
would think to re-check.

**154. An unusable key is refused where the string is built, not at the door.**
The rule is checked inside `scoped_reference`, which raises
`InvalidIdempotencyKeyError`, and **not** in `schemas.py`, which is a decision
rather than a placement. That module's own docstring promises shape-only checks and
imports nothing from the domain; more to the point, the rule belongs to the string
being built, so it holds for every caller - the API today, the CLI, and whatever
opens a collection next - rather than only for the door that remembered to ask. A
refusal made here is also the only one that can *explain* itself: it names the
offending character and the set that would have been accepted, which is exactly
what Paystack's bare 400 never does.

The alphabet is a constant in `app.domain.money.reference`, quoting Paystack's own
sentence, and the space their message shows inside the quotes is read as
punctuation rather than a member of the set - refusing it is the safe side of an
ambiguous sentence, and a test says so out loud rather than leaving it to be
discovered.

`InvalidIdempotencyKeyError` is deliberately **not** `InvalidInternalReference`,
which already existed. That one is the `Transaction` aggregate refusing its own
field - a value that is not a string, or is blank - which is a bug in this codebase.
This one is a caller's key that this system may not use. And the charset is
emphatically *not* a `Transaction` invariant: a plan run's reference is
`plan:{id}:...`, full of colons and correct to be so, and no provider ever sees it.

It reaches a client as a **400 by falling through** `errors._grade`, which is what
makes a refusal this new behave like every refusal that came before it: the same
`{"error", "detail"}` body, the same `error:` line and exit 1 through the CLI, no
handler edited. The OpenAPI descriptions of both `ref` fields were updated too,
which is documentation rather than validation - a client reading the schema learns
the alphabet before it ever sends one.

**155. The double now refuses what the provider refuses, and the alphabet is written
down twice on purpose.** The lesson of 151 is not "a test was missing". It is that
**the double had no opinion**, and a suite whose fake accepts everything agrees with
the code by construction rather than by being right - which is the one failure mode
a fake is supposed to prevent. `FakePaymentProvider.initialize_deposit` now refuses
a reference outside Paystack's alphabet, raising the same `PaymentProviderError` the
adapter raises on a 400, which is what makes `test_deposits.py`'s refusal test able
to fail.

The alphabet is therefore written out as a literal in two places - `tests/conftest.py`
and `tests/domain/money/test_reference.py` - and neither imports it from the module
being checked. That duplication is the mechanism, not a lapse: a copy that imported
the constant would agree with the code by construction, which is the thing being
fixed.

**Neither copy can tell you Paystack's rule is still Paystack's.** Only Paystack can,
and that is the honest limit of this whole arrangement: what the suite can prove is
that this code still agrees with the rule a live 400 spelled out, and what proves
the rule itself is the end-to-end run in "Reconciling the payments whose webhook
never arrived" - which is exactly how 151 was found.

**156. The payer's address is the account's own, and this system's entire email rule
is that it contains an `@`.** `InitiateDeposit` reads the actor's row and hands
Paystack `user.email` - the address the account was registered under - and
`app.domain.identity.user`'s validation is `"@" in email` and nothing else. So
`live@localhost` is a legal account here: it registers, it signs in, it opens a
wallet, and no provider will ever bill it.

**Found live, and only live.** The first deposit this route ever successfully
opened ran against `live@localhost` and came back
`400 invalid_email_address`. Two `curl`s to
`https://api.paystack.co/transaction/initialize` differing in that one field
isolated it: the same call with `live@example.com` returned 200 and an
`authorization_url`. The dot in the domain is the whole of the evidence, and the
live run in "Reconciling the payments whose webhook never arrived" is where it was
collected.

Nothing in the suite could have found this, which is the point rather than an
aside. Every fake in this codebase accepted every address it was handed, and the
accounts the suite registered were at `@localhost` besides - so the tests agreed
with the code and neither agreed with the provider. That is decision 151's
blind-spot pattern a second time, and the second instance is what makes it a
pattern worth naming: **the far end is the only oracle for a rule the far end
owns.**

**157. The address is refused at deposit time, and the refusal is a courtesy rather
than the guard.** Two decisions, both the user's, and they compose: it should be
refused when a person tries to put money in, and the refusal should say something
useful.

Where the check lives follows from what it is. `app.domain.payments.payerEmail`
holds one function, `refuse_unusable_payer_email`, which refuses an address with no
dot after the `@` - and the rule is deliberately the *narrowest* the evidence
supports, not the cleverest available. The two ways of being wrong are not
symmetrical: guessing wrong permissively costs one provider call and a legible
refusal, while guessing wrong strictly refuses a legitimate person's deposit, which
this system cannot undo on their behalf. So `a@b.` and `a@.` are passed through to
the authority, and a test asserts they are.

**The provider remains the authority.** The adapter raises the same
`PayerEmailRefusedError` when Paystack answers `invalid_email_address`, so a client
is told one thing whichever caught it, and correctness never rests on this
codebase's reading of the provider's rule. The local check makes the common case
cheap; it is not a second opinion that could be wrong in the expensive direction.
Same shape as 143: the grace window is a courtesy, and the four settled events are
the guard.

It sits **after** the wallet checks in `_prepare`, so a closed wallet answers with
its own refusal rather than a lecture about an address. It is graded a 400 by
falling through `errors._grade`, like every other refusal this domain makes - no
handler edited, and the same `{"error", "detail"}` body.

`payerEmail` is not an identity rule and is deliberately not raised by `User`. The
account is fine; what is unusable is the address *as a payer address*, which is a
fact about a payment provider and not about the person. A `User` that refused to
hold `live@localhost` would be this codebase inventing an email policy it has not
been asked for and cannot enforce.

**158. Our errors now carry the provider's words, and the body is still not
carried.** `_request`'s docstring documented that the response body is deliberately
discarded, because it is *"a third party's words and may quote the request back -
including the secret key, if it decides to echo headers."* That argument is not
reversed here; it is narrowed. `_json_or_none` parses the body as JSON, and
`_provider_words` extracts exactly the two fields Paystack's error contract
documents - `message` and `code` - and never the body itself. A refusal now reads
`the payment provider refused the call with 400: Invalid character in transaction
reference (invalid_character_in_reference)` instead of a bare status.

The reason this is worth the edit is decision 151: the answer that hid the
reference bug for the entire life of the deposit route was a body nobody kept, so
the operator was left with a status and no sentence. A third party's words are
still not trusted enough to store or re-emit whole; the two fields that name what
went wrong are read out and the rest is dropped. A body that is not a JSON object
contributes nothing, and a test asserts the sentence degrades to the bare status
rather than to a traceback.

**159. `invalid_email_address` is translated into a domain refusal by the adapter.**
Recognising a provider's stable error `code` and turning it into an error this
domain already has is the adapter's job, and it is the same rule
`initialize_deposit` already followed when it raised `InvalidPaymentIntentError`
for a missing `data`. Matching on Paystack's prose would pin this codebase to their
wording; matching on the `code` pins it to their contract.

This is also what makes 157's guard real rather than a claim. The local check and
the adapter raise **one** error, so the API's answer and the CLI's line are
identical whichever caught it - and the day the local rule is wrong, the provider's
refusal still arrives as a 400 naming the address.

Both `400` and the `code` arrived together, so `answers` grew. `initialize_deposit`
already named `AUTHENTICATION_FAILURES` as the statuses it would interpret; a 400
joins via `BAD_REQUEST_STATUSES`, and the principle is unchanged - a status is an
*answer* when it means something about the thing asked about, and the caller names
the ones it will read. The `data`-not-a-dict branch is untouched and still catches
401 and 403.

**160. Every deposit fixture in the suite was registered at `@localhost`, so no
deposit test could ever have deposited.** `TEST_USER_EMAIL`, `ALICE`, `BOB` and the
deposit file's own `PAYER` all used it. The whole API suite was asserting
collections that the provider would have refused, and it passed - which is 156's
blind spot reached from the other side: not a fake that validated nothing, but
fixtures whose accounts could never have done the thing under test.

They moved to `@example.com`, and the value is not a guess: `live@example.com` was
accepted by Paystack in the very live run that refused `live@localhost`. The
`carol@localhost` in the 503 test was left alone deliberately - that request is
refused by the dependency and never reaches the payer check, and arriving at a
different refusal there would be a bug in the ordering rather than a fixture to
fix.

**161. An expectation is written from the test's own input, never from a shared
constant.** Moving those fixtures broke exactly one test, and the way it broke is
worth keeping. `test_sessions.py::test_the_address_is_folded` registered
`"  Alice@LocalHost  "` and asserted the result was `ALICE` - which passed only for
as long as `ALICE` *was* `alice@localhost`. The moment the constant moved, the test
was asserting that the fold of one address equals a different address, and it
failed for a reason that had nothing to do with folding.

The test was wrong before the change and nothing revealed it. What it claimed to
assert was a *transformation*; what it actually asserted was agreement between two
values that happened to be equal, so it would have gone on passing through any
change to the fold that moved both. It now spells out `"alice@localhost"`, and the
rule is general: **a constant standing in for a computed result is that result by
coincidence, and the coincidence ends the next time somebody edits a fixture.**

That is 156's blind spot a third time, and the third instance is what makes the
pattern the interesting part of this whole change rather than a run of bad luck.
A fake that validated nothing, fixtures whose accounts could never deposit, and a
test measuring a constant - all three are the suite agreeing with something other
than the thing under test, and all three survive a green run indefinitely. What
they have in common is that nothing in the repository is an oracle for them. Only
a real call, or a deliberate re-reading, ever finds one.

### The address an account holds, and the way out of a bad one

This closes the gap decision 156 opened and decision 157 refused to close at the
time. That refusal was right about the *deposit* and wrong about the *account*: an
address no provider will bill should not be refused at the only door that can
create one, because a person standing at a registration form cannot be asked to
know what Paystack accepts - but an account that holds one is stranded, and until
now the only remedy was to abandon the wallet with it.

Two halves, and they are one change rather than two:

```
POST /users                  ──┐
                               ├── the aggregate's shape rule   (User)
                               └── refuse_unusable_email()      (the entry rule)
POST /users/me/email-changes ──┘   ...then at most one pending EmailChange per account

POST /email-changes/confirm  ──► claim_by_token_hash()   one statement, the spend
                                   └─ users.save(user)    the same unit ──► one commit
                                        └─ best-effort notice to the address left behind
```

**162. One predicate, three call sites, read as a guard at two of them and as a
courtesy at the third.** `has_real_domain` answers one question - is there a dot
after the last `@` - and it now lives in `app/domain/identity/emailAddress.py`,
because what an address *is* belongs to the aggregate that holds one. Decision 157
argued why the deposit-time reading is a courtesy and that argument has not
changed; what changed is that the *same narrow rule* turned out to be the right
entry rule too, for the opposite reason.

The asymmetry is the point: a guard that guesses wrong costs a retry, because the
person is at a form holding an address they already use. A courtesy that guesses
wrong costs a deposit that cannot be undone on their behalf. So the rule stays
narrow in both places, and the two readings are two functions with two error
classes and two sentences - `UnusableEmailError` and `PayerEmailRefusedError` -
because a caller catching one is catching a fact about the address an account may
hold and one catching the other is catching a fact about a provider.
`app.domain.payments.payerEmail` delegates the predicate here rather than
restating the dot test, and `tests/domain/payments/test_payer_email.py` pins the
agreement across ten addresses, so the only way to change either behaviour is to
change the one predicate.

**163. The entry rule is checked where an address is minted, and never by the
aggregate.** The obvious home is `User.__post_init__`, and it is the wrong one:
that method also runs when a row is *loaded* by the repository, so a rule enforced
there would make every existing `@localhost` account unreadable - turning a
stranded account into a broken one, which is strictly worse than the problem. It
belongs at the operations that mint an address, and there are exactly two:
`SignUp` and the email-change request.

The consequence is an asymmetry that is deliberate and documented: `change_email`
does *not* enforce usability either, because construction must accept what is
already on disk and the two paths would otherwise disagree about what a `User` may
hold. What `change_email` does own is the aggregate's own rule - non-empty, one
`@`, folded - and that rule was extracted into one function (`checked_email`, with
the folding it already had) so that `__post_init__` and the method cannot drift
apart. The method validates into a local before assigning, so a refused change
cannot leave a `User` holding a bad address either.

**164. Shape first, then usability, and the order is load-bearing rather than
tidy.** `"not-an-address"` has no `@`, so the usability rule refuses it too - but
it must keep answering `InvalidUserEmailError`, which is the rule a person can act
on, rather than `UnusableEmailError`, which is a statement about a provider. Each
value is refused by the more specific rule it actually broke, and the two are 400s
by the same route: neither is listed in `errors._grade`, and an unlisted
`MoneyError` falls through to a 400.

**165. An email change is authorised by a session *and* the current password.**
Neither alone is enough. The session says which account, and it is the only thing
in this codebase that can produce a `User`; the password is what makes a stolen
token insufficient to move an account. The password is verified with
`PlainPassword` + `PasswordHasher.verify`, and the wrong-password refusal is
`InvalidCredentialsError` - `LogIn`'s own error, deliberately - so the 401 and the
sentence are identical across the two places in this system a password is checked,
and a value that could not be a password still gets `WeakPasswordError`'s 400 for
free.

**166. The confirmation is authorised by the token alone, and that is not a
bypass.** `LogOut` is the precedent: the one operation in this codebase authorised
by the thing it destroys. The token exists only because somebody already proved
the account's password in order to mint it, so the password proof has been spent
on this change already. Requiring a live session on top would refuse exactly the
person this feature is for - asked on a laptop, opened the mail on a phone - and
would leave the link dead if their session lapsed inside the window. The habit
decision 123 records applies here unchanged: this is an accident guard, not a
security control, and the rate limiter is what will make the second half of that
sentence less uncomfortable.

**167. The credential is 256 bits rather than six digits, and the reason is the
rate limiter that does not exist.** A short numeric code is the friendlier shape
and it is brute-forceable, and the endpoint it would protect is one whose whole
power is moving an account's address. Rate limiting is a known unbuilt item
(decision 86), so the token reuses `Session`'s exact discipline - 256 bits of
CSPRNG output, SHA-256 at rest, the hash always *derived* from a presented value
and never taken from the request. `hash_session_token`'s docstring said "exactly
two callers" and now says three, with the reason written beside it: the rule is "a
256-bit token, hashed at rest", and where it was first needed is not what it is
about.

**168. `EmailChange` is `Confirmation`'s lifecycle with none of its fields.**
`Confirmation` requires a `wallet_id` and its docstring calls it "a request to
move money out", so it is the wrong container. But its *lifecycle* is exactly what
this needs - single-use, windowed, with three refusals distinguished by one atomic
claim - so the new aggregate mirrors that shape and shares nothing else. Three
things came across deliberately:

- **`EXPIRED` is derived and never written.** `expires_at` already holds the fact,
  and the only thing that could write `EXPIRED` is a reader, which would make a
  `GET` a write.
- **The row survives the change**, which is what makes the refusals
  distinguishable: deleting it on use would collapse "already used" into "never
  existed", and those two have different remedies. It also means the last change
  is readable per account, which is most of an audit trail for the cost of one
  column.
- **No `previous_email` column.** Nothing would read it - the notice to the old
  address is composed from the account as it is read *before* the change - and a
  column written and never read is the speculative state this codebase refuses.

`EMAIL_CHANGE_LIFETIME` is a separate constant from `CONFIRMATION_LIFETIME` even
though the value currently matches, because nothing has decided those two windows
move together.

**169. One pending change per account, structurally.** `user_id` is the primary
key of `email_changes`, so "at most one" is the schema rather than a rule somebody
has to remember, and a second request is an `ON CONFLICT … DO UPDATE`. Superseding
is not a shortcut but the behaviour a person wants: a mistyped address should be
correctable by asking again, and a token nobody wants must not stay live.

**170. A spent request is spent by the attempt, not by the success.** A confirm
that finds the address taken in the meantime refuses with `DuplicateEmailError`
and leaves the request `CONFIRMED`; the remedy is to ask again, not to retry the
token. This is `ConfirmationStatus.CONFIRMED`'s rule stated again, so a client that
has met one has met the other - and it is the reason the confirm commits *before*
it raises, since a refusal that rolled back would leave the token live and the
rule would be a comment.

**171. What can change between the request and the confirm is checked twice; what
cannot is checked once.** The address may have been taken by somebody else inside
the fifteen minutes, so the duplicate lookup runs again at apply time. The
usability rule cannot change inside one run of one version of this code, so it is
checked at the request only - and a second check would be a second place to
disagree with the first.

**172. The credential-bearing mail is sent in-request and its failure refuses the
request; the notice is best-effort and its failure is reported.** The asymmetry is
the whole argument. A mail carrying the credential must arrive or the request is
pointless, so it raises - and the row that remains after a failed send holds no
credential, because the token is hashed at rest, so the harmless leftover is
retried by asking again, which supersedes it.

A mail announcing a fait accompli must not be able to undo it. Refusing to change
an address because a doomed notice could not be delivered would strand the very
account this endpoint exists to rescue - and it *will* bounce for a stranded
account, which is the case the endpoint is for. So its failure is caught into the
result instead: `notice_sent` and `notice_error` carry three states in two fields
(`True`/`None`, `False` with the reason, and `False`/`None` for an installation
with no mail at all), and both presentations print a bounced notice as a footnote
under a success rather than as a failure.

Neither message goes through the outbox. `NotificationKind` is a closed set of
wallet-boundary events, `Notification` has no expiry *by design*, and the queue's
whole premise is that late is acceptable - and a credential that is worthless
after fifteen minutes is the one thing that cannot be late.

**173. With no mail account configured, the change applies on the password proof
alone - and both presentations say so.** Otherwise a fresh install could never fix
a bad address: the trap would simply move one layer down, and the person would be
told to check a mailbox no code can post to. The API answers `applied` with
`expires_at` absent, the CLI prints the change and then names the missing variable,
and the sentence is the same one the deliverers already report, because it is the
same fact. An installation without mail is never *silently* less safe than one with
it.

**174. No link is fabricated in the mail.** This system does not know its own
public address - there is no such setting, and inventing one would be a promise no
code keeps and a value that is wrong on every install but one. The message carries
the token and says where to present it: `confirm-email` at a terminal, or the
route. A real link wants a base URL setting that also has to be right behind a
proxy, which is a deployment decision rather than a message decision.

**175. The change does not revoke sessions, and that is a decision rather than an
omission.** `Session` is bound to `user_id` and not to an address, so a change
cannot orphan one - nothing breaks by leaving them alone. Revoking would buy
little against the threat that matters: an attacker holding both the token and the
password logs in again immediately. And there is no `delete_by_user_id` to call,
so the honest options were to add one for a measure that does not measure much, or
to leave it. What it does mean is that a session opened before the change keeps
working afterwards, which is worth knowing rather than assuming either way.

**176. A secret is never an argument and never a path segment.** Two
presentations, one rule, and both halves are the existing rule rather than a new
one. The CLI prompts for the token with `_prompt_token` beside `_prompt_password`,
for `_prompt_password`'s own reason: an argument lands in the shell's history file
and in the process table. The API takes the token in the request *body* rather
than the path, so that a credential which can move an account does not end up in
an access log, a proxy log, or a `Referer` header. It is the same reasoning as
decision 83 one bearer of a secret further along.

The CLI's placement follows the same split. `change-email` sits *below* the actor
line, because it acts as somebody; `confirm-email` sits *above* it beside
`signup`/`login`/`logout`, because the token is the whole authorisation and there
is no session it could act as. So one machine can ask at a desk and answer from a
terminal that has never logged in - and a test deletes the session file before
answering, to say that with the filesystem rather than in a comment.

**177. `get_by_id`'s discipline is narrowed rather than the door widened.** The
repository method's docstring reserved it for the auth boundary. Confirmation
*must* read a user by the id a claimed token names, and the request reads its own
account inside its own transaction. What still holds, and is what made it safe, is
the part that did not change: the id always comes from a resolved actor or from a
claimed token, and never from a request.

**178. The third `Deliverable` is not an aggregate, and the Protocol's cost was
paid where it was predicted.** `Deliverable` is a `Protocol` with three attributes
- `recipient`, `subject`, `body` - so a composed verification message goes through
`SmtpNotificationChannel` with no new aggregate, no `NotificationKind` member and
no `plan_id`. Its docstring said "only two classes are ever passed to a channel";
that sentence now says three, and `deliverable.py` names the thing it cannot
enforce: nothing checks a `Protocol`, so a channel that reached for a fourth
attribute would fail on the first message of the second *kind*, not at import.
`TestTheThirdDeliverableShape` in
`tests/infrastructure/notifications/test_smtp_notification_channel.py` is the
compensating control that sentence asked for - it asserts the absence
(`hasattr(mail, "plan_id")` is false) and then sends the message anyway, because
the absence is the claim.

**179. A rule that needs two aggregates is re-applied by every use case that loads
both - and this slice found one that isn't.** A plan's currency must match its
wallet's, and `SavingsPlan` cannot enforce that: its own docstring says so, because
a plan cannot see a wallet. `PlanService.create_plan` is where the rule actually
lives, in the one place both are loaded - and `edit_instructions` loads only the
plan. So a plan can be created honestly in NGN and then edited into USD, the
aggregate re-validating exactly what it can see (non-empty, one currency *among
the instructions*, source rules) and nothing that needs the wallet.

The consequence is worse than a wrong answer, which is why it is here rather than
in a list: the run's pre-flight compares `wallet.available_balance <
plan.total_to_move`, two `Money` values in different currencies raise
`CurrencyMismatchError`, `ExecutePlanRun` re-raises it, and `RunDuePlans` has no
per-plan catch - so one such plan stops that tick's remaining plans, for every
user, on every tick. It is in `### Still open` as the first of the audit's
findings, and it is the shape this whole slice was about: a value that could have
been refused at the door, refused later in a place that holds more than the one
thing it was asked about.

**The ten verification items, run by hand against a throwaway database, with the
suite as one of them.** Item 1 is `python3 -m pytest -q` - 2,267 passed in 200
seconds - and it is the only one that can be run alone. The other nine exist
because a green suite proves the code agrees with itself and this project has
already been taught that lesson once (decisions 151-155), so each of them puts a
claim in front of something this codebase does not own: a real HTTP call, a real
SMTP conversation, a real store read with the standard library's `sqlite3`. Item 3
is the whole slice and the reason the two halves are a composition rather than a
pair - an account registered at `nobody@localhost` before the rule existed, a
deposit refused by the payer courtesy, `change-email` to a real address, and the
same deposit then accepted with an `authorization_url` from Paystack.

Items 4 to 9 each put one decision in front of that something. The session minted
before the change still authorises after it - five address changes and both
deposits, which is decision 175 made visible rather than argued. Both mails arrived
through `aiosmtpd`: the verification carrying the code and no fabricated link, the
notice carrying no code at all, naming the change, the moment, and the actionable
fact. The three refusals were distinguishable by class and sentence. A second
request left one row per account and killed the first token. An unconfigured install
applied the change at request time, minted no row, and named the variable it lacked
(`SMTP_HOST is not set`). A refused notice at confirm time reported
`[Errno 111] Connection refused` while the address change stood. Item 10 is the
boundary test's set - the two new routes appear nowhere in `app/presentation` outside
their own module - and `plan tick` was handed over rather than run here, since it is
actorless by dispatch position and nothing in this slice moved it.

**Two of the ten refused to be as tidy as the plan assumed, and both were the
run's errors rather than the code's.** The CLI's top-level `deposit` is a ledger
credit (`WalletService.deposit`), not the provider path, so the first attempt at
item 3's refusal demonstrated a command that was never supposed to refuse it; the
payer check is reachable only through `POST /wallets/{wallet_id}/deposits`, which is
the one path where a provider is asked to collect. And `sqlite3` is not installed on
this machine, so every store read in the run is a `python -c "import sqlite3"`
one-liner.
Both are recorded because the alternative is a verification section that reads as
though it went to plan.

**One item's evidence is stronger than the plan asked for.** Item 6 wants an
expired token, and the plan's route to one is a row backdated in the store. That
works, and it is not what happened: the token minted at 18:26 for
`nobody4@example.com` was still unspent when its window closed at 18:41, during the
attempt to paste it cleanly, so the refusal the run recorded - `this email change
expired at 2026-09-14T18:41:04.676425 and can no longer be answered` - is fifteen
minutes elapsing rather than a `datetime` written backwards. The backdated row was
kept as well, and what it did is the more interesting half: `EmailChange.__post_init__`
refused to load it at all (`an email change must expire after the moment it was
requested`), and the nearest state the shipped code can reach is
`expires_at = requested_at + 1 second`, which is how the suite reaches it too. That
is the aggregate doing exactly what decision 163 says an aggregate should do, and it
is the contrast that makes the `User` case read correctly: a window is a fact about
the row's own two fields, and usability is a fact about the world.

**180. A token prompt must strip and a password prompt must not, and the alphabet
is the whole of the difference.** `_prompt_token` returns
`getpass.getpass("confirmation code: ")` exactly as typed, and the verification
message indents the code by four spaces under its heading - so a reader who selects
the whole line rather than the value pastes 44 characters whose inner 43 are the
token. The refusal is `InvalidEmailChangeTokenError`, which names nothing,
deliberately: a message saying "that is a code with a space on the front" would tell
a caller something about what the store holds, and the remedy for a mistyped code and
for an invented one is identical - ask again. The live run hit this more than once,
and each time the refusal was indistinguishable from a token that had never existed.

Stripping is safe here for a reason the token alphabet guarantees rather than one the
prompt happens to satisfy: `secrets.token_urlsafe` draws from `[A-Za-z0-9_-]`, so
whitespace cannot be part of a real token and any that arrives is an artefact of the
selection. It is **not** safe one function up, and the difference is not tidiness.
`_prompt_password` shares the no-strip habit deliberately, because a password may
legitimately begin or end with a space - and the two failures are not equal:
refusing a password a person typed locks them out of their own account, while
accepting whitespace they did not type is a password they could not reproduce
anyway. So the two prompts keep one shape and two dispositions, and what separates
them is what the printable set can contain.

**This run demonstrates the fix rather than shipping it.** The `.strip()` that made
the paste work lived in a wrapper written to measure it; `cli.py` still passes the
paste through untouched. It is recorded in that order on purpose, so the decision
record does not claim a change the code does not have.

**181. Neither mail identifies its own request, so a mailbox holding five of them
cannot tell them apart.** Both subjects are fixed strings - every request's mail says
"Confirm your new email address" and every notice says "Your account's email address
was changed" - and neither body carries the `email_change_id`. The live run put five
requests into one inbox inside an hour, and the only thing that distinguished them
was arrival order, which is the one property a mail client does not promise: a
threaded or re-sorted view loses exactly that, and the person is left holding codes
with no way to know which window each one belongs to.

This is a usability defect rather than a wrong state, which is why it is in
`### Still open` instead of fixed here. Two candidate fixes answer different needs.
Carrying the `email_change_id` makes a mail citable - it is a bare UUID that means
nothing outside the store, so it adds no secret to a message that already carries the
one credential - but it is addressed to somebody with no way to use it. Quoting the
*request's* moment rather than the window's close gives the reader a fact they can
match against what they did, which is how a person actually tells five similar mails
apart, and it costs one line in a message that already prints when the window shuts.

**182. An aggregate invariant reaches the wire as a 400, and its own docstring calls
it a bug.** `errors._grade` maps any `MoneyError` that is not in `UNAUTHORIZED`,
`NOT_FOUND` or `CONFLICT` to 400, documented as "the safe direction to be wrong in" -
and `IdentityError` is a `MoneyError`, so `InvalidEmailChangeWindowError` is a 400.
The error means `expires_at <= requested_at` on a stored row, which
`EmailChange.__post_init__` refuses to construct, so the only way to reach it through
a running server is a row written straight into the database - which is what the live
run did, and what the aggregate then refused to load. The `unexpected_error_handler`
is never reached.

The fallthrough is right for the class it was written about, which is input a person
supplied, and this error is not in that class: it is reached by a row, not by a
request. That is a question rather than a fix, because both answers cost something,
and it is in `### Still open` with the two of them.

### The password somebody has forgotten, and the way back in

This is the last item of Phase 2c, and it closes one half of the `### Still open`
entry decision 156's slice left standing: an address could be moved, and a password
could not. The other half - *verification* of an address an account already holds,
as distinct from changing one - is still open and still not this.

It is **the address change with the proof removed**, and that one subtraction is the
whole of the design. Everything the change flow took from `Confirmation` comes across
unchanged: a 256-bit token hashed at rest, one atomic claim that spends it, three
refusals distinguished by that claim, a row that survives being spent, a mail that
must arrive and a notice that must not be able to undo anything (decisions 166-172).
What does not come across is the authority the *request* is made under. A change
takes a session **and** the current password (decision 165); a reset cannot, because
the caller's entire problem is that they hold neither.

```
POST /password-resets          ──►  look the address up, and answer the same either way
                                     └─ resets.save(row)         the row before the mail
                                          └─ a code mailed to whatever that address names

POST /password-resets/confirm  ──►  claim_by_token_hash()       one statement, the spend
                                     └─ credentials.save(...)   the new password
                                          └─ sessions.delete_by_user_id()
                                               └─ one commit ──► best-effort notice
```

**183. A second aggregate, not a generalised one.** The question this entry said to
answer first was whether `EmailChange` generalises, and the answer is no - for
decision 168's own reason, restated because the pressure to generalise is stronger
here than it was there. The two rows share a lifecycle and share no field: an
`EmailChange` carries a `new_email`, because the new address is the fact the request
authorises and it must be on the row before the mail goes out; a `PasswordReset`
carries nothing of the kind, and **the field's absence is the design**. A password
must never be on the row at all, so the row authorises "set a new password" and the
password itself arrives with the confirm, living only in a local variable for the
length of that call. A shared base class would have to be a base class with the one
field the child needs in a subclass - which is a generalisation that pays for nothing
and couples two flows whose next changes will be different.

What carries across is copied rather than inherited, exactly as decision 168 argued
for the first of the two: `AWAITING` / `CONFIRMED` / `EXPIRED` as its own enum,
`PASSWORD_RESET_LIFETIME = timedelta(minutes=15)` as its own constant - two numbers
agreeing today is a coincidence of two arguments, not one decision - `EXPIRED`
derived and never written, and `settled_at` and the status required to agree. The
store is the same two methods and no `find`, because a request is written once and
answered once and both operations already end holding the row they acted on. `save`
upserts `ON CONFLICT(user_id)`, which makes "one pending reset per account"
structural rather than checked - decision 169's rule, one table over - and it is what
makes asking twice supersede: the second request's row replaces the first's, so the
first code is refused as a code that never existed rather than as one that was
superseded, which is the same choice decision 168 made about rows that survive.

**184. The request answers 202, and the body is byte-identical whatever address it is
given.** This is the decision the route is built around, and 202 rather than 201 is
only the visible half of it. `POST /users/me/email-changes` answers 201 because
something is always created: the caller proved the account and the row is theirs.
Here an unknown address creates nothing, so a 201 would be a lie in half the cases -
and **a status that differs between the two cases is an enumeration oracle**, which
is a worse failure than the small lie it would tell. A stranger who can post a list
of addresses and read the status has a membership test for this installation, and the
answer to that is one response shape for both arms, with the sentence the body cannot
carry ("if that address names an account, a code has been sent") living in the
OpenAPI description where a client reads it once rather than per request. The tests
assert it on the raw bytes: `test_an_unknown_address_gets_the_same_bytes` compares
the two responses' text *and* content, because a difference in headers or in a
trailing newline is the same oracle by another route.

**185. The CLI prints the truth, and the two presentations disagree on purpose.** The
terminal says `no account reads mail at ada@example.com | nothing was sent`, and that
is not a leak of what the API is at pains to hide. A terminal that can write to this
database is not a public surface: its operator can open `sqlite3` and read the `users`
table, so withholding the answer would protect nobody while leaving the person who
mistyped their own address unable to tell a typo from a delivery failure - and a typo
is how a good share of legitimate requests arrive. The API's caller is a stranger with
a list; the CLI's caller is somebody who already holds the database and wants to know
which of two things just happened. The two presentations are the same use case with
the same behaviour and two honest reports, and it is worth writing down as a decision
rather than leaving as an inconsistency, because "the CLI and the API say different
things here" reads like a bug in either direction and is the correct answer in both.

**186. With no mail account the request refuses, and decision 173's fallback does not
transfer.** The change flow applies immediately when there is no channel, which is
sound there and unsound here for one reason: it is authorised by the password proof,
which the request already carries, so applying without a mail is a smaller act than
what was asked for. A forgotten password has no equivalent proof - the caller cannot
demonstrate the one secret the account holds, which is the whole premise - so
"apply anyway" has no meaning, and a privileged local path was considered and rejected
as a second way into every account in the installation. The refusal therefore has to
be a refusal, and the only thing it owes the operator is the name of the variable
that is missing, which is `describe_configuration`'s entire purpose: the failure mode
of a misconfigured notifier is silence, and "SMTP_HOST is not set" is the difference
between a two-minute fix and an afternoon of guessing.

It is raised by the **use case** rather than by an API dependency, and that is a
deliberate departure from the arrangement `dependencies.payment_provider` uses. There
is one mail configuration for the whole installation and *two* presentations that
need this refusal, so a guard written into the HTTP layer would have to be written a
second time in the CLI - the shape `fold_email` exists to end. Instead the *builder*
composes the reason once and hands it down, both presentations render
`NoMailAccountError` through the handlers they already have, and the API grades it to
a 503 while the CLI exits 1 with the same sentence. See decisions 83 and 176 for the
two rules that keep the credential out of the argument list on the way there: it is
read with `getpass` and never passed as an argument, and it travels in a body rather
than in a path.

**187. A weak new password does not spend the code, and this is decision 170's
opposite on purpose.** Decision 170 spends a request on the attempt, and the case it
decides is a change to the *world*: the address was taken while the request waited, so
a retry cannot succeed and the token is dead weight. A password that fails
`PlainPassword`'s policy is a statement about the string the caller typed, and a retry
succeeds - so the remedy is "type a longer one" rather than "go and find another
mail". This is decision 171's own principle applied rather than contradicted: what can
change between the request and the answer is checked twice, what cannot is checked
once, and **nothing about this flow can change in the window**. The password is
therefore validated before the claim, which puts the refusal where it belongs - a 400
in the domain's vocabulary, from `WeakPasswordError`, rather than a 409 about a row -
and at a terminal it reads as the same code working on the second attempt. The tests
pin it by reusing one code across two invocations, the first refused and the second
accepted, because a version that spent the code would still answer the first
invocation identically.

**188. Every session on the account is deleted, which is decision 175's opposite
answer, and the two questions really are different.** An address change leaves
sessions alone, because `Session` is bound to `user_id` rather than to an address, so
a change breaks nothing and a person who changes an address because they believe the
old one is compromised does not thereby log the old holder out - which is named as a
known limitation in `### Still open` rather than as an oversight. A reset is the
mirror image. Its entire premise is that somebody else may know the old password, so
leaving the old sessions alive leaves the attacker holding a live token under a
password that has just been changed *because* it was the suspect thing. The delete is
`SessionRepository.delete_by_user_id`, which returns the count so the caller can
report it, and it is deletion rather than a flag per decision 49. It is also the
reason the CLI prints how many ended: a person who was signed in on a phone will find
it signed out, and a command that did that silently would leave them reading a closed
tab as a bug.

The three writes - the claim, the new credential, the revoked sessions - are **one
transaction**, and that is the correctness boundary rather than tidiness. "The code
was spent", "the password is new" and "the old sessions are gone" have to be one fact,
or a crash between them leaves sessions alive under a password that no longer works.
`test_the_writes_land_together` injects a failure between them and asserts the
surviving state is the *old* one: the code unspent, the old password still valid.

**189. The timing oracle is real, is not closed, and is named where someone would look
for a claim that it is.** `POST /password-resets` mails on one arm and not the other,
and an SMTP round trip is far more measurable than the hash work decision 81
equalised - so the endpoint leaks account existence *by latency* even though its
response is identical. Nothing available closes it. The mail cannot be queued, because
a code that dies in fifteen minutes cannot go through an outbox whose whole premise
is that late is acceptable (decision 30, and `passwordResetMessage` says so at the
top). A mail cannot be composed for a nonexistent address, because there is no address
to compose for. And there is no rate limiter (decision 86), which is the thing that
would actually answer it - not by making the two arms equal, but by making the number
of probes a stranger gets small enough that the leak stops being a list. It is
recorded in `### Still open` as the strongest argument for building rate limiting
next, rather than as a comment claiming it is handled.

**190. An account that has never had a password can gain its first one this way.** The
credential is looked for and its absence does not change the answer: the mail goes
out, and presenting the code writes a `PasswordCredential` for an account that had
none. Refusing would have to be *silent* - saying "this account has no password" is
the enumeration oracle returning by a different door - so the choice is between
mailing a person who can then use their account and telling nobody anything while
leaving them with no way in at all. Mailing is both the more useful answer and the
more honest one, since the mailbox is exactly the evidence that they own the account.
This was unreachable when it was written, because `SignUp` wrote both rows in one
unit and no other path created a `User`. **It is reachable now**: `SignUpWithGoogle`
is the second path, and the account it creates holds an address and no credential
(decisions 204-213). So this is the decision that made the arrival uneventful - a
Google account sets its first password by asking for a reset, which is the flow that
was designed for it before it could exist - and the alternative would have been to
re-derive it against a live account.
**191. `_prompt_code` is extracted and strips, which closes decision 180's unshipped
fix.** That decision recorded the paste problem, demonstrated the fix in a wrapper
written to measure it, and said in as many words that `cli.py` still passed a pasted
code through untouched - and this slice is where the second caller appeared, so the
fix ships here rather than being restated. One function now serves `confirm-email`
and `confirm-password-reset`, and its label is a parameter because the two commands
prompt for two different codes: "confirmation code" belongs to an address change and
"reset code" to a password reset, and a person looking at a prompt naming the other
one would reasonably wonder whether they were in the right command. The stripping
argument is unchanged and is the alphabet's (`[A-Za-z0-9_-]` cannot contain
whitespace), and `_prompt_password` deliberately still does not strip - so the pair
keeps one shape and two dispositions, and the tests assert both halves by pasting a
code wrapped in whitespace and by setting a password with a trailing space that must
survive to the login prompt.

### Profiles, tiers, and the ceiling an amount meets

This slice closes a hole the README had already named against itself (the
entry-point audit, decision list below and `### Still open`): **amounts were refused
for shape and never for size.** `Money` refused a non-finite value, a bool, a third
decimal place and a foreign currency in arithmetic; `<= 0` was refused at two doors;
and nothing refused an amount above any ceiling. The only thing that would is the
provider, at the far end, after the request has left.

It closes it with a *profile*, and that pairing is the design rather than a
convenience. A KYC tier **is** a set of amount ceilings, so "give an account the
identity fields a tier is derived from" and "give the tier teeth" are one job -
built separately they would have been built twice, and the second version would have
been free to disagree with the first about what a tier means.

**192. A `Profile` is its own aggregate, keyed on `user_id`, and not a set of fields
on `User`.** The reason is the one that gave `PasswordCredential` its own table:
`User` is loaded by *every authenticated request* and rendered by `translate.user_out`,
and personal identity data on that object is one forgotten omission away from being
served to every caller who asks who they are. A separate aggregate makes the leak not
a thing to remember but a thing that cannot happen - the profile is read by the two
places that need it, under the actor's own id, and by nothing else.

**193. A tier is derived from the profile on every read and never stored.** `tier_for`
takes a `Profile | None` and answers `IDENTIFIED` only when the four fields a real
identity check would confirm are all present, `UNVERIFIED` otherwise; `None` is a
normal input and not an error, because every account that existed before this feature
has no profile row. A stored tier would be a column free to disagree with the profile
it describes, and the direction it drifts in is the dangerous one: an account reading
`IDENTIFIED` over an empty profile. It also would have made this slice untestable
until a verification provider existed - the derived version is exercisable today.

**194. The limits are a table over every `(Tier, Currency)` pair, written out rather
than converted.** `Money` carries an explicit currency and `Currency` has five
members, so a limit scaled by an exchange rate would need a rate - and this system has
none: no FX table, no rate provider, no moment at which a rate could be said to be
current. A limit computed from a rate changes its meaning the day the rate moves, and
a ceiling that moves on its own is not a ceiling. The five non-NGN columns are
deliberately *lower* than the naira one rather than derived from it, because the
currencies this system does not actually collect in are the ones it should be most
careful about. `limits_for` raises `KeyError` for a missing pair rather than falling
back to a default, and a test asserts the table covers the whole product of the two
enums - so the failure is a test at build time rather than a `KeyError` in production
the day somebody adds a sixth currency.

**195. The unverified row is 50,000 a movement, 200,000 a day, 300,000 held.** Every
existing account is on it on rollout, so these numbers decide whether an account that
is moving money today still works tomorrow. They are set at the point below which a
person's ordinary use of the product is untouched - a plan contribution, a withdrawal,
a payout - and deliberately not so low that the limit is the first thing a new user
meets, because a tier 0 that refuses ordinary use teaches people to hand over a date
of birth to stop being annoyed, which is the wrong reason to give one. The balance cap
is three days at the daily cap, which makes it a cap that can be *reached*: a cap
nobody can reach is a cap that is never tested. The step to `IDENTIFIED` is an order
of magnitude, which is the shape a tiered programme has in practice - and what it
means is *we have their details*, not *we checked them*, since no provider verifies
anything in this slice.

**196. Each tier has three ceilings, and they are not three interchangeable dials.**
The per-transaction ceiling and the daily cap face value *leaving*; the balance cap
faces value *arriving*. So a credit is judged by the balance it would produce and
nothing else - one large deposit into an empty wallet is bounded by the same number as
a hundred small ones, so a per-transaction ceiling on inbound money would add nothing.
And a movement out has no balance ceiling at all, deliberately: the balance cap is a
statement about what a wallet may hold, a movement out can only lower it, and applying
it there could only ever refuse to let somebody spend down towards a cap they are
already above - a control turned into a trap. Each comparison is `>` and not `>=`, so
a movement exactly at the ceiling is allowed; an allowance of 50,000 is worth 50,000.

**197. The ceilings are enforced at confirm time, in `WalletService._run`, and not at
request time.** `request_confirmation` already argues why the *balance* is not checked
then: the check that can be relied on is the one at confirm time, and a second one
earlier would be a weaker copy of the wallet's own rule free to disagree with it. A
limit is the same shape of rule and gets the same answer. **The bypass this does not
open** - the obvious objection - is that twenty recorded withdrawals confirmed in
sequence do not get twenty movements through: each confirm reads the day's total
inside its own unit, and the earlier confirms have already committed their rows, so
the twentieth sees the nineteen before it and is refused. The request stays inert; the
confirm is authoritative.

**198. The limit check runs *inside* the operation, between the PENDING row and the
movement, rather than before the operation starts.** This is a placement with an
argument rather than a preference. Invariant 7 of this slice says every refusal is
recorded, and the only way a refusal can be recorded is if it happens after the ledger
row exists and inside the `try` that catches `MoneyError` - so the ceiling is a `guard`
callable on `WalletOperation` that `execute` runs after saving the row and before
`_apply`. `TierLimitExceededError` therefore derives from `IdentityError`, which is a
`MoneyError`, and travels the same path every other refusal does: recorded FAILED and
committed. An exception root outside that tree would have been a refusal the ledger
never hears about, which is the one thing a financial control must not be. The same
placement is why the day's total is read *before* the row is written: a PENDING row
counts toward its own day, so a total read afterwards would include the movement in
its own allowance and refuse at half the cap.

**199. The unit of work opens with `BEGIN IMMEDIATE`, and it is not silently contained
to this feature.** The deferred `BEGIN` it replaces takes a read lock and upgrades only
at the first write, so two confirms can each read the same day's total *before* either
writes - both pass, both commit, and the daily cap is breached by an amount neither of
them did anything wrong by its own reading. SQLite's single-writer rule does not save
it: both reads happen before either write, so the second write waits and then succeeds.
`BEGIN IMMEDIATE` takes the write lock at the start, which makes the read and the write
it authorises one critical section. That serialises money operations against each other
(which SQLite does for writes anyway) at the cost of a unit holding the write lock for
its whole life, read-only ones included - while readers still run together, since a
reserved lock coexists with shared ones in the default rollback-journal mode. It
affects every unit in the system and is called out for that reason rather than folded
into the limit work.

**200. A plan run is *blocked* by a ceiling, not stopped by it.** A plan-run payout
never goes through `WalletService._run` - `_move_the_money` builds its operations
directly, because a scheduled run has no confirmation and no person in it - so without
a check of its own a plan would have been the one way to move money with no ceiling at
all, and the caps would have been decorative for exactly the accounts that move the
most. The check lands in `_blocking_reason`, as a new `RunBlockReason.TIER_LIMIT_EXCEEDED`
member. It is a *block* rather than a raise because a raised error takes down the whole
scheduler tick: five payout instructions judged one at a time would raise on the fourth
and every other plan that tick would not run. Blocking is the honest state - the run did
not happen, it is recorded as blocked for a reason, and the tick carries on. The
ceilings are applied per instruction *and cumulatively*, by passing the running day
total forward, so a plan whose lines each fit but which together cross the cap is
refused; and only lines that send value out are counted, so a release-only plan is
judged by nothing here, exactly as it faces no ceiling when a person types it.

**201. The balance ceiling is refused at initiate deposit, and the settle-time half is
deliberately left open.** A deposit that would leave the wallet above its cap cannot be
credited when it settles, so opening a collection for it would send a payer to a
checkout page to buy a rejection. The check at that door is `check_credit` itself
rather than a second comparison written out, so the courtesy and the rule it foreshadows
cannot drift apart. What it is not is a guarantee: the balance it reads is free to
change before the payer pays, so two collections that each fitted can both be paid and
both settle, and nothing refuses the second one - `SettlePayment` credits the wallet
directly and does not go through the ceiling. Closing that gap means deciding what
happens to a payment the payer has *already made* and the wallet may not accept: hold
it, reverse it, or exceed the cap for it. Each is a real answer with a different
conversation attached, and guessing one here would be inventing a refund policy in a
docstring. It is in `### Still open`.

**202. The limit-day is the server's local day.** Every timestamp in this system is a
naive `datetime` written from a presentation's `datetime.now()` - and for a movement
out, from the `as_of` that presentation hands down, which is the same value (decision
203); there is no timezone
anywhere in the codebase, no column holding an offset, and no moment at which one is
recorded. So the stored rows are in the server's wall-clock frame, and a day boundary
is only meaningful in that frame - applying a Lagos offset to a value that is already
local would shift it twice and put the boundary in the wrong place on purpose. What
that costs, plainly: a user in another timezone gets their allowance reset at a moment
that is not their midnight. The remedy is a timezone concept this system does not have,
and it is open rather than settled.

**203. A movement out is stamped with the moment it was judged in, not with the clock.**
`WalletOperation` built its `Transaction` without a `created_at`, so the default in
`Transaction.__init__` supplied `datetime.now()` - and the daily cap is summed *from
that column*. Two frames, then: the ceiling computed from the `as_of` the caller was
handed, and the row written from whatever the clock said. In production they agree,
because every presentation passes `datetime.now()` as its `as_of`, and that is what made
it dangerous rather than benign: the disagreement was invisible until something supplied
a moment that was not the clock's, and it fails *silently*, because a day total that
misses a row reads as a smaller total rather than as an error. A plan run is that
something. `_limit_block` asks about the plan's own `as_of` day, so a tick replayed or
caught up for a day that is not today would have judged every one of its payouts against
a day that was always empty - the cap enforced, and never once binding. So
`WalletOperation.now` carries the moment and stamps the row, assigned beside `guard` at
both places that build an operation: `WalletService._run`, and `ExecutePlanRun`'s
`_move_the_money`, which builds its own because a scheduled run has no confirmation.
Two things keep it narrow. It is set only on the three outbound call sites in `_run` -
the ones that already passed `now`, because only a movement *out* is judged against a
day - so a deposit or an internal move still stamps the clock and behaves exactly as
before. And it is not a new convention: every other use case here that is handed an
`as_of` stamps what it writes with it, which is what makes a replayed tick compose the
same rows (see `NotifyUpcomingRuns`, whose receipts are stamped with the tick's moment
for precisely this reason). The template was the outlier, and the daily cap is the first
thing to depend on the rule it was breaking.

**The profile is also the second thing `errors._grade` was asked about in a week**
(decision 182), and this one needed no change: `TierLimitExceededError` is a
`MoneyError` that is in none of `UNAUTHORIZED`, `NOT_FOUND` or `CONFLICT`, so the
fallthrough that was already documented as "the safe direction to be wrong in" turns
it into a 400 carrying the error's own name and message. No translation was written
for it, and that is the point - the grade was right for input a person supplied, which
an over-limit withdrawal is.

### Signing up with Google, and the identity somebody else vouches for

This slice builds the last unbuilt clause of the MVP sentence - *"A person **signs
up with Google**"* - and it is the first bullet of Phase 2c. Very little of the
*shape* was new: `users.google_subject` has existed since Phase 1 with a comment
explaining why `NULL` and `''` must stay distinct, `UserRepository.find_by_google_subject`
has existed for as long, `LogIn._settle` already handled an account with no
credential, and `RequestPasswordReset` was designed around the fact that a Google
account would be exactly that. What did not exist was any verification of anything,
any setting, any route, any CLI command, and any library capable of checking a
signature.

Two decisions were taken with the person who asked for this before the plan was
written, and they frame everything below: **the id_token arrives in a request body**,
and **an address that already has an account is refused rather than linked**.

**The security chain this closes is worth stating first**, because it is the reason
the feature is shaped the way it is rather than the other way round. An unverified
Google address must never become a `User.email`, and the reason is a path through
code that already shipped: `RequestPasswordReset` mails a code **to whatever address
an account holds**, with no credential check, precisely so a credential-less account
can set a first password. An address stored without Google having proved it would
therefore be an address this system would mail a working reset code to, and whoever
controlled that mailbox would take the account. `email_verified` is load-bearing
rather than hygiene, and its refusal lives in the use case as a named error rather
than being folded into the verifier, so the reason is greppable from the flow it
protects.

**204. Two use cases, not one create-or-log-in endpoint.** `SignUpWithGoogle` and
`LogInWithGoogle` are separate modules with separate routes, and the argument is the
one that produced Phase 2a: `SignUp`'s docstring records that `ResolveUserByEmail`'s
find-or-create *"created an account as a side effect of a caller naming it"*, and
that replacing it with two separate acts is the whole of that phase. A single Google
endpoint would reintroduce exactly that, with the extra cost that "the address was
free" and "the proof held" would collapse into one response a client could not read.
Two use cases also keeps the 409 legible: it can only come from the sign-up half,
where the caller was asking to create something.

**205. The two routes mirror `POST /users` and `POST /sessions` exactly, including
that signing up does not sign you in.** `POST /users/google` answers 201 with a
`UserOut` and no session; `POST /sessions/google` answers 201 with a token. The cost
is that a first-time client sends the same token twice, which is cheap because a
token is reusable for its lifetime; the benefit is the one `POST /users` already
argues - the address being free and the proof holding stay two facts. `LoggedIn` is
the same frozen value `LogIn` returns, so `translate.session_out`, the CLI's
`_start_session` and everything downstream of them work unchanged.

**206. The audience is checked against our own client id, and this is the whole of
the OIDC confusion bug.** A token that Google issued to *any other application* is a
valid Google token. Without `audience=settings.client_id`, every Google-integrated
product on the internet is a credential for this one. It is one keyword argument in
the adapter, one line in the fake, and one test - which is exactly why it is worth a
numbered decision rather than being left as an argument in a docstring, since the
failure it prevents is invisible from every angle except the one it comes from.

**207. `algorithms=["RS256"]` is pinned, never inferred.** PyJWT will happily read
the algorithm out of the token's own header if you let it, and `alg: "none"` is the
single most common JWT verification failure in the wild - a token that says "no
signature" and a library that agrees. Pinning the list is what refuses it, and it has
its own test.

**208. The subject is the key and the address is a record.** `LogInWithGoogle` looks
up `find_by_google_subject` and never consults the address on the token. A subject is
stable for the life of a Google account, while the address on it can change - so an
account found by address would be unreachable the moment its owner changed their
Gmail, and worse, an account found by address is an account whose stored address a
later token could silently re-point. So the address a token carries is used at the
sign-up and never again, which makes a Google account's stored address a record of
what it was rather than a key to it. This is the decision the Phase 1 groundwork was
built for.

**209. A duplicate address is a 409 and is never linked automatically.** If somebody
already holds the address, `DuplicateEmailError` comes back and nothing is joined: a
caller proving they hold a Google account has proved nothing about the account that
already exists here, and linking would let whoever controls the Google identity take
over an account they never authenticated against. The remedy is named in the refusal
- use another address - and the person who has both keeps using both. That is a real
cost and it is the price of not having an account-takeover path that reads as a
convenience. A duplicate *subject* is a different 409 with a different remedy
(`DuplicateGoogleSubjectError`, log in), which is why the two are not one class.

**210. `email_verified` is normalised by the adapter and refused by the use case.**
Google has shipped this claim as both a boolean and the string `"true"`, and
`bool("false")` is `True` - so the adapter accepts either spelling and yields a real
`bool`, and `GoogleIdentity.__post_init__` refuses anything that is not one. The
division is `PaymentProvider.outcome_for`'s: **the port reports facts and the use
case applies policy**, which is what lets the takeover argument above sit in the
layer whose comments can be read alongside the reset flow it protects. The port
cannot express "I could not reach Google" as a `GoogleIdentity`, so it raises:
`InvalidGoogleTokenError` for a token that does not verify, `GoogleProviderError` for
the case where nothing could be asked at all.

**211. The verifier is built once and kept on `app.state`, unlike every other
collaborator the identity routes use.** The contrast is with the SMS channel, which
is built per request, and it is not stylistic: `jwt.PyJWKClient` fetches Google's
signing keys, caches them and rotates them itself, so a per-request verifier would
re-fetch the JWKS on every sign-in - slower, and a way to be rate-limited by a
provider whose keys have not changed. It is `payment_provider`'s shape rather than
the channel's, and `create_app`'s docstring carries the argument where the
asymmetry is visible.

**212. The address is still minted here, so `refuse_unusable_email` applies exactly
as it does in `SignUp`.** The same pair in the same order - shape before usability,
so `not-an-address` is not reported as an unusable one - and `emailAddress.py`'s
header was corrected from "three call sites" to four, because a rule written down
once is only worth having if the count of places it binds is kept true. The guard
site here is the odd one of the four: nothing is *typed*, and the account is created
all the same.

**213. The CLI reuses `_prompt_code` rather than growing a twin.** `signup-google`
and `login-google` read the token from a hidden prompt, never from a `--id-token`
flag, and the argument is `_prompt_code`'s own written out one provider in advance:
an argument lands in the shell's history file, in `ps` output for the duration, and
in whatever the terminal is recording. An id_token is a better fit for that
paragraph than the mailed codes it was written for - it is a live bearer credential
for about an hour and the whole of the authorisation for the call - so the function
gained a sentence and two callers rather than a copy. The stripping it does is right
for the same reason it is right for a code and wrong for a password: a token is
minted by a machine, so every character in it is Google's rather than the typist's.

**The redirect flow is deliberately not built, and the reason is a setting this
system has already decided not to have.** An authorization-code flow needs a
`redirect_uri` - a public address for *this installation* - and there is no such
setting, on purpose: `passwordResetMessage` argued that "any URL here would be
invented - right on one installation and wrong on every other, and a promise no code
keeps." Nor is there a browser front-end to redirect back to. So the verification
core built here is the whole of what a redirect would need underneath it, and the
redirect becomes a thin adapter over it in Phase 4, where TLS and a web front-end
exist. The cost of not having it is that a person obtains a token from Google's OAuth
2.0 Playground or their own client - which is why both CLI commands say so rather
than pretending to be self-sufficient.

**What is recorded rather than closed: an id_token is replayable for its lifetime**
(about an hour). Nothing here can prevent it - the honest defences are TLS, which is
Phase 4, and short lifetimes, which are Google's - so it is in `### Still open`
rather than in a comment claiming the hole is shut. And **rate limiting gets more
urgent rather than less**: this slice adds two unauthenticated writes that call out
to a third party, which makes them the second and third members of the list
`POST /password-resets` already leads.

### Refusing a caller who is asking too often

The last control Phase 2c owed, and the one the `### Still open` entry below was
written for. Seven routes now refuse a caller who has asked too often, the refusal
is a 429 in the house shape, and the counters survive a restart.

**The seven are the doors where one request makes this installation spend
something**, and that - not "the unauthenticated writes" - is what decided the list.
`POST /phone-verifications` mints a real SMS charge per call; `POST /password-resets`
and `POST /users/me/email-changes` each put an SMTP round trip in flight;
`POST /users` and `POST /sessions` each pay an argon2 hash at deliberately painful
parameters; and `POST /users/google` and `POST /sessions/google` fetch a third
party's signing keys. The three `*/confirm` routes are deliberately left alone -
decision 231.

**214. Two layers: a hot counter in this process, and a cold one in SQLite.** The
hot layer is a dict behind a ``threading.Lock``; the cold layer is a `rate_limits`
table written by a background flush. The lock is load-bearing rather than
decorative, and it is the rule `claude.md` states directly - never assume a check
followed by an update is atomic. **Every endpoint here is a plain `def`**, so
FastAPI runs it in its threadpool and several requests are inside the counter at
once; the read, the comparison and the increment are one critical section because
that is the only way the limit means what the table says. Splitting the two layers
this way is what makes the request path affordable: a write per request would make
the limiter the most expensive thing on the route it was added to protect.

**215. The hot layer answers alone, so the database is never on the request path.**
Every limit decision is made from the dict, which means a store that is slow, locked
or gone stops the counts *persisting* rather than stops the limiting. That is the
point of the arrangement rather than a side effect: what is lost when the cold layer
is unhealthy is durability across a restart, and what is not lost is the limit. The
same property makes the failure degrade in the safe direction - a limiter that kept
working from memory is a limiter, and one that refused everything because it could
not reach SQLite would be an outage wearing a security control's clothes.

**216. The key is the subject the request names, plus a per-route installation
ceiling, and there is no source dimension yet.** The subject is the address in the
sign-up body, the address or number a login named, the mailbox a reset asks about,
the number a text is sent to, or the account a change belongs to. **Folding is part
of the key rather than a step before it**: `fold_email` and `fold_phone` are applied
first, so `Ada@example.com` and `ada@example.com` are one budget and `0801 234 5678`
and `+2348012345678` are one handset - without that, a budget is escaped by typing
it differently. The domain's `checked_phone` is deliberately *not* used: it raises,
and a limiter that could raise would answer a request the domain answers with a
sentence about credentials with a 500 instead.

There is **no IP address and no `X-Forwarded-For`**, and that is a decision rather
than a phase not reached yet. Until a reverse proxy this installation controls is in
front of it, every such header is a string the attacker chooses, so a limit keyed on
one is a limit the attacker sets. The per-subject half is meanwhile the half a CDN
*cannot* do - Cloudflare sees an IP and cannot read an address inside a request body
- so the two compose rather than replace, and the source dimension arrives later as
a third column on the same table.

**217. The Google pair is ceiling-only, and that is a finding rather than an
omission.** Its subject would be the Google `sub`, which is not known until the
token has been verified - and verifying it is the cost being defended. There is
nothing to key on before the money is spent, so the ceiling is the only lever that
exists, which is why those two routes have no per-subject budget and it is written
down rather than left as a gap somebody would later read as an oversight.

**218. Fixed windows, and the burst at a boundary is 2× the policy limit.** An entry
carries the instant its window opened, and a check at or past the window's length
later opens a new one. The known cost is that a caller can spend a full budget at
the end of one window and another at the start of the next, so the true worst case
across a boundary is twice what the table says. Accepted, and named here rather than
hidden, for two reasons: the installation ceiling is enforced on a window of its
own, and the upgrade is a known one - a sliding window counter that keeps the
previous window's count and weights it by how far into the current one the caller is
- rather than a redesign. It is written down so the number in the table means a
*rate*, not a ceiling on a burst.

**219. A refused request still costs.** The count is spent by the attempt, not by
the success. This is the rule that decides whether the limiter bounds anything at
all: a counter that rolled back its increment on refusal would let a caller try
forever at exactly the limit's rate, so the budget would bound how many requests
*succeeded* and nothing about how many were made - which on `POST /users`, where
only the first can ever succeed, is a bound of one. It is the same position
`wallet_operation` takes when it records a refused attempt as a `FAILED` row rather
than discarding it, and `test_rate_limits.py` asserts it as a sequence rather than
as a status: five attempts at one address spend the budget with four of them failing
for a *different* reason, and the sixth is the first refused for asking too often.

**220. The two dimensions count different things, and the asymmetry is chosen.** A
subject's budget is spent by that subject's own requests and by nothing else; the
ceiling is spent only by work the installation actually admitted. So **a subject
that has exhausted its own budget does not spend the installation's, however hard it
keeps trying** - which means one abusive caller cannot lock the feature for everybody
by hammering, and that is the failure a naive shared counter produces. The price is
on the other side and is harmless: a request refused by the ceiling has already
spent a subject credit, and the caller could not have done the work either way. The
ordering in `rate_limits.enforce` is what produces both halves, and the observable
is one row apart in the store - six for the address that kept trying, five for the
installation - which is what the test asserts.

**221. The cold layer is written in deltas, never totals.** Each entry tracks how
much of its count the store has already been told about, and a flush writes the
difference. **This is the part that decides whether the limiter survives a second
worker.** Two processes each writing their totals would overwrite each other and the
stored number would be one worker's rather than both; summing totals would re-add
everything on every flush. With deltas, summing across workers *and* across restarts
gives the true count. What is *not* claimed is that several workers enforce one
budget - each holds its own hot counter, so the effective installation limit is up to
N times the policy limit and a refusal by one worker is not seen by another. A
deployment needing the exact number has to route a subject to one worker or move the
hot layer to something shared, which is what Phase 5 names Redis for.

**222. Over-counting is the safe direction and under-counting is not, and the flush
is ordered so that failures land on the safe side.** The deltas are marked as
written only *after* the commit returns, so a write that fails leaves them unmarked
and the next flush offers them again - the count is late rather than lost. The one
window where accuracy can suffer is a crash between a successful commit and the mark,
and it costs accuracy by counting those increments twice. That is the direction
nothing here is allowed to fail in: a stored count that is too high refuses somebody
who has done less than the policy says, which one window of waiting repairs, while a
count that is too low is a limit that is not the limit.

**223. The sweep and the writes share one transaction, and that is not tidiness.**
A sweep that committed separately could delete a row between this process reading it
and writing to it, and the write would then resurrect a count from a window that had
just been swept - with the original increment gone, so the number would be this
process's *delta* presented as a total. One unit of work makes the pair atomic. The
sweep itself is not housekeeping: one row per subject ever seen is unbounded, and a
limiter keyed on caller-supplied identifiers is therefore a table an attacker can
grow, which makes it a disk that fills rather than a limit that holds. Retention is
computed from the policy table rather than chosen - `rate_limits.longest_window` -
because a row swept while its window was still live would hand its subject a fresh
budget in the middle of their window, which is precisely what the cold layer exists
to prevent.

**224. The flush is a background thread on a lifespan, with a warm start at boot and
a final flush at shutdown.** This is the first lifespan and the first thread in
`app/`, which is why it is called out rather than folded in quietly. `RATE_LIMIT_FLUSH_SECONDS`
defaults to **thirty** and `0` builds no flusher at all - and the default is
positive rather than opt-in because one of production's two call sites is
`uvicorn ...:create_app --factory`, which cannot pass a keyword, so an opt-in default
would silently disable durability for the documented invocation. The HTTP test suite
is the side that opts out, in `tests/conftest.py`, because a thread per test
application would be hundreds of them and each performs a real write at shutdown.
`stop` flushes once more **on the calling thread**, so a process that has returned
from it knows the counts are stored rather than knowing a thread was asked nicely.

**225. A warm start that fails does not stop the application.** `warm` reads the
store once at boot and adopts what the last process flushed, so a restart is not a
way to get a fresh budget. The read happens inside a unit of work like every other
database access here, even though it only reads. If it raises, the application
starts anyway and records the failure on `app.state.rate_limit_warm_error`: a
database that cannot be read already serves `/health`, and refusing to boot over a
counter table would take the money down to protect the rate limiter. What the
failure costs is durability across *this* restart, which is the smaller of the two
evils available. There is no logging in `app/` yet - the MVP list carries that as
its own item - so the attribute is the only observable, and the flusher keeps
`last_error` and `last_report` for the same reason.

**226. The limiter is a dependency and not middleware, and the reason is the
subject.** The subject is a field in a request body, which middleware would have to
consume and re-inject; re-reading a body after it has been parsed is a known trap,
and `dependencies.py` is already the one place a request's identity is read. The
limiter declares the endpoint's own pydantic model as its own parameter, and FastAPI
shares one parse of the body between the two.

**227. A body that fails validation spends no credit, and that falls out of where
the limiter sits rather than out of anything it does.** FastAPI validates a
dependency's declared body while *solving* the dependency and skips the call
entirely when that solve produced errors, so a request with no `password` field
never reaches the limiter. The opposite arrangement is easy to write and hard to
notice: a limiter that read the raw body would charge for a request the route never
ran, and a client with a bug in its serializer would walk itself into a 429 while
making no valid request at all. Pinned in `test_rate_limits.py`, because it is a
property of a framework mechanism rather than of any line in this repository.

**228. On the one limited route behind a session, the session is resolved first.**
`POST /users/me/email-changes` takes the limiter as a dependency that itself depends
on `current_actor`, so a bad token is a **401** and never a 429. Two things are ruled
out and they are different in kind: a 429 to a caller who has not proved who they
are would answer a question they have no standing to ask, and - because the subject
this route counts is the *account* - it would show a stranger a status about
somebody else's budget. Worse, a budget spent by unauthenticated requests would let
anybody exhaust a named account's allowance without ever holding its token, which
turns the limiter into the denial of service it exists to prevent. The test drives
the route past its budget with a nonsense bearer token and asserts every answer is a
401, because the ordering is the claim.

**229. On `POST /password-resets` the unknown-address arm is counted too, and this
is the invariant that makes the limiter safe to put on that route at all.** Decision
184 answers identically for a known and an unknown address so the bytes are not an
enumeration oracle; a limiter that counted only the arm which found an account would
make 429-versus-202 a statement about whether the address exists - asked three times
and then answered - and the leak would have been *introduced* by the control meant to
protect the route. That it cannot happen is a property of the placement: the limiter
is asked as a dependency, so it runs before `RequestPasswordReset` is reached and
therefore before the use case can return early on the miss. Had it been a check
inside the handler body, after the branch, the two arms would carry different budgets
and the status would be the oracle. `test_rate_limits.py` asserts it directly, by
driving both arms past the budget and requiring the two sequences to be *equal*
rather than each to be correct - which is why that test runs on an installation with
mail configured: on one without, both arms raise before the address is read and the
distinction could not be observed at all.

**230. 429 is a new grade, carries `Retry-After`, and says nothing about which limit
was hit.** The header is the caller's own remaining window rather than a constant,
rounded **up** so that a client which obeys it is not refused a second time for
having obeyed; `Retry-After: 0` would invite an immediate retry, which is the
opposite of what the response is for. The body is the house shape with a deliberately
uninformative detail, and that vagueness is load-bearing: a body naming the policy
would describe the shape of the defences to whoever is probing them, and on
`POST /password-resets` it would hand back the known-versus-unknown distinction
decision 229 exists to keep. `ApiError` gained an optional `headers` mapping so this
one class could carry the header without the handler learning its name; the header's
*absence* from every other refusal is asserted too, since that is the risk the seam
introduces.

**231. The three `*/confirm` routes are deliberately unlimited.** The mailed and
texted codes are 256-bit CSPRNG values rather than six digits - decisions 167 and
the phone flow's own - so there is nothing to guess and nothing to slow down; what a
limiter there would bound is one indexed lookup. This is a decision rather than an
omission, and `test_boundary.py` asserts that they carry no limiter, so overturning
it means failing a test that says why it existed. **If a code ever becomes short
enough for a person to read aloud, this is the first decision that has to be
revisited** - and the failure would be silent, because the route would go on
answering correctly.

**232. This is not the velocity control over money movement, and that one is still
owed.** `transaction_repository.py` already records a second, different control: a
bound on how often money may *move*, which counts ledger rows rather than requests
and needs a read of its own. Nothing here closes it, and the `### Still open` entry
below says so rather than implying the item is finished. A limiter counts requests;
a velocity control counts value, and the two would disagree about a caller making one
enormous payout.

**What is recorded rather than closed: the reset flow still leaks account existence
by latency.** Decision 189 stands and the limiter does not answer it. Three requests
an hour per address is a budget an attacker can still spend - a dozen addresses is
thirty-six measurements an hour, enough to distinguish a round trip from no round
trip if the difference is visible at all. What the limit does is make the attack
slow and leave a trace rather than make it impossible, so the follow-up is owed and
nothing about this slice should be read as having paid it.

### Collecting in the currency the wallet holds

Second on the MVP list, and the first finding of the entry-point audit to be closed.
`PaystackPaymentProvider` sent `_subunit(amount)` - a *number* derived from the `Money` -
beside `"currency": SUPPORTED_CURRENCY`, a hard-coded `"NGN"`. One payload, two
decisions, and nothing connecting them.

**233. The correction to the record comes first, because it changes what the fix is
for.** The audit entry struck through below says a USD wallet depositing
`Money(100, USD)` posts `{"amount": 10000, "currency": "NGN"}` and that "the ledger row
credits the wallet with the amount it *intended*". The first half is right and the second
is not, and the difference is the whole shape of the bug. What actually happens:

- `settle_payment.py:97` is `if row.amount != outcome.amount`, and it refuses to move
  anything.
- `routes/webhooks.py` reads the outcome's currency off `data["currency"]` - the field
  the *provider* reports, which says `"NGN"` because that is what was sent.

So the row asks for `Money(100, USD)`, the webhook reports `Money(100, NGN)`, the two
disagree, and `SettlementOutcome.AMOUNT_DISAGREES` comes back with the row left PENDING.
**The payer is charged ₦100 and nothing is ever credited** - not the intended amount, not
the wrong one. And because the row stays PENDING, the reconciler re-reports the same
disagreement on every run, which the entry below already describes as reporting "for ever
rather than resolving it". Both halves of the audit's sentence were wrong in the same
direction: "a hundredth" read the kobo factor as a hundredth of *value*, and "credits the
wallet" credited a ledger that in fact refuses. It is struck through rather than edited,
per this file's habit, because the correction is the interesting part of it.

**234. The guard goes at the deposit door, and that is a deliberate departure from where
the checklist put it.** The order entry said "one check at wallet creation". Creation is
the wrong door, for three reasons, and the first is decisive:

- **A USD wallet is fundable today, by a path that works.** `WalletService.deposit` (into
  `DepositMoney`) credits a wallet directly with no provider in the room, and the CLI's
  root `deposit` verb is exactly that door. Restricting creation would close a working
  door in order to close a broken one.
- **"We cannot *hold* USD" and "we cannot *collect* USD" are different claims.** `Money`
  accepts five currencies, `tier.py` carries a limits row for each, and pots and plans
  work in each. Only the rail is naira-only.
- **It would make a live property untestable.** `test_plans.py:327` and
  `test_cli_plans.py:364,1492` each open a USD wallet to prove that amounts are read in
  the *wallet's* currency rather than the request's, which is the only end-to-end way to
  have a second currency at all. Those three tests are unchanged, and **their staying
  green is the assertion that creation was not restricted** - a claim no test written for
  this slice could make as directly.

So the refusal lives in `InitiateDeposit._prepare`, the last moment at which refusing
costs nobody anything, in the same door the closed-wallet and balance-cap checks already
stand in.

**235. The port gained a fourth method, and it is not the `initiate_transfer` mistake.**
`PaymentProvider.supported_currencies() -> frozenset[Currency]`, abstract. The port's own
docstring argues that `initiate_transfer` is deliberately absent because its signature
"would have been guessed", so a fourth method is the kind of thing that needs a reason
rather than an assertion. The reason is the test the third method already passes: **it
has a caller that exists today** - the deposit door, which must refuse *before* it sends
a payer to a payment page - and a signature *derived* from that caller rather than
imagined. The alternative was a copy of the fact in the application layer, and **a copy
is a thing that drifts**, in the one direction that matters: the door would admit a
currency the transport silently relabels.

**236. `_from_subunit` had to move with `_subunit`, and that is forced rather than scope
creep.** Once a collection can legitimately be opened in USD, the verify response comes
back saying `"currency": "USD"` - and a `_from_subunit` that parsed every answer as NGN
would make reconciliation refuse a perfectly good row, which is this same bug with the
sign flipped. The two directions of one conversion are one decision, so the currency
became a parameter of both; `outcome_for` reads `data["currency"]` - the same field
`routes/webhooks.py` already reads - and a value `Currency(...)` does not know is
translated into `InvalidProviderAnswerError` rather than escaping as a bare `ValueError`
and reporting as a 500.

**What the suite found, and it is the argument for that paragraph rather than a footnote
to it.** The first full run after the change was 3,202 passed and **3 failed**, all three
in `test_cli_reconcile.py` and all three with the same stdout line: *"could not ask: the
provider reported a settled charge with no currency"*. The cause was not the adapter but
the double - `AnsweringPaystack` answers a verify lookup with the fields the adapter
*reads*, and its docstring said so in as many words ("the two fields the adapter reads"),
which is why it went red the moment a third one was added. That is a double behaving
exactly as a double should: it fails here rather than silently disagreeing with the far
end. **The tempting fix is the wrong one** - defaulting a missing currency to NGN would
have restored the original bug in a quieter form, and the refusal is the whole point. So
the double gained a `currency` field and a paragraph recording why. Worth writing down
because the three failures were the *precise* mirror of decision 236's prediction, found
by a run rather than by the reading that produced the prediction - and because a
reconciler refusing good rows for ever is the same failure this slice exists to close,
one door over.

**237. The supported set is a constructor argument rather than a settings value, and the
reason is that the alternative is untestable.** `PaystackPaymentProvider(secret_key,
timeout, *, currencies=SUPPORTED_CURRENCIES)` - a per-deployment fact of the same kind as
the key itself, and a `frozenset`, so it is not a shared-mutable hazard. Against a
provider that only ever collects naira, "the door asks the rail" and "the door hard-codes
NGN" are the *same behaviour*, and no assertion can tell them apart; building one that
collects two is what makes the question askable at all, and that is what
`test_the_rail_is_asked_rather_than_assumed` does. A settings value was the other
candidate and is rejected for the reason this file keeps giving: it would state the same
fact twice - once in configuration, once in the adapter that has to honour it - and the
two would disagree the first time somebody enabled a currency at the provider and forgot
the setting.

**238. 409, and the two grades it is not.** Not 400: the request is well formed and there
is nothing in it for the caller to fix. Not 503: this installation serves deposits
perfectly well against a wallet in the currency it collects. What refuses *this* request
is the wallet's own state, which is the shape the error table describes for 409 and the
row `WalletClosedError` already sits on. `CurrencyNotCollectableError` is filed in the
payments tree rather than beside `WalletClosedError`, because the money domain has no
opinion about what a rail can collect and filing it there would say it did.

**239. The currency guard sits between two refusals that were already there, and each
edge is load-bearing.** It sits after the wallet's own `CLOSED` check and after
`amount.currency != wallet.currency`, and both edges were chosen:

- After `CLOSED` because a closed wallet is the refusal with a *remedy* - give up on it
  and open another - while a currency the rail cannot collect has none, and answering the
  actionable fact first is the ordering `errors` already uses to put 401 above 404.
- After the mismatch check because that one has its own sentence: a dollar amount sent to
  a naira wallet is the caller's typo and is correctable, and it would be told the wrong
  thing if the rail's answer arrived first.

`test_the_amount_is_weighed_against_the_wallet_before_the_rail` and
`test_a_closed_wallet_answers_with_its_closure_and_not_its_currency` pin the two edges,
because a test that asserted only "it refuses" would pass with the branches reordered.
The refusal names both the wallet's currency and the ones this installation *does*
collect, because the first half tells a caller what they already know and the second is
the half they can act on.

**Verified against a socket, and the two answers are the pair that makes it a
verification.** One `uvicorn` process, one throwaway key, a wallet in each currency: the
USD deposit answered **409 `CurrencyNotCollectableError`** with the sentence naming both
the wallet's currency and the ones this installation collects, and with **no
`authorization_url`** in the body; the NGN deposit on the same server at the same moment
answered **400 `InvalidPaymentIntentError`** - the key being a throwaway, so Paystack
refused it at the far end. Same process, same configuration, same instant: one path
contacted the rail and the other did not. That is the guard proved to run *before* the
call rather than after it, which is the one thing a test asserting `requests == []`
against an in-process double cannot say on its own. The second call is also the control
the first needs - a guard that refused everything would produce the 409 and nothing else,
and the 400 is what says the door still opens for a currency the rail collects.

**What this does not close.** Four things, named rather than left to be inferred:

- **Rows already stuck PENDING from this bug are not migrated.** After this change no new
  one can be created; the old ones go on being reported by the reconciler. What to do with
  money that was collected against a row that can never settle - refund it, credit it at
  the rate it arrived at, or write it off - is a policy question with a refund policy
  attached, and it belongs to the item that builds the reconciler's first remedy rather
  than to a currency guard.
- **The reconciler still re-asks for ever about a row that can never agree.** Adjacent,
  separate, and already recorded in `### Still open`.
- **Wallet creation stays unrestricted**, deliberately, for the three reasons in 234. A
  wallet this rail cannot collect is still creatable and can still be funded by
  `WalletService.deposit` and by any future rail.
- **Payouts are untouched.** `initiate_transfer` is deliberately not on the port, so there
  is no outbound rail for a destination or a payout currency to be checked against. When
  one exists, this is the decision it will have to answer to.

### Failing to reach the provider is not failing to ask it properly

The first slice of the client's work, and it began where this file keeps saying it must: with a
reading of the real-money path rather than with the client. Building a UI on top of the deposit
route means writing code that has to decide what to *show* a payer when a call fails, and reading
`_request` to find out what it says turned up a grade that was wrong.

**240. The provider-failure grade was a 400, and the sentence it produced sent payers to edit a
form.** `errors._grade` grades by falling through: 503 for `UNAVAILABLE`, 401, 404, 409, and **400
for everything else**. `PaymentProviderError` was in none of the four lists, so every failure of
every outbound call arrived as *"there is something unacceptable in your request"*. Three of
`_request`'s four failure branches are not that:

- `paystack_payment_provider.py:522` - `httpx.HTTPError`, which is **a timeout as much as a dead
  network**. The single most likely way a real deposit fails.
- `:527` - `status_code >= 400 and not in answers`, which is written as though it meant "the
  provider refused" and also catches **Paystack's own 5xx**.
- `:534` - a `200` whose body is not JSON, which is a proxy in the way.

A refusal is a fact about the request: it will be refused again, and the caller has something to
change, so a 400 is right for it and stays. The other three are facts about the *rail*: the request
may be perfectly good, the same request may work in a minute, and there is nothing for the caller to
correct. The cost of collapsing them was not cosmetic. **A client that read a 400 would not retry** -
the grade is exactly the signal a UI branches on - and a timeout on `/transaction/initialize` may
well have left a collection open on the far end. So a payer whose money was already taken was shown
"your deposit request is unacceptable" and given a form to edit.

The repair is a split rather than a rewrite. **`PaymentProviderUnavailableError`** takes those three
branches and grades **503**; `PaymentProviderError` keeps the 4xx refusal and grades 400. The 5xx
check sits *before* `answers` is consulted, because a status this file was told to interpret is still
the provider breaking rather than answering something - none of the statuses `answers` names is a
5xx, and if one ever is, the provider being broken outranks it.

**241. The two classes are siblings rather than one inheriting the other, and that is what makes the
tests honest.** Making the new error a subclass would have kept every `except PaymentProviderError`
working - including `reconcile_payments.py`'s, and including eleven `pytest.raises` sites in the
adapter's own test file. It was rejected because a hierarchy makes the *wrong* assertion pass:
`pytest.raises(PaymentProviderError)` around a timeout would have gone on passing while claiming the
failure was a refusal. As siblings, every one of those call sites had to be read and re-decided, and
each is now a statement about which kind of failure it is. That is the same reasoning that keeps
`PayerEmailMissingError` and `PayerEmailRefusedError` apart, and the same one that puts
`CurrencyNotCollectableError` in this tree rather than beside `WalletClosedError` (decision 238).

The precedent for the grade was already in the table and had been for a while: **`GoogleProviderError`
is in `UNAVAILABLE`** for exactly this shape - a provider configured correctly and unreachable. The
new member is that one provider over. A 502 was considered and refused: this codebase's 503 means
"this installation cannot serve *this* right now", which is what a dead provider means here, and a
second upstream-shaped grade would be a distinction no caller acts on differently.

**242. A real deposit now leaves a trace, and it is the front edge of i17 rather than i17.**
`app/` imported no `logging` at all, which checklist item i17 records and which is a strange state
for a service that takes money: a deposit leaves a trail at the far end - a transaction in the
Paystack dashboard with a reference on it - and nothing on this side produced a line an operator
could put beside it. What is added is deliberately narrow:

- **`paystack_payment_provider.py`** - `info` when a collection is opened (the reference, and the
  name the provider files it under), `warning` for each of the four failure branches, `warning` on
  the socket failing before a request is sent.
- **`routes/webhooks.py`** - `info` for every signed event that reached settlement, with the
  outcome and the reference; `info` for an event this installation does not settle; `warning` for a
  signature refusal.
- **`app.py`** - one line at boot, for the reason 244 gives.

**The rule is the reference and the outcome, never the payload and never the payer.** Payloads are a
third party's text and `_request`'s docstring already argues at length against quoting them; the
payer's address is the one field of theirs this system holds in the clear and there is no reason for
it to reach a log. The reference is the join between this ledger and Paystack's dashboard, which is
what makes it the field worth carrying. Structured logging, a level policy, and somewhere to ship
these lines are i17 and stay open; nothing here configures anything or adds a dependency, and the
lines inherit whatever the process configured - uvicorn in a deployment, the root logger in a test.

**243. `webhooks.py` promised a distinction that lived "in the server's log", and there was no
server log.** The route's docstring has said since it was written that an absent signature and a
wrong one are one response because *"which of the three it was is a distinction that exists in the
server's log and nowhere in the response"*. That was a claim about a facility that did not exist. It
does now, and it is the narrowest line that keeps the promise: **whether a signature was presented
at all**, never the body, never the header's value, never a comparison of the two. "Absent" and
"present but not this installation's" is the whole of what is knowable - an altered body and a
wrong signature are the same fact from here - and calling those one thing would put an operator back
where they started.

**244. The deployment says once, at boot, whether it can take real money.** Paystack's sandbox and
its live service share a host, a set of endpoints and every line of this code; a deployment is in
one mode or the other solely by which secret key was exported. A live key exported into a staging
shell takes real money from real cards while the dashboard, the logs and the tests all look like
testing, and the operator finds out from a card statement. **It is the only configuration mistake in
this system that is both silent and expensive**, which is why it is the one thing `create_app` prints.

`PaystackSettings.mode` reads the key's `sk_test_`/`sk_live_` prefix and returns `"test"`, `"live"`,
or **`"unrecognised"`** - a third answer rather than a guess, because the guess it refuses is the
dangerous one: defaulting an unknown prefix to `"live"` puts a false alarm in every operator's log,
and defaulting it to `"test"` says "no real money here" about a key nobody has checked. **Nothing
branches on the value.** A live key does not stop the server starting, because a live key is the
correct state of a production deployment and refusing would take the money down to protect it. The
unrecognised case is the only `warning`, because it is the one where the answer to "are we taking
real money" is *nobody can tell from here*.

**245. What the split hands the client.** Once a timeout means "the far end may have acted", the
retry rule changes, and this is the deposit route's consequence rather than the adapter's: **the
client should send its own `ref`.** `POST /wallets/{id}/deposits` defaults the key to a fresh
`uuid4()` when the body carries none, so a client that retries after a 503 opens a *second*
collection while the first is still live - and the payer meets two checkouts for one intention. With
a reference of its own, the retry lands on `DepositAlreadyInitiatedError`, which is a 409 naming a
row that exists rather than a second row that should not. The route is unchanged; what changed is
that the adapter's refusal to say "nothing happened" is now visible to whoever writes the client.

**Verified.** `python -m pytest tests/ -q`, with the pair in
`tests/presentation/api/test_deposits.py` as the assertion that matters: a provider that cannot be
reached answers **503 `PaymentProviderUnavailableError`** and a provider that *refuses* still answers
**400 `PaymentProviderError`**, both driven through the real route against the real error handlers.
The second is the control without which the first proves nothing - a 503 for everything would pass
it and be exactly as wrong in the other direction, sending a caller to retry a request that will be
refused identically for ever. In the adapter's own file the split is pinned at the level below:
a 500, a timeout, a `ConnectError` and a non-JSON body each assert the new class for `initialize`
*and* for the lookup, because the reconciler's most common real failure is a cron run on a machine
whose network is briefly down, and a job that logged that as a bad row would send somebody looking
at the ledger.

**What this does not close.** The live test-key run - the thing this whole slice is a preparation
for - has not happened yet, and the assumptions it exists to settle are unchanged: whether the
webhook's `data.amount` is gross or net, whether `data.currency` comes back as sent, whether our
reference alphabet and its lack of a length rule survive the far end, and what the test key's
minimum amount is. None of those is answerable from this repository, and every one of them is a
sentence in this file that a run would either confirm or correct. Also unchanged: the balance cap is
still not consulted at settlement (i4, a policy question), abandoned charges still accumulate as
PENDING rows the reconciler re-asks about for ever (i10), chargebacks are still acknowledged and
ignored (i9), and there is still no rail out (i8).

**246. `BUDGET_DB` moved the API's database and left the CLI's where it was, and decision 71 had
already claimed otherwise.** Found the moment a live run was actually attempted: the CLI registered
an account and opened a wallet, the API was pointed at the wallet, and `POST
/wallets/{id}/deposits` answered **404 about a wallet that plainly existed**. Nothing was wrong with
either program. They were reading two different files.

`settings.database_path()` reads `BUDGET_DB` and falls back to `DEFAULT_DATABASE_PATH`, and it was
wired into exactly one caller - `create_app` at `app.py:280`. The CLI's `--db` took
`default=DEFAULT_DATABASE_PATH`, the bare constant, and `cli.py` did not import `database_path` at
all. So on any machine where the variable was set - a deployment, or a run deliberately keeping a
throwaway database away from the repository - **the two presentations resolved to different files**,
which is the precise thing decision 71 was written to prevent. That decision says, in as many words:
*"The same constant, `DEFAULT_DATABASE_PATH`, is now the default for both the CLI's `--db` and the
API's `create_app`, so the two presentations resolve to one file rather than to two identical string
literals that could drift. `BUDGET_DB` overrides it."* The first clause was true and the last was
not, and the gap between them is this bug.

**The failure mode is worth stating because it looks like nothing.** A constant cannot read an
environment variable, so the CLI happily wrote a wallet to `./budget.db` while the API read
`$BUDGET_DB`; the CLI then printed a wallet id the API had never heard of. There is no exception, no
stack trace, and no two log lines that disagree - each process is correct about the database it is
holding. What a person sees is a 404 on a resource they created seconds ago, which reads as a bug in
the wallet lookup or in session resolution rather than as a configuration split.

**The comment beside the default defended the mistake, which is why it lasted.** It read: *"exactly
as `--db`'s own default is a constant that module owns"* - written to explain why `--session`, two
lines below, is defaulted from `configured_session_path()` rather than read inline. The reasoning
about `os.environ` was right; the comparison was the bug written down as a virtue. `--session` and
`--db` were never alike: one resolved through `settings`, the other named its constant, and the
comment asserted the resemblance that was missing.

**The fix is one line, and it is the same line `--session` already had.** `--db` defaults to
`configured_database_path()`, imported as `configured_database_path` for the same reason
`configured_session_path` is - so the parse-time read is still the single read `settings`'s docstring
promises, and *"this is the only module in the codebase that reads `os.environ`"* stays true. A typed
`--db` still wins, because a default loses to an explicit flag. The help text now names the variable
the way `--session`'s does.

**Why no test caught it, and why the new ones are shaped the way they are.** All thirteen CLI call
sites in the suite pass `--db` explicitly - through `run` in `tests/presentation/test_cli.py`, or
spelled out where a test builds its own argv - so the *default* was exercised by nothing at all.
A default that nothing exercises is a default that can be wrong indefinitely, and this one was wrong
for as long as it existed without a single red test. `TestWhereTheDatabaseComesFrom` therefore calls
`build_parser()` **inside** each test rather than in a fixture, because the default is resolved at
parse time: setting the variable after a parser exists would assert nothing. Three tests, and the
third is the control - the first two would both pass if the flag were being ignored in favour of the
variable, and the point is that an explicit `--db` still beats `BUDGET_DB`.

**What this does not change.** `DEFAULT_DATABASE_PATH` is still the fallback and still the constant
`settings` owns; the CLI still ships a working default on a machine with nothing configured. And
`tests/conftest.py`'s clearing is untouched - `BUDGET_DB` remains in `SETTINGS_VARIABLES`, so the new
tests rest on `monkeypatch.setenv` *after* that fixture has unset it, which is the only arrangement
in which they mean anything.

**The live run, in progress.** The test-key run of Stage 1a began on 2026-09-18 and has produced two
findings without yet reaching Paystack at all, which is itself the finding worth recording: *a run is
not only a way to ask the provider a question, it is the only thing that exercises the whole setup,
and both of these were about our own wiring rather than anything the far end does.*

The first was `BUDGET_DB` splitting the CLI's database from the API's - decision 246. The second:
with the CLI and API finally pointed at one file, `POST /wallets/{id}/deposits` answered **503
`PaymentsUnconfiguredError`**, because `PAYSTACK_SECRET_KEY` had been exported into the terminal
driving the CLI and not into the one running uvicorn. Two shells, two environments, one variable
each - and the failure is indistinguishable, from the caller's side, from a deployment that has
genuinely not been configured.

**That second one is decision 244 collecting on the argument it made.** The boot line was added on
the claim that a person should not have to infer an installation's payment state from a 503 on a
route they were told to call; on the run it was the only thing on either machine that said what was
wrong, and it had said it before the first request arrived. It is worth noting that the 503 itself
was exactly right and is not a defect - `PaymentsUnconfiguredError` is the correct grade for an
unconfigured install, the refusal lands at `dependencies.payment_provider` *before* the wallet is
read, so no ledger row was written and no payer was sent anywhere, and the route's docstring argues
that ordering is the feature. What the run confirms is not the grade but the sequence: the refusal
arrived before any read, which is the first time that claim has been observed rather than asserted.

What the run still owed when this paragraph was written was the `initialize` call, the checkout and
the `charge.success` that decides whether `data.amount` is gross or net. The first two have since
happened - see decision 247 - and the third has not.

**The third finding, and the first one that is about this code rather than about a shell.** With
uvicorn finally holding a key, the deposit reached Paystack and came back as
`InvalidPaymentIntentError` - *"the provider accepted the call but returned no transaction; the
secret key is the usual reason"*. Reading the path rather than the sentence: `_request` passes
``401``/``403`` through as an *answer* because ``AUTHENTICATION_FAILURES`` is in ``answers``, the
``status >= 400 and status not in AUTHENTICATION_FAILURES`` branch above therefore does not catch
them, and the ``data = response.get("data")`` check below is what fires. That check is reached by
**two facts that have nothing to do with each other**: a key the provider rejected, and a ``2xx``
that carried no transaction. Both produce this identical body, and the body is a ``400`` - so a
caller is told to correct their request when the likeliest cause is a configuration value.

**And the sentence is wrong in the case it was written for.** It opens *"the provider accepted the
call"*, which is true only of the second fact; on a ``401`` the provider rejected the call, said so
in ``message``, and this frame had both the status and that message in hand and discarded them. The
comment above the check asserts the reachability claim correctly - *"reached on a wrong secret key,
which is the common case by a wide margin"* - but nothing in the code carries the one word that
would let a reader tell the common case from the rare one. **The remedy is the same shape as
decision 243's**: this adapter now logs, and the two facts this branch cannot tell apart are exactly
the two an operator needs told apart, so the line belongs here. It is left unwritten for now because
the leading hypothesis is the dull one - the run was using a placeholder key - and a fix aimed at a
bug that a corrected key makes unreachable would be inventing a defect to have found one. What the
run has established is the *shape* of the gap: an installation configured with a bad key and an
installation whose provider answered an empty ``2xx`` are indistinguishable from the client, from
the ledger, and - as the code stands - from the log.

**The run's fourth observation is a confirmation rather than a defect, and it is worth its line.**
`budget.db: 1 wallet(s)` alongside `/tmp/live-run.db: 1 wallet(s)` is decision 246 caught in the act:
the first attempt's wallet really did land in the repository while the API was reading the throwaway
file, and the check printed both because both existed. The fix is what made the second attempt land
in the right one.

**And the third finding was then confirmed to be a real gap rather than a hypothesis, by the one
route that could get around it.** With a genuine `sk_test_` key, `curl` against
`/transaction/initialize` answered **200** with a complete `data` object - `authorization_url`,
`access_code`, `reference` - for the same account and the same currency the API was failing on. The
API, meanwhile, kept answering `InvalidPaymentIntentError`. The process was the reason: `provider_for`
resolves the adapter once inside `create_app`, so a server started with the placeholder key keeps
that key for its lifetime, and correcting the environment in another shell changes nothing about a
running process. That part is ordinary and needs no decision.

**What needs one is that the API could not say so.** The deposit's refusal was byte-identical to the
refusal a genuinely broken provider would have produced, and there was no way from the response, the
ledger or the log to tell a key Paystack had rejected from a `2xx` that carried no transaction. The
only reason the run got past it is that `curl` was pointed at Paystack *around* this system - which
is the same shape of workaround, in the same file, that decision 137's `invalid_character_in_reference`
story describes. **Both times the provider had already said the answer and this repository was the
only place it could not be read.** The remedy is now small and named: the branch that raises
`InvalidPaymentIntentError` holds `status` and the provider's own `message`, and the two cases it
cannot separate are exactly the two an operator needs separated. That remedy has since been written -
see decision 248 - because the run made the case for it faster than the run could finish.

**247. The deposit loop ran against Paystack, and `data.amount` is gross.** On 2026-09-18 the whole
path went end to end for the first time, against the test service - a wallet at
`96f71fe1-12ad-4b60-a06f-75911614579a`, a 100.00 NGN collection under the reference
`96f71fe1-12ad-4b60-a06f-75911614579a.run-1`, a healthy `POST /wallets/{id}/deposits` answering 201
with an `authorization_url`, the payer's checkout paid, and the row settled by the reconciler as
`charge_succeeded applied to a deposit`. The balance afterwards read `available: 100.00 NGN`. **The
number is the finding: not 98.50, not 99.25, not 100 less a processing fee.**

That answers assumption 1 of the run's table, and it was the one that could have invalidated the
entire rail. `settle_payment.py:97` refuses when `row.amount != outcome.amount`, so a provider
reporting the amount *net* would have made **every** real deposit an `AMOUNT_DISAGREES`: the payer's
money taken, nothing credited, and the row re-asked about by every subsequent reconcile run for
ever. **The suite could not have caught it**, and this is the cleanest example of why the run
existed: every webhook and lookup body in `tests/` was written by this repository, so a repository
that assumed gross would have gone on agreeing with itself indefinitely.

**Assumption 2 falls to the same settlement, because `Money.__eq__` compares the currency as well as
the number** - `self.amount == other.amount and self.currency == other.currency`. A `data.currency`
that came back as anything but `NGN` would have failed this comparison and the row would not have
credited. So the currency guard's output side is confirmed too: what was sent is what came back, and
the disagreement the guard exists to make unrepresentable did not occur.

**A second deposit was then read back with a bare `curl`, and the payload it returned settles
assumption 1 in the provider's own words rather than by arithmetic.** 50.00 NGN under
`96f71fe1-12ad-4b60-a06f-75911614579a.run-2`, paid at the checkout, and
`GET /transaction/verify/:reference` answering:

```
"status": "success",  "amount": 5000,  "requested_amount": 5000,  "fees": 75,
"currency": "NGN",  "domain": "test",  "channel": "card"
```

**`amount` is the gross 5000 kobo and `fees` is a field beside it.** That is the whole of assumption 1
stated as a fact about the payload: Paystack does not net the fee out of the amount it reports, it
reports both, and `requested_amount` agreeing with `amount` is the same thing said twice - which is
why both are quoted. The balance argument above and this are independent: one infers gross from our
ledger not moving by a fee-sized amount, the other reads gross directly off the far end's response,
and the second does not depend on our arithmetic being right.

**`domain` is a second source for a fact this codebase derives.** Decision 244 reads a deployment's
mode off the `sk_test_`/`sk_live_` prefix of its key, which is a property of the *installation*;
`domain` is the same fact about a *transaction*, stated by the provider in its own response. Nothing
consumes it and nothing should - reading the mode from a response would make "am I taking real money"
answerable only after a call succeeds, which is the wrong way round for the one question 244 exists to
answer at boot. It is recorded because it is the cheapest cross-check available the next time anyone
wonders whether a deployment's key and its dashboard agree, and because a payload field that restates
one of our settings is worth knowing exists before somebody assumes no such field does.

**And `fees` is a number this system now knows it does not record.** The ledger credits
`outcome.amount` - the gross - so a 50.00 NGN deposit makes the wallet 50.00 richer while Paystack
will settle 0.75 less than that into the operator's bank account. Nothing here is wrong: a wallet is a
record of what its owner is owed, and a processing fee is the operator's cost of accepting the money
rather than the payer's, so crediting gross is the correct model and is what every wallet-shaped
product does. What is new is that the cost is no longer hypothetical - it has a field name and a value
on every transaction - and the day somebody asks "what did this month's deposits actually cost us",
the answer is reconstructible from Paystack's dashboard and from nothing in this repository. That is a
reporting gap rather than a defect, it is named here so it is not rediscovered as one, and it belongs
with i4's balance-cap question rather than beside the rail.

**`run-2` then settled the same way `run-1` had, and the gap between paying and crediting is the
point of it.** The payer's checkout completed at 20:36; the balance stayed at 100.00 until
`reconcile` was run by hand, at which point the row read
`96f71fe1-12ad-4b60-a06f-75911614579a.run-2  settled  charge_succeeded applied to a deposit` and the
wallet read `available: 150.00 NGN`. **Nothing was wrong, and that is exactly why it is written
down.** No webhook URL had been registered for this installation - there was no public address for
one to arrive at - so the payer's 50.00 NGN sat at Paystack with this ledger holding a PENDING row
and no idea, and the only thing that closed the gap was somebody running a command. A deposit rail
whose normal path is the webhook and whose safety net is the reconciler looks, from a laptop with
neither, like a system that has quietly stopped crediting money. It has not; it is waiting to be
told. The tunnel half of the run exists to make the *normal* path the one under test rather than
leaving the net to stand in for the road.

**A third deposit then confirmed that the net is not the road, by sitting unsettled.** `webhook-2`,
25.00 NGN, was initiated and paid with no public address registered, and `history` read:

```
2026-09-18T14:27:03  deposit  100.00 NGN  successful
2026-09-18T21:35:34  deposit   50.00 NGN  successful
2026-09-18T22:47:21  deposit   25.00 NGN  pending
```

Three rows, and the third is the only one this system did not have to be told about by hand. **The
`pending` is the correct state and is not a defect** - it is a paid collection whose event never
arrived, which is exactly what the reconciler is for and exactly what it would fix on its next run.
What the row records is the shape of the remaining gap: **every deposit this system has credited, it
credited because something local asked.** The webhook is the path that must work in production, and
it cannot be exercised without a public address - which is a deployment artifact, not a line of code,
and the reason this last step cannot be finished on a laptop alone.

**The reference survived the round trip, which is the other thing only a live run could settle.**
`96f71fe1-12ad-4b60-a06f-75911614579a.run-1` is 42 characters and carries both hyphens and a dot;
Paystack accepted it as the idempotency key and echoed it back as `data.reference`, which is the
value this system stores as `provider_reference` and the value an arriving webhook will have to match
on. Until this run, decision 149's separator and decision 151's alphabet were a *reading of Paystack's
prose* - quoted from a rejection message, and checked against nothing. What is now observed is the
half that matters operationally: **every reference this system mints is one the far end takes.**

**Scope, because overclaiming here is the expensive kind.** This is the `/transaction/verify` path -
the one the reconciler drives. The webhook's `charge.success` body is a different code path that
happens to carry the same field names, and **it has still never been seen**. What is proven is that
Paystack reports a settled collection gross in answer to a lookup *this system performs*; what is not
proven is that the pushed event agrees. Since the webhook is how the money normally arrives - the
reconciler is the safety net, not the road - that remains the last unverified link in the rail, and
it is the one the tunnelled `charge.success` exists to close.

**What else the run leaves open, in the order it matters.** The webhook, as above. Then the parts of
i12 that this did not touch: `=`, `,` and the deliberately-absent length rule are still untested -
what is proven is that the references *this system mints* are accepted, which is narrower than "the
alphabet is right", and a caller-supplied `ref` is bounded only by the alphabet. Finally, the test
key's minimum amount: 100.00 NGN was accepted, and where the floor sits is still unknown, which
matters only for deposits smaller than a naira and is therefore the least urgent of the three.

**248. The branch that reports "no transaction" now says which of two things happened.** Decision
247's run spent a full round on a refusal that could not be read, so the one word the provider had
already supplied was made to reach the sentence. The branch is
`initialize_deposit`'s `if not isinstance(data, dict)`, and the argument for changing it is entirely
in what reaches it: **two facts that share nothing but an absence of `data`.** A ``401``/``403`` gets
there because ``AUTHENTICATION_FAILURES`` is in ``answers``, so the refusal branch above it does not
catch them; a ``2xx`` with an empty body gets there because nothing else would. The first is a
credential this installation can correct, the second is a provider or an adapter failing, and the
old message - *"the provider accepted the call but returned no transaction; the secret key is the
usual reason"* - named the first in both cases while opening with a clause that was **false** in the
first case, since a ``401`` is the provider explicitly refusing the call.

**The test gap is the sharper half of the story.** Of the branch's two reachable states, the ``401``
had a test and the ``2xx`` had none - so the sentence was pinned in the case where it was merely
misleading and free to drift in the case where it was actively wrong. The new test is
`test_an_empty_two_hundred_is_not_reported_as_a_bad_key`, and its assertion is that the phrase
*"secret key"* is **absent**: a test asserting only that some sentence was raised would have passed
against the version that sent an operator to check a key that was fine. That is the same shape as
decision 241's argument for siblings over a hierarchy - the assertion that matters is the one the
wrong implementation would fail.

**What was deliberately not changed is the grade.** `InvalidPaymentIntentError` still falls through
`errors._grade` to a **400**, and a rejected key is arguably a **503** - "this installation cannot
serve" is exactly what a wrong secret key means, and it is the grade
``PaymentsUnconfiguredError`` already takes next door. Making it 503 would be wrong for the other
half of the branch, though, because a malformed ``2xx`` from a healthy provider is not a
configuration fact about this installation either; the honest fix is to split the class as decision
241 split its sibling, and that is a change to make on its own evidence rather than as a rider on a
logging repair. It is named here so the next reader does not have to rediscover that the grade is
load-bearing and wrong in one direction.

### The deployment

Phase 4's second bullet, and the one that turns this from a project that runs on
a laptop into one that answers on a hostname. Three files - `Dockerfile`,
`compose.yaml`, `Caddyfile` - plus `.env.example`, which is the list of every
setting the software reads with none of the values in it.

```
cp .env.example .env          # APP_DOMAIN, PAYSTACK_SECRET_KEY, BUDGET_UID=$(id -u), BUDGET_GID=$(id -g)
mkdir -p data
docker compose up -d --build
```

The tick, and what it costs to run it:

```
*/5 * * * * cd /srv/budget && docker compose run --rm tick >> tick.log 2>&1
```

**249. Caddy, and the whole of the TLS configuration is one line.** Decision 52
committed to "a reverse proxy with TLS" and deliberately did not name one, so
this is the decision that spends that. The argument is entirely about what has
to be remembered: Caddy obtains a certificate for `$APP_DOMAIN` from an ACME
issuer on the first request, stores it in its own volume, renews it about thirty
days before expiry, keeps an OCSP staple current and redirects HTTP to HTTPS,
and none of that is configured in `Caddyfile` because none of it is a decision
this deployment is making differently from the default. The alternative -
`nginx` plus a `certbot` container, a webroot, a renewal cron and a reload hook
- is two more moving parts whose failure mode is a certificate that expires on a
weekend, and it is close to the operational surface decision 52 was arguing
against.

**The cost, stated rather than implied:** a reverse proxy whose behaviour is
mostly hidden, so an operator debugging a certificate has less to read than they
would with nginx; one more party in the trust path, since an ACME issuer has to
be reachable from this host; and a tool that is less transferable knowledge than
nginx. What is bought is that the number of ways this can be wrong is small
enough that the configuration fits in a comment. `Caddyfile` keeps the access log
directive written out and commented, so the place it goes is not a discovery.

**250. One image, two services, because `BUDGET_DB` is the setting that decides
which file is the money.** `api` and `tick` are the same build with different
commands, and the point is not the saving. Decision 246 recorded a live bug
where the CLI and the API resolved to *different files* on any machine where
`BUDGET_DB` was set - the CLI opened a wallet, the API answered 404 about that
wallet, and both were right about the database they were looking at. The
`compose.yaml` answer is a single `x-money-environment` anchor setting
`BUDGET_DB=/data/budget.db` for both, and an `environment:` block that wins over
anything in `.env` - so the two presentations cannot be split by editing the
wrong file.

**The tick stays a one-shot, and that is the shape being preserved rather than a
container being added.** The README's scheduler section argues three things
about the cron line - five minutes rather than an hour, an absolute `cd`, and the
environment set inline rather than in a shell profile - and every one of them
survives here unchanged. What changes is only what the host needs: `docker`, and
no Python, no venv and no install. The service carries `profiles: ["tools"]` so
that `up -d` does not start it: a tick that ran once at boot and then sat exited
would be a tick whose log a reader would mistake for a working schedule.

**251. The image binds `0.0.0.0` and `__main__.py` still binds `127.0.0.1`, and
the disagreement is the design.** Both defaults are right for their own caller.
The module entry point is for running this on the machine you are sitting at,
where the objection to listening widely is the one it has always been - real
credentials over plain HTTP. The image exists to be reached by a proxy on a
private compose network, where the only thing that can open the port is Caddy,
and Caddy is what terminates TLS. So the deployment passes `--host 0.0.0.0` in
the `CMD`, which is what `__main__.py`'s own docstring says a deployment does
when it says binding wider is "a decision a deployment makes explicitly - by
passing its own `--host`, which is what the `--factory` form above exists to
allow."

**That docstring had to change and the change is worth recording.** Its third
paragraph justified loopback with "this API speaks plain HTTP", and after this
phase that sentence is true of a bare local run and false of the deployment. The
file rewrites the paragraph, keeps the default, and says which of the two
situations it now describes - because a rationale that outlives its reason reads
as a constraint that has lifted, and that is this file's own standard rather than
a new one. This is the second time that paragraph has been rewritten; the first
is in its own history.

**One worker, and the number is load-bearing rather than a default left alone.**
`SqliteUnitOfWorkFactory.start` takes `BEGIN IMMEDIATE` on *every* unit
deliberately, so that two writers cannot disagree about a value one of them is
about to write. That is a property worth more than throughput, and it also means
extra workers would buy contention rather than concurrency: the second worker's
first write waits for the first worker's unit to commit either way. A future
reader who reaches for `--workers` to fix a slow endpoint will find the argument
here rather than a config value.

**252. The database is a bind mount, and the price is one `install -d`.** The
choice is made by the item *after* this one on the checklist: backups with a
restore somebody has actually run. `sqlite3 .backup` against `./data/budget.db`
from the host is a one-liner, and the same operation against a named volume is an
exercise in docker plumbing - and the difference between those two is exactly the
friction that decides whether the backup gets written at all. So `./data` is a
directory on the host rather than a volume, and the next item inherits it.

**Three consequences, all of them deliberate.** The mount is read-write and has
to be: every connection runs `executescript` with the schema and the
PRAGMA-guarded migrations in `open_sqlite_connection`, so a `:ro` mount fails on
open rather than merely failing to write. The *directory* is mounted rather than
the file, because SQLite writes its rollback journal beside the database. And the
directory has to exist before the first `up`, and the container has to be running
as the uid that owns it - the second half of which is decision 255 and was got
wrong here first.

**253. `X-Forwarded-For` is still read by nothing, and that is now a choice
rather than a constraint.** Decision 53 wrote that "until a reverse proxy this
installation controls is in front of it, every such header is a string the
attacker chooses, so a limit keyed on one is a limit the attacker sets." A proxy
this installation controls is now in front of it, so that precondition has been
met - and the answer is still to read nothing, for a reason that outlived it: the
rate limiter keys on *subject* (the address in the body, the account a change
belongs to, the phone number folded), which is the half a CDN cannot do, and the
source dimension is tracked on its own as "a third column on the same table."
Building it here would be moving that item's decision into a deployment change.

So uvicorn's `--forwarded-allow-ips` is left at its default, which is
`127.0.0.1` - and the proxy's container address is not `127.0.0.1`, so the
headers are ignored rather than trusted. `request.client` is the proxy.
**Whoever builds the source dimension should read this paragraph first**, because
the setting that will matter is that one, and the mistake available is passing
`*`.

**254. The proxy's access log is one line and is deliberately under a comment.**
`Caddyfile` keeps `log` written out and disabled. It would put every request at
the proxy into Caddy's JSON log, which is most of what the checklist's logging
item asks for and none of what it decides: the format, where the lines are
shipped, how long they are kept, and what a request is correlated by are all
still open, and turning the directive on would make one of those decisions in a
proxy config where nobody would look for it. The line is in the file so the place
it goes is not a discovery.

**255. The bind mount's ownership problem was solved the wrong way round first,
and the fix is that the container moves to the uid rather than the uid to the
container.** The first version of `compose.yaml` created the database directory
with `install -d -o 10001 -g 10001 data`, which is `chown` with a different
spelling and needs root. Not having root is the ordinary state of a deployment
user - nobody should be running `docker compose` as root, and the account this
is meant to be deployed under is the kind that does not have it - so the single
setup step of the whole deployment was a command the person deploying it
probably could not run. The attempt produced `install: cannot change owner and
permissions of 'data': Operation not permitted`, which is one of the more useful
errors in this file's history: it arrived before anything else could go wrong,
and it named the thing that was wrong.

**The answer is a build argument.** `Dockerfile` takes `BUDGET_UID` and
`BUDGET_GID` and creates its user at those numbers; `compose.yaml` passes `id -u`
and `id -g` from `.env`; the container process is therefore the person who owns
`./data`, and `mkdir -p data` is the whole of the host-side setup - one command
that needs nothing. The ownership was never the problem. Treating one fixed uid
as a fact about the deployment was.

**Both are required and neither is defaulted, and it is the argument
`RATE_LIMIT_FLUSH_SECONDS` already makes for raising on a typo.** The Dockerfile
has defaults, because `docker build .` with no arguments should still produce a
working image. Compose deliberately does not fall back to them: 10001 produces a
container that starts, serves `/health`, and then fails every unit of work on an
unwritable directory - a failure that reads as a broken database and sends the
reader to SQLite rather than to their environment. A missing variable should read
as a missing variable. The cost is that a fresh clone has to run `id -u` before
`up`, which is what `.env.example` says in the place somebody will be reading.

**Two things this does not fix, and both are real.** The gid must not already
exist inside the image - anything below 1000 probably does, since that is where
Debian keeps its system groups - so both ids have to come from a normal user
account. And changing either means rebuilding, because a build argument is baked
into the layer that uses it. Neither is an argument for the root version; both
are arguments for the value living in `.env`, beside the command that produces
it.

**Before any of this, what the host needs - and the machine this was written on
had none of it, so the failures are recorded rather than the theory.**

- **Docker *with the Compose plugin*.** On Ubuntu, `apt install docker.io` gives
  you Docker and not `docker compose`: the plugin is a separate package
  (`docker-compose-v2` on 24.04), and a client without it answers `docker:
  unknown command: docker compose`. That is a different message from the one a
  missing Docker gives, and the difference is worth knowing because the fix is
  different too. Docker's own `docker-ce` bundle includes the plugin; the
  distribution package does not.
- **Root, once.** Two separate things want it and neither can be designed
  around. Adding your user to the `docker` group, because the daemon's socket is
  root-owned and `usermod -aG docker` writes `/etc/passwd` - which answers
  `Permission denied` and `cannot lock /etc/passwd` when run without it. And
  `systemctl enable --now docker`, if the package did not start the daemon.
  Group membership is read at login, so afterwards you log out and back in, or
  run `newgrp docker` for one shell.
- **A hostname whose A record already points at the host**, before Caddy starts.
  ACME will not issue for a name that does not resolve and does not fall back to
  anything, so a wrong value here is a proxy that serves nothing - loud, at
  least.

**A host without root cannot run this deployment at all**, and that is a fact
about Docker rather than about these files. The database directory no longer
wants root - decision 255 - but the socket does, and there is no version of
`docker compose up` that works without either group membership or root. Which is
why the smoke test below belongs to the deployment host and not to a laptop.

**What has actually been run, stated precisely, because the difference between
this paragraph and the rest of this file is the point.**

*Ran, and passed:* the `.env` recipe and `mkdir -p data`, on a machine with no
root. That is decision 255's fix validated by an execution rather than by an
argument, and it is the only part of this deployment that has been.

*Ran, and found a trap:* `caddy validate`, invoked by hand without `APP_DOMAIN`
in the environment. An empty `{$APP_DOMAIN}` leaves the site header as a bare
`{`, which Caddy reads as the opening of the *global options block* - so it
reported `unrecognized global option: reverse_proxy`, naming the one line in the
file that was correct and saying nothing about the missing hostname two lines
above it. `Caddyfile` now carries that error text and its real cause, so the next
person to run the validator by hand is not sent to the wrong place. Under compose
the state is unreachable, because `${APP_DOMAIN:?}` refuses to compose without
it.

*Never ran:* `docker compose config`, `docker build`, `docker compose up`. The
Compose plugin was absent, installing it needed root, and root was refused. So
the four things still unverified are that the compose file parses, that the image
builds, that the uid-matched container can write `./data`, and that `/health`
answers through Caddy.

**`.github/workflows/ci.yml` is where three of those four get checked instead**,
on every push, by a runner that has a daemon and a Compose plugin - which is a
better home for them than a laptop anyway, since a check nobody can run on the
machine the code is written on is a check that only exists on a machine nobody
deploys from. A certificate is outside what any runner can verify, and the
container-answers-a-request step is left to the host on purpose: it needs the
mount ownership that a runner does not have in the right shape, and a green tick
that only sometimes means something is worse than an open item.

**So this section is a design with two executions behind it, not a report**, and
it should be read the way decisions 249-255 are written - as arguments about
what the deployment *should* be - until somebody has run it on the host and said
otherwise.

### Still open

- **A plan edited into a currency its wallet does not hold stops the whole tick.**
  Found by the entry-point audit below, and the sharpest of its findings because
  the consequence is not a refusal but an outage. `PlanService.create_plan` checks
  `plan.total_to_move.currency is not wallet.currency` (decision 179);
  `PlanService.edit_instructions` loads only the plan, so the check is not
  re-applied; and a mismatched run raises `CurrencyMismatchError` out of
  `_blocking_reason` - `_limit_block` for a plan that sends value out, because
  `check_outflow` adds the instruction to a day total read in the wallet's own
  currency, and `_money_block` for one that only reshuffles, which compares
  `total_to_move` against a balance the same way. Then through
  `ExecutePlanRun`'s `except BaseException: raise`, into
  `RunDuePlans`, which catches per *plan* nowhere. The tick exits 1 and every plan
  after it in that pass does not run.

  Decision 200 put a pre-flight ahead of `_money_block` and did not change this:
  the plan already killed the tick one method later, so the new check moved
  *which* method raises and not whether anything does. Said plainly because it is
  the sort of thing a later reader would otherwise infer was fixed.

  Two candidate fixes and they are not equivalent. Re-check in
  `edit_instructions`, which needs the wallet loaded and therefore a second read
  in a method that currently has none - correct, and it closes only this door.
  Or catch the domain error per plan in `RunDuePlans` and record the run as
  blocked with a new `RunBlockReason` member, which is the more honest shape
  (a plan that cannot run is a *blocked* plan, not a tick failure) and covers
  every other way one plan's domain error could take a tick down with it.
  The second is the one that matches the failure; the first is the one that
  matches the rule. Doing both is defensible and neither is done.
- **The entry-point audit, in full.** A read-only pass over the other places a
  value enters this system, asking the question this slice was about - *what does
  the door accept, and what refuses it later* - with no code changed. The six
  findings, and the honest state of each:

  - ~~**A wallet's currency is never checked against the provider's.** `Currency`
    has five members (`NGN`, `USD`, `GHS`, `KES`, `EUR`) and `Currency(x)` is the
    whole of the check at `Money.__post_init__`; `PaystackPaymentProvider`
    forwards `"currency": SUPPORTED_CURRENCY` at
    `paystack_payment_provider.py:217`, a hard-coded `"NGN"`, whatever the amount
    was. `_subunit(amount)` sends the *number* (`amount.amount * 100`) and the
    constant sends the *label*, and nothing connects them - so a wallet holding USD
    and depositing `Money(100, USD)` posts `{"amount": 10000, "currency": "NGN"}`,
    which Paystack reads as one hundred naira. The payer is charged a hundredth of
    what the wallet believes it holds, and the ledger row credits the wallet with
    the amount it *intended*: the two ends disagree and neither is told.~~ **Closed
    - and the sentence above was wrong about what it cost, which is corrected here
    rather than quietly dropped.** The posting is described correctly; the
    consequence is not. `settle_payment.py:97` is `if row.amount != outcome.amount`
    and it refuses to move anything, and `routes/webhooks.py` reads the outcome's
    currency off `data["currency"]` - the provider's own field, which says `"NGN"`
    because that is what was sent. So the row asks for `Money(100, USD)`, the
    webhook reports `Money(100, NGN)`, `SettlementOutcome.AMOUNT_DISAGREES` is
    returned and the row stays PENDING: **the payer is charged ₦100 and nothing is
    ever credited**, neither the intended amount nor the wrong one. Both halves of
    the struck sentence were wrong in the same direction - "a hundredth" read the
    kobo factor as a hundredth of *value*, and "credits the wallet" credited a
    ledger that in fact refuses. The guard is at the deposit door rather than at
    wallet creation, for the reasons in decision 234. See decisions 233-239.

    The audit's own closing claim stands and is worth keeping: "A wallet in any of
    the other four currencies is creatable today, and its deposits are wrong at the
    far end in a way nobody is told about." The first half is still true, and
    deliberately so - what changed is that the deposits are now refused rather than
    wrong. The one place the wallet's currency met anything was
    `initiate_deposit.py:164`, and that compared the amount to the *wallet*; the
    second place is the line added beside it.
  - **The payout rail has no later refusal because it has no earlier or later
    anything.** `Destination` requires `bank_code` for a `BANK_ACCOUNT`
    (`destination.py:18`), which is a floor and not a ceiling, and the identifier
    and name are only checked non-empty. Nothing verifies that the identifier is
    the ten digits a Nigerian account number is, and nothing can - `PaymentProvider`
    has three methods and `initiate_transfer` is deliberately not one of them. The
    port's docstring gives the reason (a signature that would have been guessed),
    so destinations are *recorded* and money is *held*, and no external transfer is
    ever initiated. The real refusal will arrive with the rail that needs it, and
    the shape of the entry rule should be decided then rather than now.
  - ~~**Amounts are refused for shape and never for size.** `Money` refuses a
    non-finite value, a bool, more than two decimal places, and a foreign currency
    in arithmetic; `initiate_deposit.py:161` and `wallet_operation.py:152` refuse
    `<= 0`; `withdraw_money.py:38` refuses `< 0` (so a zero withdrawal is legal
    there and refused by the wallet downstream, which is a second refusal for one
    fact). Nothing refuses an amount above any ceiling, and the only thing that
    would is the provider - which does refuse them, at the far end, after the
    request has left.~~ **Done, for every door this slice reaches** - which is all
    of them except settlement, and that exception is its own entry below. A tier
    now carries a per-transaction ceiling, a daily outflow cap and a maximum
    wallet balance; they are enforced inside `WalletOperation.execute` so that a
    refusal is recorded (decisions 197-198), in `ExecutePlanRun._blocking_reason`
    for automated runs (decision 200), and at initiate deposit for the balance cap
    (decision 201). The provider is no longer the first thing to refuse an amount
    for its size.
  - **A payment that has already been made and would breach the balance cap has no
    answer.** This is the one half of the entry above that stayed open, and it is
    open because it is a policy question rather than a missing check. The cap is
    refused at initiate deposit, which is the last moment at which refusing costs
    nobody anything; but the balance read there is free to change before the payer
    pays, so two collections that each fitted can both be paid and both settle, and
    `SettlePayment` credits the wallet directly and consults no ceiling. What it
    should do instead is genuinely undecided: hold the payment until the account is
    identified, reverse it, or credit it and accept that this wallet is over its cap.
    Each is a real answer with a different conversation attached, and none of them
    should be chosen by whoever is next editing the file. The live run this project
    has learned to insist on - a deposit that would breach the cap, against the real
    provider - is how the choice should be informed, and it has not been run.
- **The limit-day is the server's day, so an allowance resets at the wrong midnight
  for anybody who is not where the server is.** Decision 202 has the argument:
  every stored moment in this system is naive and local, so a day boundary is only
  meaningful in that frame, and `limit_day_bounds` slices the local day rather than
  inventing an offset it would then apply to a value that already has one. The cost
  is real and not only cosmetic - `Africa/Lagos` is the market this product is
  built for, so the server will usually be *in* the right frame and the bug will
  mostly be invisible, which is the worst way for a boundary bug to behave. Closing
  it means a timezone concept: a column, a policy for the ones already written, and
  a decision about whether the day belongs to the server, the account or the
  wallet's currency. That decision is the entry, and it is not a small one.
  - **A plan's schedule is validated as a type and not as a value.** `Schedule`
    requires a `Cadence` and a `datetime` anchor (`schedule.py:31-45`), and
    `ends_on` cannot fall before the anchor's day. Nothing refuses an anchor in the
    past, which is a real state with a real meaning (a plan that owes several runs
    - `RunDuePlans` is explicit that a plan missed for four months is due for four
    occurrences), so this is a decision rather than a gap. Named here so that it is
    read as one.
  - **A pot's name is the CLI handle and has no normalisation.** `Fund` requires a
    non-empty stripped string (`fund.py:306`), and `Wallet.open_pot` refuses an
    exact duplicate (`wallet.py:111`). There is no folding and no length bound, so
    `"Vacation"` and `"vacation"` are two pots, a name of ten thousand characters
    is accepted, and `" Vacation "` is accepted as a *distinct* name from
    `"Vacation"` while being indistinguishable from it in a terminal - reachable
    afterwards only by typing the spaces. Compare `email`, which folds, and the
    duplicate check here is `==` rather than a comparison against the folded form.
  - **The job that resolved the worked example.** The idempotency key reference is
    the shape this list exists to argue for and it is already closed (decisions
    151-155): the alphabet was written down from memory in one place and copied
    into a live path, and only a real call found it. What that cost is the
    argument for the five above - each is a value this system accepts and expects
    somebody further down to refuse, and the somebody is sometimes a provider, a
    person's patience, or nobody at all.
- **The dot rule is still written from one observation, and it is now load-bearing
  in two more places** (decisions 156, 151 and 162). What is known is that Paystack
  refused `live@localhost` and accepted `live@example.com`, and the dot is the only
  difference between them. That is not the same as knowing their rule, and the
  courtesy is narrow for exactly that reason - but it is now also the *guard* at
  sign-up and at the change request, where a wrong guess is cheap but a
  systematically wrong guess is a class of people who cannot register. What settles
  it is a live call rather than a reading, and the live run below is where that
  happens.
- ~~**An account registered at an address no provider will bill has no way to fix
  it** (decision 156).~~ **Done.** `POST /users` still accepts anything with an `@`
  and always will, because decision 157's argument against this codebase asserting
  an email policy at registration is unchanged - but the address can now be moved.
  `POST /users/me/email-changes` and `change-email` take a session and the current
  password, mail a 256-bit token to the new address, and apply the change when it
  is presented; with no mail account configured the change applies on the password
  proof alone and both presentations say so. The wallet, its pots and its whole
  history come with it, which was the part the old remedy could not offer. Two
  candidate shapes were named here and the second was built - an email *rule* at
  registration, or a change flow - and the reason is the one recorded: people
  mistype, and a typo is not a reason to lose a wallet.
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
- ~~**Rate limiting on `POST /users` and `POST /sessions`** (decision 86).~~ **Shipped,
  across seven routes rather than the two this entry named** - see decisions 214-232.
  The entry is kept rather than deleted because three of its claims needed updating
  and one of them was already stale when the work was done, which is the kind of
  thing that should be visible rather than quietly erased.

  **The timing gap decision 81 documents is closed, and was closed before the
  limiter existed.** The fix is the four lines this entry predicted - verify against
  a dummy hash when no credential is found - and it is in `LogIn` now, so an unknown
  address and a wrong password pay one argon2 verify each. The limiter is the second
  half of the same protection rather than the first: a caller may no longer
  distinguish the two cases by how long they take, and may no longer distinguish them
  by how many they can afford to try.

  **`POST /users` and `POST /sessions` were never the whole list, and were not the
  worst of it.** They were the only unauthenticated writes when this was written;
  there are nine now, and what decided which of them got a limit was not
  authentication but *cost* - so the two doors that mail or text a credential and the
  one that hands a real charge to an SMS provider are on the list beside them, and at
  tighter budgets than either. The list itself is in `rate_limits.py`, with the
  argument for each number.

  **And it leaks account existence by latency, which no status code can fix - and
  which the limiter does not fix either.** Decision 184 answers identically for a
  known and an unknown address, so the *bytes* are not an oracle. The clock still is:
  one arm does an SMTP round trip and the other returns as soon as a `SELECT` misses,
  and that difference is not subtle - it is milliseconds against microseconds, and
  measurable from outside without any privileged position. Nothing available closes
  it. The mail cannot be queued behind an outbox, because a code that expires in
  fifteen minutes cannot tolerate a queue whose premise is that late is acceptable; a
  mail cannot be composed for an address that names no account, because there is
  nothing to send to; and sending one anyway to a fabricated address would be this
  system mailing strangers on a stranger's word. **This half is still owed**, and the
  limiter's contribution to it is smaller than the paragraph it sits in makes it
  sound: three requests an hour per address is a budget an attacker can still spend -
  a dozen addresses is thirty-six measurements an hour, enough to separate a round
  trip from no round trip if the difference is visible at all. What the limit does is
  make the attack slow and leave a trace. Nothing about decisions 214-232 should be
  read as having paid this.

  **The other thing this entry was never about is still owed too.** A rate limiter
  counts *requests*; the control `transaction_repository.py` records is a bound on how
  often money may *move*, which counts ledger rows and needs a read of its own. The
  two disagree about a caller making one enormous payout, and the second one is not
  built. Decision 232 says so where a reader looking for it would land.
- **Purging settled `email_changes` rows, as with sessions.** Every confirmed
  change leaves a row behind on purpose - decision 168, because deleting it would
  collapse "already used" into "never existed" - and nothing ever removes one. The
  same is true of `sessions`, and the same argument applies to both: they are dead
  weight read only by a lookup that no longer matters, bounded by a fifteen-minute
  window, and deleting them destroys the only record that something happened. So
  this is one design job covering two tables rather than two chores: what the
  retention rule is, and whether *this* record is ever read by a person (an audit
  view, the moment one exists, would make the answer "keep it"). Not urgent while
  the tables are small; a sweep is one `DELETE` and one step in a job that already
  runs.
- **A session opened before an email change keeps working afterwards**, and that is
  a decision rather than an oversight (decision 175). `Session` is bound to
  `user_id`, not to an address, so nothing breaks by leaving it alone - but it does
  mean that somebody who changes their address because they believe the old one is
  compromised does not thereby log the old holder out. Named here because "my
  sessions still work" is the kind of thing a person discovers at the wrong moment.

  **Half of this bullet's stated cost is now out of date, and the decision is not.**
  It used to say that revoking "would need a `delete_by_user_id` that does not
  exist". The password reset needed exactly that method for its own reasons - a
  reset's whole premise is that somebody else may know the old password (decision
  188) - so `SessionRepository` has it now, and closing *this* gap would be one call
  in `ConfirmEmailChange` rather than a new method. What is unchanged is the
  argument: the threat that matters is an attacker holding both the password and a
  token, and they log in again the moment the change is applied. So it is cheap and
  still not done, which is worth saying plainly rather than leaving a stale reason
  standing in for the real one.
- **Neither of the two mails identifies its own request** (decision 181). Five
  requests landed in one inbox inside an hour, the two subject lines are fixed
  strings, and no body carries the `email_change_id` - so arrival order is the only
  thing that tells one mail from another, and a mail client does not promise arrival
  order. Two candidate fixes and they are not equivalent: an id makes a mail citable
  but is useless to the person holding it, and the request's own moment is something
  they can match against what they did. The second is the one that helps, and
  neither is done.
- **An invariant whose own docstring calls it a bug answers 400** (decision 182).
  `InvalidEmailChangeWindowError` gets to the wire through `errors._grade`'s
  fallthrough for unlisted `MoneyError`s, so a stored row whose `expires_at` is at or
  before its `requested_at` is reported to a client as its own bad request. Narrowing
  the fallthrough to a named set of client-caused errors is the precise fix and means
  classifying every 400 that currently arrives by falling through, one at a time;
  mapping this one member to 500 would be honest about a broken row and would make
  every future invariant reached by a direct write read as this server's fault, which
  is what `unexpected_error_handler`'s wording is for. Doing neither leaves the grade
  coarse rather than wrong, which is why it is here rather than done.
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
  rather than by a signature, so it is worth re-reading when a caller appears.

  **Re-read in 3b, and the answer is that the convention holds and has grown.** No
  third caller of `list_by_status` appeared, but a fourth *unscoped read* did -
  `TransactionRepository.list_awaiting_provider` (decision 144) - so the family is now
  four: `list_by_status` twice, `WalletRepository.owner_of` (decision 125), and this
  one. All four are kept honest the same way and each is worth re-checking the same
  way: the read answers a question about the *installation*, nothing user-facing can
  reach it, and the money is still only ever loaded through an owner-scoped door
  afterwards. The thing to notice is that four is a count somebody has to maintain by
  reading the code - which is exactly the fragility this entry has always named, and
  it is still tolerated rather than fixed.
- **Two hand-written copies of a test double, and the port growing under them.**
  `tests/conftest.py` has carried the note for a while: five modules used to define
  their own `RecordingTransactionRepository`, the three fund modules now share the
  fixture, and consolidating the remaining two "belongs with whatever touches them
  next". 3b touched them - not because anything about deposits or withdrawals
  changed, but because `TransactionRepository` grew `list_awaiting_provider`, and an
  abstract method with no implementation in a subclass makes the subclass
  un-instantiable whether or not anything ever calls it. **Four tests in those two
  modules failed on the first full run of the slice for that reason**, which is the
  evidence the note never had: a copy of a port is not untidiness, it is a thing that
  breaks when the port moves, silently, in files that have nothing to do with the
  change. The fix applied was the three lines each copy needed; the consolidation
  (delete both classes, swap four `repository = RecordingTransactionRepository()`
  lines for the fixture the fund modules already use) is small, mechanical, and
  un-done on purpose - it is a rewrite of two unrelated modules' setup and deserves
  its own commit rather than a ride along with a payments slice.
- ~~**`main` resolves the development user for every command, including the ones
  that do not act as anybody.**~~ **Done, in Phase 2a** (decision 91). The identity
  commands and `plan tick` are now dispatched above the line that resolves an actor,
  so a fresh database no longer grows a `dev@localhost` row on the way past a tick -
  there is no default identity left for anything to create.
- ~~**Password reset, and address *verification* as a thing distinct from address
  change.**~~ **Password reset is done** (decisions 183-191), and the prediction this
  entry made about it was right in the part that mattered. It said a password reset
  "will want this exact mailed-token machinery", and it did: the 256-bit token hashed
  at rest, the single-use windowed claim, the three distinguishable refusals, the mail
  that must arrive and the notice that must not be able to undo anything all came
  across whole. It also named the question to answer first - whether `EmailChange`
  generalises - and the answer was a second aggregate rather than a shared base
  (decision 183).

  What this entry could not have seen is that *removing* the password proof from the
  request does not leave a smaller address change. It leaves a route that a stranger
  can point at somebody else's account (decision 184), a response that therefore has
  to be byte-identical for a known and an unknown address, a refusal where the change
  flow has a fallback (decision 186), a session revocation where the change flow
  deliberately has none (decision 188), and a timing gap that no status code can close
  and that is written up below as the strongest argument for the next piece of work.
  Every one of those is a decision rather than a line of code, and the prediction was
  silent about all of them.

  Two smaller things went the other way, and both were already on this list. An
  account holding no credential can gain its first password through this flow, which
  no account could reach until Google OIDC landed in 2c - and `SignUpWithGoogle` is
  now the path that creates one (decision 190); and the paste fix
  decision 180 recorded and did not ship has now shipped, because the reset is the
  second caller that made extracting `_prompt_code` worth doing (decision 191).

- **Address *verification* as a thing distinct from address change, which is the half
  that is still open.** A change proves the new address; nothing has ever proved the
  one somebody registered with, so "verified" is a fact this system cannot state about
  any account that predates the address-change slice. That is a narrower claim than it
  sounds - proving somebody reads a mailbox would not have stopped `nobody@localhost`,
  which is why the deposit courtesy (decision 157) is a separate thing - but "this
  address was proved at some point" is a column that does not exist, and an
  installation that wanted to refuse an unverified address a payout would find
  nothing to read.

  **The reset flow makes this more visible rather than less.** It proves the mailbox
  at the moment a password is replaced and then forgets, exactly as the change flow
  does - because a proof that has to be spent to be useful is not a column, and
  writing one down would be asserting a fact about an account that nothing had
  checked. So the gap is now named in two features instead of one, and the shape any
  fix would take is the same in both: a `verified_at` on the account, written by
  whichever flow proves the mailbox, and a decision about what reads it.

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
- ~~**The settle-and-refund path, which is Phase 3's first item and the largest thing
  2b leaves open.**~~ **Done, in 3a** (decisions 124-136). A pending transaction now
  has an exit: `charge.success` settles a deposit, `transfer.success` settles a
  payout, `transfer.failed` fails one *and credits the hold back*, and
  `transfer.reversed` reverses a settled one. The three gaps this entry named were
  each closed deliberately - `release_hold` is the method that credits held funds
  back (decision 130), `reverse()` still refuses anything that is not SUCCESSFUL and
  that is correct for "a settled payout came back", and `provider_reference` is now
  written by `InitiateDeposit` and read by `SettlePayment` (decision 131). Two
  pieces were deliberately *not* done and had entries of their own below: the
  reconciler and deposit reversals. **One of those is now closed** - the reconciler
  was built in 3b (decisions 138-150), which leaves deposit reversals as the only
  survivor of this list.

  The last line of the original entry is worth keeping, because it was right and it
  is the reason this took a phase rather than an afternoon: the path was "deliberately
  not built in 2b: its only caller would have been a webhook that does not exist".
- **Migrating the `internal_reference` values already on disk.** Rows written before
  2b read `"<uuid>"` or `"plan:{id}:..."`; a retry of an old key no longer matches,
  once. Accepted at the time because it is a developer database and the change is
  the point (decision 107) - but it is the kind of "accepted" that stops being true
  the moment there is a real install, so it belongs on this list rather than only in
  the decision.

  **Re-read after 151, because the separator changed again and this entry is where a
  reader would come looking.** Decision 152 moved the scope separator from `:` to
  `.`, so stored references changed shape a second time - and the cost was genuinely
  nil this time, for a reason worth keeping beside this entry: the old shape never
  reached the provider, so no row was ever written under it. A reference that
  Paystack refused left no ledger row behind, which is the one place where "it never
  worked" is better news than it sounds.
- **Paystack's reference alphabet is written down from memory, three times, and
  nothing checks it against the provider.** This is the direct residue of decision
  151, and it is worth stating plainly rather than leaving the tests to imply
  otherwise. The set `letters, digits, - . , =` now appears in
  `app/domain/money/reference.py`, in `tests/conftest.py`'s double, and in
  `tests/domain/money/test_reference.py` - and every copy descends from the same
  source: the `nextStep` sentence in one 400 that a live call returned once. The
  duplication in the tests is deliberate (decision 155), so the real gap is not the
  count, it is that **no run anywhere asks Paystack what it currently accepts.** The
  suite can prove the code and the double still agree with the rule as it was written
  down; only a live call can prove the rule is still the rule, which is exactly the
  distinction that let a deposit route sit broken behind 2,042 green tests.

  Two ways to close it, neither built. A one-off script that posts a handful of
  references - one per candidate character - and records which come back
  `invalid_character_in_reference`, run by hand when Paystack's docs change; or a
  single test that is skipped unless a real key is present, which is the honest
  version of the same thing but adds a test whose meaning depends on an environment
  variable. Worth doing before this system takes a real payment, and not urgent
  before it.

  **One character in the set is a judgement rather than a quotation**, and it should
  stay visible as one: Paystack's sentence spells the set as `-,., =`, and the space
  inside those quotes may be punctuation or may be a member. It is read as
  punctuation and refused (a test says so out loud), which is the safe side - but it
  is this codebase's reading of an ambiguous sentence, not the provider's answer, and
  the script above is what would settle it.
- **Three receipt guards that mean the same thing and are written three times.**
  `WalletService._announce` skips a PENDING transaction; `ExecutePlanRun._record_success`
  skips a run whose rows did not all settle; and `SettlePayment._announce` skips
  anything that is not SUCCESSFUL (decision 135). They agree, none is derived from
  another, and there is no single place that says so. This entry used to say
  "unifying them is a small job that wants doing before a **third** caller appears" -
  and 3a is where the third caller appeared, so the condition has been met and the
  job is now owed rather than pending. The three are not quite the same rule either,
  which is the interesting part: two are "this movement has not happened yet" and the
  third is "this movement happened and then un-happened", and a shared helper would
  have to say which of those it means.
- **Deposit reversals and disputes, and the overdraft they imply.** Not built in 3a,
  and the reason is arithmetic rather than scheduling. Reversing a charge means
  **debiting** a wallet that may have already spent the money, and there is no honest
  answer to a wallet that cannot take the debit - the ledger would have to go
  negative, which is the one thing every rule in `Wallet` exists to prevent. (A
  chargeback is this system's only *inbound* movement whose far end can take money
  back, which is exactly what makes it unlike the four events 3a handles: a
  `transfer.reversed` gives back money the wallet already owned.)

  The shape the fix would take, recorded here so it is not re-derived: **refuse,
  record, and let a human settle it.** The wallet is left alone, the reversal is
  written down as a fact about a row, and the resulting shortfall becomes something a
  person looks at - because "this account owes money" is a real state a business has,
  and modelling it as a negative balance would be this system pretending it has a
  credit facility. It is Phase 3's second slice at the earliest.
- ~~**The reconciler.**~~ **Done, in 3b** (decisions 138-150). A PENDING row whose
  event never arrives is no longer indistinguishable from one whose event is in
  flight: `reconcile` asks Paystack about every deposit older than fifteen minutes
  and settles what Paystack says arrived, through the same `SettlePayment.settle`
  the webhook uses. The entry named the two things that made it a slice rather than
  an afternoon, and both were the real work:

  It needed a `verify(reference)`-style read on the provider port, and the port
  method whose signature was guessed was the mistake decision 124 declined to make
  once - so the read was *designed* here rather than sketched: `outcome_for ->
  ProviderAnswer`, three answers rather than an outcome or nothing, and the third
  one ("the provider has never heard of this reference") is the alarm the whole job
  exists to raise. And the poll interval, which had "no default", turned out to have
  a defensible one once the question was asked properly: the window is a courtesy
  rather than the guard (decision 143), because only a settled answer makes anything
  happen. Fifteen minutes is a chosen number, not a derived one, and it is one
  constant in one file.

  What it deliberately does **not** do is in the entry below.
- **Sweeping abandoned charges, and the fifth `ProviderEvent` that would need.** The
  reconciler reports an abandoned checkout and leaves it PENDING on purpose (decision
  142), which means a wallet can accumulate rows that will never settle and are asked
  about for ever. The fix is a sweep - some age past which an unclaimed charge is
  failed - and it is not a line in the reconciler because it needs an answer this
  system does not have yet: what does it mean to *fail* a charge that might still
  arrive?

  The hazard is concrete and it is the reason this is an open item rather than a
  branch. `SettlePayment` refuses a `CHARGE_SUCCEEDED` on a row that is not PENDING,
  so a row failed a minute before its payment arrived would take the payer's money
  and have nowhere to put it - and nothing would notice, because a FAILED row is not
  in the candidate set any more. The shape the fix would take is a fifth
  `ProviderEvent` - something like `CHARGE_EXPIRED` - and the rule around it would
  have to be one only the reconciler may apply, and only on evidence that the
  provider has stopped offering the checkout. That evidence is not in the answer the
  lookup currently returns, which is what makes this a design job rather than a
  branch. Worth doing against a real abandoned charge rather than against the idea of
  one.

  Note also what the report gives an operator in the meantime, which is the reason
  this is tolerable: the row appears in the log on every run, with its reference and
  `not_settled`, so an abandoned checkout is *visible* rather than lost. What is
  missing is a way to clear it, not a way to see it.
- **The `provider_events` audit table, argued against and not built.** Decision 128
  says why the *feature* does not need one. What it does not settle is whether an
  operator would want one anyway - a durable record of every event received,
  acknowledged, ignored or refused, independent of what the ledger ended up saying.
  The ledger is the answer to "what happened to this money" and is not the answer to
  "did Paystack send us anything last Tuesday". Worth building the day somebody has
  to ask the second question, and not before.
- **A refused withdrawal under an explicit `--ref` can be retried into a silent
  success.** *Found while designing the confirmation, not created by it* - the
  reproduction exists on `main` today:

  ```
  budget-manager withdraw W 5000 --ref X      # refused: not enough money
  budget-manager deposit  W 10000
  budget-manager withdraw W 5000 --ref X      # says "withdrew", moves nothing
  ```

  `WalletOperation.execute:105` deduplicates on `get_by_internal_reference`, a
  **global lookup that returns the row whatever its status**, FAILED included. So
  the third command finds the old FAILED row, returns it as though it were the
  outcome, and `_announce` composes a receipt for a payment that did not happen.

  Unreachable without an explicit `--ref` - a minted reference is a fresh UUID
  every time - which is why nothing has ever hit it, and why it is here rather
  than fixed. Confirmation 116 routes *around* it for the money-out path (a spent
  reference can never be read back, because the confirmation that would authorise
  a second attempt is the one already spent), but the plain `withdraw --ref X`
  path above is untouched and still walkable. The fix is either a non-FAILED-only
  lookup or a `Transaction.retry()` transition; both collide with the column's
  global `UNIQUE` and it is its own piece of work.
- **No CLI `close` verb**, today or after this phase. `close` is reachable only
  over HTTP, so its confirmation is API-only and the prompt has never been seen by
  a terminal. Adding the verb is small - the command, its parser branch, and one
  test - and it was left out rather than smuggled in because it is a *user-facing
  money transition with no way back* (decision 113) and deserves its own decision
  about what the preview should say before it deserves a prompt.
- **Abandoned `AWAITING` confirmations are never swept, on purpose** (decision
  119). They are dead weight read only by their own id, bounded by a fifteen-minute
  window, and deleting them would destroy the only record that somebody asked for
  something and abandoned it. If that judgement is ever wrong, the fix is one
  `DELETE` on the repository and one step in `plan tick` - and the reason it is not
  already there is that a sweeper with no clock to run it is not a sweeper, which is
  why the plan's own option text ("an expiry policy, and a sweeper") was implemented
  as the first half only.
- **The browser half of Google sign-in, deferred to Phase 4.** What shipped is a
  verification core and two routes that accept a token. What did not ship is any way
  for a *person* to obtain one without leaving this system: they use Google's OAuth
  2.0 Playground or their own client, and both CLI commands say so rather than
  implying otherwise. The authorization-code flow needs a `redirect_uri` - a public
  address for *this installation* - and there is no setting for one, deliberately
  (`passwordResetMessage`: "any URL here would be invented - right on one
  installation and wrong on every other, and a promise no code keeps"), and no
  browser front-end to come back to. Neither of those is a Google problem; both are
  Phase 4's.
- **A stolen Google id_token can be replayed until it expires, which is about an
  hour.** Verification refuses everything it can refuse - signature, issuer,
  audience, expiry, and an address Google has not proved - and none of that helps
  once a valid token is in somebody else's hands, because a valid token *is* the
  credential. TLS is the honest defence and TLS is Phase 4. Recorded here rather
  than left as a comment claiming the hole is shut.
- **An existing password account can never link a Google identity.** Declined by
  decision 209 rather than overlooked: the 409 names the remedy, and a person who
  holds both keeps two logins. What linking would need before it could be safe is
  proof that whoever is at the Google end is whoever holds the password - which is
  the password, presented again - and that is a flow nobody has asked for.

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

**Phase 2b - the money endpoints. Complete.** Every operation held by decision 62, now
that there is an actor worth spending a session on - and what looked like a mechanical
phase turned out to be the one that asked when a withdrawal has actually happened.
See "The money endpoints, and the pending intent". Two things were deliberately
*not* done: deposits stay held for Phase 3 (decision 97), and a pending transaction
is left with no exit (decision 106). Both were closed in 3a.

**Phase 2c - the rest of it:**

- ~~Google OIDC signup, matching on the Google subject id rather than the email.~~
  It shipped as decisions 204-213, and both predictions held: `User` has carried an
  optional `google_subject` since Phase 1, and `LogIn`'s branch for a
  credential-less account was already written and already correct. The one thing the
  groundwork did not anticipate is that the *proof* would arrive in a request body
  rather than through a redirect - which is a property of Phase 4 not existing yet
  rather than of this design.
- ~~Rate limiting on the auth endpoints first - login and signup are the ones worth
  brute-forcing - then on the API generally. It also carries the timing fix
  decision 81 documents.~~ It shipped as decisions 214-232, and both halves of the
  sentence needed correcting. **The scope was wrong in the narrowing direction**: what
  decided which routes got a limit was not authentication but *cost*, so the seven
  routes are the two here plus the three that mail or text a credential and the two
  that hand a real charge to an SMS provider or to Google. Anything unauthenticated
  that costs a lookup rather than a round trip is still unlimited, deliberately.
  **And the timing fix was already in before the work started** - `LogIn` has verified
  against `DUMMY_HASH` when no credential is found for some time, so decision 81 was
  closed by the login slice rather than by this one, and this file said otherwise
  until this line was rewritten.

  What the bullet got right is that `POST /password-resets` was the strongest case on
  the list. What it did not anticipate is that the strongest case is not one a limiter
  can answer: decision 189's latency oracle survives decisions 214-232 intact, and the
  new section says so where a reader looking for the fix would land. The **velocity**
  control over money movement that `transaction_repository.py` owes is the other half
  and is untouched by this - it counts ledger rows, not requests, and it is still owed.
- ~~Password reset and email verification.~~ The reset shipped as decisions 183-191.
  Email verification is a different question and is still open: it is about proving
  an address an account *already* holds, which no flow does today. See
  `### Still open`.
- Session listing and per-device revocation. The store already supports it, since a
  session is a row and revocation is a deletion; what is missing is the endpoints.
- TLS. Non-negotiable, and the reason a reverse proxy sits in Phase 4.

**The second-level confirmation was built ahead of this list**, because it is neither
of the two phases it sits between: it is a property of the money endpoints 2b built
rather than a new surface, and it was worth doing while that surface was still the
thing being thought about. See decisions 113-123.

**The password reset was built first of what remains here**, and it is the one item on
this list that was waiting on nothing. Rate limiting and OIDC were each a decision
about a technology, session listing is an endpoint over a store that already supports
it, and TLS is a reverse proxy; the reset needed only the mailed-token machinery the
address change had already built and proved in a live run, so the whole of its design
was the question of what changes when the password proof is *removed* from a flow that
had one. The answer is nine decisions' worth of change and no new machinery, which is
the strongest evidence this file has that the address-change slice was built at the
right level of generality - and the reason it went first rather than after the
limiter is that it is the flow whose open defect makes the limiter urgent. See "The
password somebody has forgotten, and the way back in" (decisions 183-191).

**Three of those four have since closed, and the order they closed in is the point of
the paragraph above.** The reset went first because it was blocked on nothing; the
limiter (decisions 214-232) went next because the reset had made it urgent; OIDC
(decisions 204-213) went last of the three despite being listed before both, because it
was the one that needed a decision rather than an implementation. What that order bought
is worth recording: the limiter was built against a surface that already existed and
already had tests, so the only new thing in it was the limiter, and the OIDC pair could
then be given limiters on the day it shipped rather than being retrofitted. A list
ordered by dependency rather than by importance looked wrong at every step and has
needed nothing redone. What remains here is session listing and TLS, and neither is
waiting on anything but the phase it sits in.

### Phase 3 - Paystack

**Phase 3a - the webhook, the settle path, and the deposit route. Complete.** The
phase that makes this a product rather than an exercise, and the one where the
existing domain work paid off exactly as predicted: the hard parts - the reference,
the pending intent, the two boundaries - were already built, and what was missing was
a second kind of authority. See "The webhook, and money that arrives from outside".

- **Signature verification**, which this roadmap called "the highest-risk item in the
  whole roadmap", built first and tested hardest. It is HMAC-SHA512 over the raw
  bytes, stdlib `hmac`, `compare_digest`, and `test_webhooks.py` is where the claim
  lives - including the test that signs one body and sends another (decision 126).
- **Payment initiation**, writing a `PENDING` transaction whose
  `internal_reference` is decision 7's key and which doubles as the provider's own
  idempotency key (decision 131).
- **The deposit route, unheld.** Decision 97's boundary crossing, now honest: the
  route credits nothing, and the only thing that can credit is an event (decision
  133).
- **The four settling events**, including the two that give money back - which is
  decision 106 closing, and the largest single gap 2b left open (decision 130).

What is deliberately still open, with entries under `### Still open`: **the
reconciliation job** ("webhooks get lost and payments get reversed; drift has to be
detected, not assumed away" - it wants a port read that does not exist yet, and the
shape of the fix is recorded there; closed in 3b, below), **deposit reversals and
chargebacks**, and the `provider_events` audit table.

Two things this phase changes about the roadmap below it. The reconciler became the
only unbuilt item on the original Phase 3 list, and it moved from "the fourth bullet"
to "the thing that makes the other three trustworthy at 3am" - which is what 3b below
is. And the provider port grew to **two** methods rather than the symmetric three the
plan sketched - `initialize_deposit` and `verify_signature` - because
`initiate_transfer` and `verify(reference)` would each have been a signature guessed
rather than derived. One of those two was then designed and built in 3b, and the other
is still correctly absent.

**Phase 3b - the reconciler, and the port read it needed. Complete.** The slice that
makes 3a trustworthy rather than merely correct: a webhook that was lost left a
payer's money spent and their wallet at zero, with nothing in the system able to
notice. See "Reconciling the payments whose webhook never arrived".

- **`outcome_for(reference) -> ProviderAnswer`**, the port's third method and the
  first that *asks* rather than tells - three answers, with "the provider has never
  heard of this reference" as the alarm no other part of this system can raise
  (decisions 139-141).
- **`reconcile`**, an actorless top-level command with its own hourly cron line,
  settling through the *same* `SettlePayment` the webhook uses - so a recovered
  payment and a delivered one leave identical state, which is the assertion the
  slice's central test makes (decisions 138, 149-150).
- **`list_awaiting_provider()`**, the fourth unscoped read and the one that makes the
  candidate set exactly "deposits a provider was actually told about" (decision 144).
- **`_request` learns that a status can be an answer**, which is decision 137
  recurring one method over in the same file: a 404 means "broken deployment" to a
  call that creates something and "no such transaction" to a call that asks about one
  (decision 146).

What it deliberately does not do, with entries under `### Still open`: **sweep
abandoned charges** (nothing is ever marked FAILED, for a hazard the entry spells
out), **reconcile transfers** (no payout goes through a provider yet, which is why the
port read is about collections only), and **deposit reversals** - which was already
open and is unchanged.

That is the last item on the original Phase 3 list. Nothing there is unbuilt any more.

**And 3b's own verification is what found out that 3a's deposit route had never
worked.** The second-to-last step of the plan is a run against a real test-mode key,
and the first `POST /wallets/{id}/deposits` it made came back
`invalid_character_in_reference`: the reference sent as Paystack's idempotency key
carried a colon, and Paystack refuses colons. Every deposit in the route's history
had been refused the same way, which the 2,042 tests then passing could not see,
because the fake provider accepted any string it was handed (decisions 151-155).

It is worth keeping in the roadmap rather than filed away as a bug, because it is
the clearest evidence this project has for a claim Phase 4 already makes in the
abstract: **a green suite proves the code agrees with itself.** Three of 3a's four
bullets above were correct and stayed correct, and the one that was not was
invisible from inside. What found it was the single step that talks to something
this codebase does not own, and the fix's real content was giving the double an
opinion - the alphabet is now written down twice, deliberately, in the two test
files, because a copy that imported the constant would agree with the code by
construction, which is the thing that had gone wrong.

**Phase 3c - the entry rule, and a way out of a bad address. Complete.** The slice
that closes the finding 3b's live run left behind, and it is a strange one in the
best way: the thing that found it was not a test and could not have been, and the
thing that fixes it is a rule the codebase had already written down and declined to
enforce for a good reason. See "The address an account holds, and the way out of a
bad one".

- **One predicate, three call sites** (decision 162): `has_real_domain` is a
  *guard* at the two places this system mints an address and a *courtesy* at the
  one place it hands one to a provider. `app.domain.payments.payerEmail` delegates
  to it, so the two refusals have one opinion between them and two sentences.
- **The rule is checked where an address is minted, never by the aggregate**
  (decision 163) - because `User.__post_init__` also runs on load, and enforcing it
  there would make every stranded account unreadable rather than fixable.
- **`POST /users/me/email-changes` and `POST /email-changes/confirm`**, plus
  `change-email` and `confirm-email` beside them: a session *and* the current
  password to ask, a 256-bit mailed token to answer, one atomic claim, and one
  commit that moves the address and spends the token together (decisions 165-171).
- **Two mails with two different failure rules** (decision 172): the one carrying
  the credential must arrive and refuses the request if it cannot; the notice to the
  address being left is best-effort and reports rather than raises, because it *will*
  bounce for exactly the accounts this endpoint exists to rescue.
- **With no mail account the change applies on the password proof alone**
  (decision 173), so a fresh install can still escape a bad address - and both
  presentations say so rather than being quietly less safe.

What it deliberately does not do, with entries under `### Still open`: **rate limit
the endpoints that mail a credential** (decision 86 - it had two members when this
paragraph was written, three by the end of the slice, and five after the password
reset), **purge settled `email_changes` rows**, and **revoke sessions on a change**
(decided against, decision 175 - and note that the password reset decided the other
way for its own reasons, which is what makes the pair worth reading together). And
the read-only audit it was asked for turned up five more
entry points of the same shape, one of which - a plan edited into a currency its
wallet does not hold - takes down a whole tick rather than one request. Those are in
`### Still open` in full, with the line numbers, and none of them was changed.

**Its ten verification items were then run by hand, and the run is recorded in the
section above** - including the two that refused to go as planned and the one whose
evidence came out stronger than the plan asked for. Nothing there contradicted a
decision, and all three findings it produced are defects the plan could not have
predicted, which is 3b's lesson arriving a second time in a gentler form: what a live
run is for is not confirmation.

### Phase 4 - Production

- Migrations through a tool (Alembic), replacing the hand-rolled PRAGMA-guarded
  `ALTER`s that `sqlite_unit_of_work.py` already flags as the manual version.
- `docker compose`, a reverse proxy with TLS, and a VPS. The first two exist as
  configuration - `Dockerfile`, `compose.yaml`, `Caddyfile`, `.env.example` - and
  are argued in "The deployment" above. Neither has been run, and the VPS is not
  a file: DNS, a firewall and a host remain somebody's afternoon.
- **Backups with a tested restore.** A backup nobody has restored is a belief,
  not a backup, and this is the one item whose absence is unrecoverable.
- Structured logging and error tracking.
- CI running the suite on every push. *"N tests that nobody runs automatically are a
  liability that feels like an asset"* - and note what has happened to this bullet:
  it used to name a number, 1,191, and by the time anybody read it the number was
  wrong. That is the argument for the bullet rather than an embarrassment about it. A
  suite whose size has to be maintained by hand is a suite whose size nobody knows,
  and the figure is left out here rather than corrected because the next correction
  would go stale the same way.

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



## Key Implememtations
double-entry accounting

transaction ledgers

idempotency

payment processing

reconciliation

audit trails

authorization

fraud/risk controls

concurrency handling

database transaction boundaries

encryption/key management

event sourcing

immutable financial records

regulatory reporting

