"""SQLite-backed Unit of Work.

One SqliteUnitOfWork is one connection with an open transaction. The service
opens one per business operation: the reads and writes made through its
repositories all land in that single transaction and become durable together on
commit() - or are all discarded by rollback().
"""

import sqlite3
import uuid
from datetime import datetime
from decimal import Decimal

from app.application.unit_of_work import UnitOfWork
from app.domain.money.currency import Currency
from app.domain.money.fundKind import FundKind
from app.domain.money.walletStatus import WalletStatus
from app.infrastructure.persistence.serialization import (
    datetime_to_text,
    enum_to_text,
    money_to_text,
    text_to_enum,
    text_to_money,
    uuid_to_text,
)
from app.infrastructure.repositories.sqlite_plan_notice_repository import (
    SqlitePlanNoticeRepository,
)
from app.infrastructure.repositories.sqlite_confirmation_repository import (
    SqliteConfirmationRepository,
)
from app.infrastructure.repositories.sqlite_email_change_repository import (
    SqliteEmailChangeRepository,
)
from app.infrastructure.repositories.sqlite_notification_repository import (
    SqliteNotificationRepository,
)
from app.infrastructure.repositories.sqlite_password_reset_repository import (
    SqlitePasswordResetRepository,
)
from app.infrastructure.repositories.sqlite_phone_verification_repository import (
    SqlitePhoneVerificationRepository,
)
from app.infrastructure.repositories.sqlite_profile_repository import (
    SqliteProfileRepository,
)
from app.infrastructure.repositories.sqlite_rate_limit_repository import (
    SqliteRateLimitRepository,
)
from app.infrastructure.repositories.sqlite_outbound_message_repository import (
    SqliteOutboundMessageRepository,
)
from app.infrastructure.repositories.sqlite_plan_run_repository import (
    SqlitePlanRunRepository,
)
from app.infrastructure.repositories.sqlite_savings_plan_repository import (
    SqliteSavingsPlanRepository,
)
from app.infrastructure.repositories.sqlite_transaction_repository import (
    SqliteTransactionRepository,
)
from app.infrastructure.repositories.sqlite_password_credential_repository import (
    SqlitePasswordCredentialRepository,
)
from app.infrastructure.repositories.sqlite_session_repository import (
    SqliteSessionRepository,
)
from app.infrastructure.repositories.sqlite_user_repository import (
    SqliteUserRepository,
)
from app.infrastructure.repositories.sqlite_wallet_repository import (
    SqliteWalletRepository,
)

SCHEMA = """
-- Users come first, because every wallet names one and a wallet is only
-- meaningful because somebody owns it. There is deliberately no foreign key
-- from ``wallets.user_id`` to this table, and the reason is a SQLite one rather
-- than a design preference: ``wallets`` already exists on every database in the
-- wild, and a foreign key cannot be added to an existing column without
-- rebuilding the whole table. Integrity between the two is the repository's job
-- for now, and the rebuild is recorded in the README as a later candidate.
CREATE TABLE IF NOT EXISTS users (
    user_id        TEXT PRIMARY KEY,
    -- Optional, and the optionality is the point rather than a relaxation: an
    -- account is identified by an address, a number, or both. The rule that at
    -- least one of the two is present is ``User``'s (``InvalidUserIdentifierError``)
    -- and there is deliberately no CHECK beside it here - and this is *not* the
    -- usual "one rule in one place" argument, because ``profiles`` below makes
    -- the opposite call for the invariant it can hold and says why. The
    -- difference is what SQL can see. That one compares two columns to each
    -- other, which is a fact about the stored row and nowhere else; this one is
    -- about values that are *normalised* before they arrive, by
    -- ``checked_email`` and ``checked_phone``, so a CHECK could only catch the
    -- case where both columns are literally NULL - which the constructor refuses
    -- and no repository can write. A constraint that restates a rule and catches
    -- less of it is worse than none, because it reads as the guard.
    --
    -- NULL is therefore ordinary, and SQLite's UNIQUE permits any number of them,
    -- which is what lets every phone-only account coexist. This is the argument
    -- ``google_subject`` below already carries, arriving for a second column.
    email          TEXT UNIQUE,            -- folded to lowercase by the aggregate
    -- The second identifier, and the only one this system can deliver a
    -- credential to without a mail account. Stored in the canonical
    -- country-code-first form ``fold_phone`` produces - digits only, no ``+`` -
    -- so that one number has one spelling and this UNIQUE bounds what it looks
    -- like it bounds. See ``app.domain.identity.phoneNumber``.
    --
    -- Note what is *not* stored here: whether the number was ever verified. A
    -- verified number is a fact about a proof rather than about this column, and
    -- an account is only created once that proof has happened - so an account
    -- holding a number is one whose holder demonstrated they hold the handset.
    -- The proof is a table of its own, which arrives with the signup flow that
    -- produces one; it is not here, for the reason ``password_credentials``
    -- gives about itself: every column on ``users`` is one forgotten omission
    -- away from being rendered to whoever asks who they are.
    phone          TEXT UNIQUE,
    -- The Google account this user signs in with, when they have one. NULL is
    -- the ordinary case for an account created any other way, and SQLite's
    -- UNIQUE permits any number of NULLs - which is exactly right here, and is
    -- why an empty string would be wrong instead: every Google-less account
    -- would collide on "" and the second signup would fail against the first.
    --
    -- Deliberately *not* counted as an identifier for the at-least-one rule,
    -- because a login Google performs is not something this system can send a
    -- credential to.
    google_subject TEXT UNIQUE,
    created_at     TEXT NOT NULL           -- ISO moment
);

-- How a user proves they are that user, and deliberately a table of its own
-- rather than two more columns on ``users``. ``User`` is the identity - who a
-- wallet belongs to - and it carries no credential by design; see its docstring,
-- and ``PasswordCredential`` for what went here instead. The practical half of
-- the reason is that a ``User`` is loaded by every authenticated request and
-- rendered by ``translate.user_out``, so a hash living on it would be one
-- forgotten omission away from being served to a client.
--
-- There is no foreign key to ``users``, for the same SQLite reason recorded
-- above: the constraint cannot be added later without rebuilding, and the
-- repository is where integrity lives for now.
CREATE TABLE IF NOT EXISTS password_credentials (
    -- The primary key, so a password change is an update to this row rather than
    -- a second credential. One account, one password.
    user_id       TEXT PRIMARY KEY,
    -- The *encoded* hash: salt, cost parameters and digest in one string, as
    -- argon2 emits it. Storing the parameters alongside is what allows the cost
    -- to be raised later without invalidating every existing password.
    password_hash TEXT NOT NULL,
    updated_at    TEXT NOT NULL           -- ISO moment
);

-- A record that somebody proved who they are, and until when. Not a credential:
-- this table holds no secret that can be presented. The server hashes whatever
-- token arrives and looks the *hash* up, so a copy of this table is a list of
-- sessions that cannot be used - which is what "hashed at rest" buys, and it is a
-- different property from the one a password hash has.
--
-- There is no ``revoked_at``. Revocation is deletion (decision 49): a deleted row
-- cannot be misread, where a flag obliges every query to remember to filter on it
-- and the one that forgets is a session that never ended.
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    -- UNIQUE for the same reason ``users.email`` is: two sessions sharing a token
    -- cannot realistically happen, and if it did - a broken RNG, a row copied by
    -- hand - the store refuses it rather than letting one token resolve to two
    -- identities. It is also the index every authenticated request reads through.
    token_hash TEXT NOT NULL UNIQUE,
    issued_at  TEXT NOT NULL,             -- ISO moment
    expires_at TEXT NOT NULL             -- ISO moment; absolute, never extended
);

CREATE TABLE IF NOT EXISTS wallets (
    wallet_id         TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL,
    currency          TEXT NOT NULL,
    status            TEXT NOT NULL,
    available_balance TEXT NOT NULL    -- decimal as text, e.g. "10000.00"
    -- One wallet per currency, per person, while a wallet is open - and it is not
    -- written here, because SQLite has no partial table constraint and this rule
    -- has to be partial. The ``CLOSED`` rows are excluded so that closing a wallet
    -- frees its currency, and a rule that could not be expressed that way would be
    -- a different rule. It is an index created by
    -- ``_migrate_one_wallet_per_currency``, which every connection runs, so a
    -- database that already exists gets it too. See decision 267.
);

CREATE TABLE IF NOT EXISTS funds (
    fund_id       TEXT PRIMARY KEY,
    wallet_id     TEXT NOT NULL REFERENCES wallets(wallet_id),
    name          TEXT NOT NULL,    -- the handle a human types, e.g. "Vacation"
    kind          TEXT NOT NULL,    -- enum name: PERSONAL / BUSINESS
    balance       TEXT NOT NULL,    -- decimal as text, e.g. "50000.00"
    maturity_date TEXT,             -- ISO date; NULL means no maturity at all
    created_at    TEXT NOT NULL,    -- ISO moment; fixes the payout draw order
    -- The two moments a business pot's early-payment exemption is judged on. See
    -- ``Fund.authorises_early_payout`` for the rule; what matters for the schema
    -- is that ``sealed_at`` is always set and ``first_funded_at`` is NULL exactly
    -- while the pot is empty. Together they say whether any commitment predates
    -- the money, which is the one question the pot cannot answer from its balance.
    sealed_at      TEXT NOT NULL,   -- ISO moment; re-stamped on every extend
    first_funded_at TEXT,           -- ISO moment; NULL until money first arrives
    -- There is deliberately no ``currency`` column. A fund is in its wallet's
    -- currency by construction - ``Wallet.__post_init__`` refuses one that is
    -- not - so a stored copy would be a second place for the same fact to live,
    -- free to disagree with the wallet it belongs to. See decision 33.
    --
    -- The UNIQUE is not tidiness either: a fund's name is the handle a human
    -- types (``fund release Vacation 5000``), so two funds called "Vacation" on
    -- one wallet would make that command ambiguous and the failure would be
    -- "some fund, chosen by the wrong rule" rather than "no such fund". The
    -- aggregate refuses a duplicate before it gets here; this is the backstop
    -- for the gap between the check and the write. See decision 37.
    UNIQUE (wallet_id, name)
);

CREATE TABLE IF NOT EXISTS transactions (
    transaction_id     TEXT PRIMARY KEY,
    wallet_id          TEXT NOT NULL REFERENCES wallets(wallet_id),
    type               TEXT NOT NULL,
    amount             TEXT NOT NULL,
    currency           TEXT NOT NULL,
    internal_reference TEXT NOT NULL UNIQUE,   -- idempotency backstop
    provider_reference TEXT,
    narration          TEXT,
    metadata           TEXT,                   -- JSON
    destination        TEXT,                   -- JSON, payouts only
    fund_id            TEXT,                   -- which pot moved, when one did
    status             TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    completed_at       TEXT,
    reversed_at        TEXT
);

CREATE TABLE IF NOT EXISTS savings_plans (
    plan_id        TEXT PRIMARY KEY,
    wallet_id      TEXT NOT NULL REFERENCES wallets(wallet_id),
    -- Who owns this plan - the same person who owns ``wallet_id``, recorded
    -- here as well. A plan cannot reach its wallet's owner without loading the
    -- wallet, and loading a wallet requires naming its owner; the scheduler,
    -- which runs everybody's plans, needs this column to break out of that
    -- circle without a privileged read. See ``SavingsPlan.user_id`` for the
    -- full reasoning and for what the duplication costs.
    --
    -- No REFERENCES users(user_id), and that is the same choice the migration
    -- adding this column to an existing database is forced into: SQLite refuses
    -- ALTER TABLE ADD COLUMN with a REFERENCES clause unless the default is
    -- NULL, and this column is NOT NULL. Declaring one here would leave fresh
    -- databases and migrated ones different shapes.
    user_id        TEXT NOT NULL,
    name           TEXT NOT NULL,    -- what the user calls this plan, e.g. "Rent"
    source         TEXT NOT NULL,
    schedule       TEXT NOT NULL,    -- JSON: cadence name + anchor moment
    instructions   TEXT NOT NULL,    -- JSON array; value objects live with their root
    status         TEXT NOT NULL,
    completed_runs INTEGER NOT NULL, -- drift guard: next due = anchor + n, never anchor + 1
    ends_on        TEXT,             -- ISO date; NULL means open-ended
    created_at     TEXT NOT NULL,
    -- Which pot this plan draws on, for a LOCKED-source plan. NULL is not "no
    -- pot wanted" - it is the *legacy pooled draw*: a plan saved before pots
    -- could be named, which spends the oldest matured pot first and records
    -- nothing. Every plan created now names its pot, and a plan that spends the
    -- available balance never has one. See ``Wallet.payout_from_locked``.
    fund_id        TEXT
);

CREATE TABLE IF NOT EXISTS plan_runs (
    plan_id     TEXT NOT NULL REFERENCES savings_plans(plan_id),
    due_at      TEXT NOT NULL,   -- ISO moment of the occurrence this run is for
    status      TEXT NOT NULL,
    reason      TEXT,            -- enum name; blocked runs only, NULL otherwise
    recorded_at TEXT NOT NULL,
    -- The natural key, and the whole reason a retry is coherent: one plan has
    -- at most one run per occurrence, so re-saving rewrites rather than
    -- appends. A run cannot be identified by a generated id because it was
    -- never anything but (plan, occurrence).
    PRIMARY KEY (plan_id, due_at)
);

CREATE TABLE IF NOT EXISTS plan_notices (
    plan_id   TEXT NOT NULL REFERENCES savings_plans(plan_id),
    due_at    TEXT NOT NULL,   -- ISO moment of the occurrence being announced
    raised_at TEXT NOT NULL,   -- the tick's own notion of now when it was raised
    -- The same natural key as plan_runs, and for the same reason: a notice is
    -- nothing but (plan, occurrence), so it has no identity to generate. The
    -- primary key is what makes the warning once-only - a second tick inside
    -- the same window inserts nothing rather than warning the user again.
    --
    -- It is enforced here rather than by a "have we warned yet?" query for a
    -- reason worth keeping: a check followed by a write has a gap between them,
    -- and two overlapping ticks could both look, both find nothing, and both
    -- warn. The constraint closes the gap because the decision and the write
    -- are the same operation.
    PRIMARY KEY (plan_id, due_at)
);

CREATE TABLE IF NOT EXISTS outbound_messages (
    plan_id     TEXT NOT NULL REFERENCES savings_plans(plan_id),
    due_at      TEXT NOT NULL,   -- ISO moment of the occurrence being announced
    recipient   TEXT NOT NULL,   -- captured at enqueue, not resolved at send
    subject     TEXT NOT NULL,
    body        TEXT NOT NULL,   -- composed once; never re-rendered
    status      TEXT NOT NULL,   -- enum name: PENDING / SENT / EXPIRED
    attempts    INTEGER NOT NULL,
    last_error  TEXT,            -- the last delivery failure, NULL if none yet
    created_at  TEXT NOT NULL,
    settled_at  TEXT,            -- when it was sent or expired; NULL while owed
    -- The same natural key as plan_notices, and the same reason: a message is
    -- nothing but (plan, occurrence). The primary key is what makes a
    -- double-send unrepresentable - the notice claim already stops a second
    -- warning being raised, but a table whose rows become emails should not
    -- rely on another table's discipline to keep from mailing someone twice.
    --
    -- Deliberately a *separate* table from plan_notices despite sharing this
    -- key 1:1. A notice is inserted once and never touched - it has no truer
    -- second version (see SqlitePlanNoticeRepository.claim). A message is
    -- updated on every attempt, and its row carries a last_error. Keeping
    -- delivery state in its own row is what keeps "did the email go out?" out
    -- of reach of anything deciding whether a payout may proceed.
    PRIMARY KEY (plan_id, due_at)
);

CREATE TABLE IF NOT EXISTS notifications (
    event_key  TEXT NOT NULL,     -- derived from the event; see domain eventKey.py
    kind       TEXT NOT NULL,     -- enum name, e.g. PAYOUT_SUCCEEDED
    subject_id TEXT NOT NULL,     -- the plan or wallet the event is about
    recipient  TEXT NOT NULL,     -- captured at enqueue, not resolved at send
    subject    TEXT NOT NULL,
    body       TEXT NOT NULL,     -- composed once; never re-rendered
    status     TEXT NOT NULL,     -- enum name: PENDING / SENT / EXPIRED
    attempts   INTEGER NOT NULL,
    last_error TEXT,              -- the last delivery failure, NULL if none yet
    created_at TEXT NOT NULL,
    settled_at TEXT,              -- when it was sent or expired; NULL while owed
    -- Every message about something that *happened*, of every kind, in one
    -- table. Deliberately not keyed on (plan_id, due_at) as outbound_messages
    -- is: a deposit has no plan anywhere in it, and a payout receipt is not
    -- about an upcoming occurrence - the identity is the *event*, and an event
    -- is not always plan-shaped. The key is derived (see
    -- app/domain/notifications/eventKey.py), namespaced with its kind so a plan
    -- key and a wallet key cannot collide, and is the primary key so a duplicate
    -- send is unrepresentable rather than merely unlikely.
    --
    -- One table for every kind, rather than one per kind. The delivery rules -
    -- retry on failure, record the error, never raise, never re-send a settled
    -- row - are identical for all of them, so a table per kind would mean a
    -- drain per kind, each free to drift from the others in exactly the way that
    -- is hardest to notice.
    --
    -- There is deliberately no ``expires_at``. A warning stops being worth
    -- sending when its occurrence arrives; a receipt never does, because "your
    -- payout went out" does not become false. A nullable column that no code
    -- writes would advertise expiry as a live concept, so the column stays
    -- absent until a kind arrives that genuinely has a shelf life - and adding
    -- it then will be a migration, which is the price decision 18 named.
    --
    -- No migration function, for the reason the absence of one for plan_notices
    -- and outbound_messages already records: CREATE TABLE IF NOT EXISTS above
    -- creates a missing table on an existing database for free. Only a change to
    -- a table already on disk is a migration.
    PRIMARY KEY (event_key)
);

-- A recorded request to move money out of a wallet, waiting to be answered.
-- This is the second-level confirmation: nothing has been done when a row here
-- is written, which is the opposite of a PENDING transaction one table up - that
-- is a movement that *has* happened, whose wallet is already debited, and which
-- cannot yet be called finished because the far end has not confirmed it. The
-- two words are kept apart deliberately; see ``Confirmation``.
--
-- There is no migration function for this table, for the reason recorded against
-- ``plan_notices``, ``outbound_messages``, ``users``, ``password_credentials``,
-- ``sessions`` and ``notifications`` above: CREATE TABLE IF NOT EXISTS creates a
-- missing table on a database already in the wild for free. Adding a table is
-- not a migration; changing a table that is already on disk is.
CREATE TABLE IF NOT EXISTS confirmations (
    confirmation_id    TEXT PRIMARY KEY,
    -- Who asked. The same person who owns ``wallet_id``, recorded here as well
    -- and for the same reason ``savings_plans.user_id`` is: every read of a
    -- confirmation is scoped to its requester, and this is the column that scope
    -- reads. No REFERENCES users(user_id), matching that column's note.
    user_id            TEXT NOT NULL,
    wallet_id          TEXT NOT NULL REFERENCES wallets(wallet_id),
    kind               TEXT NOT NULL,   -- enum name: WITHDRAWAL / PAYOUT_* / CLOSE
    -- The amount, as text, and its currency beside it. Both are NULL for a
    -- CLOSE, which moves no money and so has no figure to record - a real answer
    -- rather than missing data, and the aggregate refuses a CLOSE that carries
    -- one. The currency travels with the amount because a Money is meaningless
    -- without it; see ``SqliteConfirmationRepository._values``.
    amount             TEXT,
    currency           TEXT,
    destination        TEXT,            -- JSON, payouts only; NULL otherwise
    fund_name          TEXT,            -- locked payouts only; NULL = pooled draw
    -- The caller's idempotency key, or one minted when they sent none. This is
    -- the key that will be handed to the ledger row underneath, namespaced with
    -- the wallet by ``app.domain.money.reference`` exactly as it always was.
    internal_reference TEXT NOT NULL,
    -- AWAITING or CONFIRMED. There is deliberately no stored EXPIRED: expiry is
    -- *checked*, not swept, and the API reports an awaiting request past its
    -- window as expired from ``expires_at`` alone - so nothing has to write on a
    -- read. See ``Confirmation.status_as_of``.
    status             TEXT NOT NULL,
    created_at         TEXT NOT NULL,   -- ISO moment
    expires_at         TEXT NOT NULL,   -- ISO moment; absolute, never extended
    -- Audit: the ledger row this request produced. NULL for a CLOSE (which writes
    -- none) and NULL for a request that was never answered.
    transaction_id     TEXT,
    -- The reference chain, and it is load-bearing rather than tidiness:
    -- one reference -> at most one confirmation -> at most one execution attempt.
    --
    -- Without it, a client retrying a request it never saw the response to would
    -- record a second request under the same key, and the *second* confirm would
    -- reach ``WalletOperation.execute`` holding a reference the first attempt had
    -- already spent. That lookup is global and returns the row whatever its
    -- status - including FAILED - so the retry would be handed the old row, move
    -- nothing, and report success. The constraint is what makes that unreachable:
    -- a new attempt must be a new request, and a new request must be a new key.
    --
    -- It is also what makes ``find`` safe to scope to the wallet alone, and what
    -- lets a client's own key be handed back to them without leaking anything:
    -- the key cannot name a request on somebody else's wallet.
    UNIQUE (wallet_id, internal_reference)
);

-- A requested move of an account's address, waiting to be proven. Nothing has
-- changed while a row here is AWAITING: the account still holds the address it
-- held, and what is outstanding is a token mailed to the *new* address - proving
-- somebody can read mail there before the account is moved onto it.
--
-- The lifecycle is ``confirmations``' and the subject is not. That table is a
-- request to move money out and names a wallet; this one moves an identity and has
-- no wallet anywhere in it. The shape is written out again rather than shared for
-- the reason ``instruction.py`` gives about coupling two aggregates to
-- deduplicate a few lines - so each copy is free to move on its own.
--
-- There is no migration function for this table, for the reason recorded against
-- ``plan_notices``, ``outbound_messages``, ``users``, ``password_credentials``,
-- ``sessions``, ``notifications`` and ``confirmations`` above: CREATE TABLE IF NOT
-- EXISTS creates a missing table on a database already in the wild for free.
-- Adding a table is not a migration; changing a table that is already on disk is.
CREATE TABLE IF NOT EXISTS email_changes (
    -- The request's own id, and the row's identity. **Not the key this table is
    -- *about*** - see ``user_id`` below, which is the one that carries the rule.
    email_change_id TEXT PRIMARY KEY,
    -- **UNIQUE, and this is where "one pending change per account" lives.** One row
    -- per user, so a second request supersedes the first rather than joining it -
    -- and superseding is the remedy for a mistyped address, where the alternative
    -- would be a person stuck with a token sent to an address they typed wrong. No
    -- REFERENCES users(user_id), matching every other owner column here.
    --
    -- It is the UNIQUE index rather than the primary key, and the distinction is a
    -- SQLite rule rather than a preference: a table may declare only one PRIMARY
    -- KEY, so an id column and a per-account key cannot both be one. UNIQUE is the
    -- whole of what the upsert needs - ``ON CONFLICT(user_id)`` targets any unique
    -- index - and it enforces the same one-row-per-account guarantee.
    --
    -- That the row is keyed this way is also what lets a *spent* request stay in
    -- the table: the last change per account survives as a small audit trail, and
    -- its survival is what makes "this token was already used" distinguishable
    -- from "this token never existed". See ``EmailChangeRepository``.
    user_id         TEXT NOT NULL UNIQUE,
    new_email       TEXT NOT NULL,   -- folded to lowercase by the aggregate
    -- The hash, never the token, and UNIQUE for ``sessions``' reason: two requests
    -- sharing a hash cannot realistically happen, and if it did - a broken RNG, a
    -- row copied by hand - the store refuses it rather than letting one token
    -- answer two changes. It is also the index the claim reads through.
    --
    -- Hashed at rest buys the same thing it buys for a session and no more: the
    -- server hashes whatever arrives and looks *that* up, so a copy of this table
    -- holds no token anybody can present. That matters more here than for a
    -- session, because what a token in this table authorises is moving an account
    -- rather than spending one wallet's balance.
    token_hash      TEXT NOT NULL UNIQUE,
    -- AWAITING or CONFIRMED. There is deliberately no stored EXPIRED: expiry is
    -- *checked*, not swept, and a request past its window is reported as expired
    -- from ``expires_at`` alone - so nothing has to write on a read. See
    -- ``EmailChange.status_as_of``.
    status          TEXT NOT NULL,
    requested_at    TEXT NOT NULL,   -- ISO moment
    expires_at      TEXT NOT NULL,   -- ISO moment; absolute, never extended
    -- When the request was answered: set by the same UPDATE that sets CONFIRMED,
    -- and NULL exactly while it is AWAITING. The pair is validated on load, so a
    -- row whose two halves disagree fails loudly rather than being interpreted.
    settled_at      TEXT
);

-- A requested password reset, waiting to be proven. Nothing has changed while a
-- row here is AWAITING: the account still has the password it had and every
-- session it held is still live. What is outstanding is a token mailed to the
-- address the account already holds - proving somebody can read mail there before
-- the account's password is replaced.
--
-- The lifecycle is ``email_changes``' and the subject is not, and the difference
-- shows in the columns rather than in prose: **there is no payload column here.**
-- ``email_changes`` carries ``new_email`` because the address being moved to is
-- the fact the request authorises and the fact its mail must name; this request
-- authorises a *new password*, and there is deliberately nowhere to put one. A
-- password is written down exactly once in this system, as an argon2 hash in
-- ``password_credentials`` below, and a second copy - even hashed, even briefly -
-- would be a second thing to protect for no gain. The password arrives with the
-- confirm and lives in a local variable for the length of one call.
--
-- The shape is written out again rather than shared with ``email_changes`` for the
-- reason ``instruction.py`` gives about coupling two aggregates to deduplicate a
-- few lines - so each copy is free to move on its own.
--
-- There is no migration function for this table, for the reason recorded against
-- ``plan_notices``, ``outbound_messages``, ``users``, ``password_credentials``,
-- ``sessions``, ``notifications``, ``confirmations`` and ``email_changes`` above:
-- CREATE TABLE IF NOT EXISTS creates a missing table on a database already in the
-- wild for free. Adding a table is not a migration; changing a table that is
-- already on disk is.
CREATE TABLE IF NOT EXISTS password_resets (
    -- The request's own id, and the row's identity. **Not the key this table is
    -- *about*** - see ``user_id`` below, which is the one that carries the rule.
    password_reset_id TEXT PRIMARY KEY,
    -- **UNIQUE, and this is where "one pending reset per account" lives.** One row
    -- per user, so a second request supersedes the first rather than joining it -
    -- which is the remedy for a code that never arrived, and the reason a second
    -- request cannot leave two live codes racing. No REFERENCES users(user_id),
    -- matching every other owner column here.
    --
    -- It is the UNIQUE index rather than the primary key for the SQLite reason
    -- ``email_changes`` gives: a table may declare only one PRIMARY KEY, so an id
    -- column and a per-account key cannot both be one. ``ON CONFLICT(user_id)``
    -- targets any unique index and enforces the same one-row-per-account
    -- guarantee.
    --
    -- That the row is keyed this way is also what lets a *spent* request stay in
    -- the table, and its survival is what makes "this code was already used"
    -- distinguishable from "this code never existed". See
    -- ``PasswordResetRepository``.
    user_id           TEXT NOT NULL UNIQUE,
    -- The hash, never the token, and UNIQUE for ``sessions``' reason: two requests
    -- sharing a hash cannot realistically happen, and if it did - a broken RNG, a
    -- row copied by hand - the store refuses it rather than letting one code answer
    -- two resets. It is also the index the claim reads through.
    --
    -- Hashed at rest buys the same thing it buys for a session and no more: the
    -- server hashes whatever arrives and looks *that* up, so a copy of this table
    -- holds no token anybody can present. The stakes are the highest of the three
    -- tables carrying this shape, because what a token here authorises is
    -- replacing the account's password outright.
    token_hash        TEXT NOT NULL UNIQUE,
    -- AWAITING or CONFIRMED. There is deliberately no stored EXPIRED: expiry is
    -- *checked*, not swept, and a request past its window is reported as expired
    -- from ``expires_at`` alone - so nothing has to write on a read. See
    -- ``PasswordReset.status_as_of``.
    status            TEXT NOT NULL,
    requested_at      TEXT NOT NULL,   -- ISO moment
    expires_at        TEXT NOT NULL,   -- ISO moment; absolute, never extended
    -- When the request was answered: set by the same UPDATE that sets CONFIRMED,
    -- and NULL exactly while it is AWAITING. The pair is validated on load, so a
    -- row whose two halves disagree fails loudly rather than being interpreted.
    settled_at        TEXT
);

-- Proving a number belongs to the person typing it, before any account holds it.
--
-- **This is the one request table keyed on an identifier rather than on a ``user_id``,
-- and that is the whole of what is different about it.** ``email_changes`` and
-- ``password_resets`` above are requests made *by* an account about an account, so the
-- account's id is what ties a row to everything else in the schema. A signup has no
-- account yet - the account is what answering this request creates - so the subject is
-- the number itself and the number is the key. There is deliberately nothing to
-- reference and nothing that could be referenced.
--
-- The lifecycle is the same as the two above and is written out rather than inherited,
-- for the reason ``email_changes`` and ``password_resets`` give about each other: three
-- copies free to move on their own beat one shared base free to move all three at once.
CREATE TABLE IF NOT EXISTS phone_verifications (
    -- The request's own id, and the row's identity. **Not the key this table is
    -- *about*** - see ``phone`` below, which is the one that carries the rule.
    phone_verification_id TEXT PRIMARY KEY,
    -- **UNIQUE, and this is where "one pending verification per number" lives.** One
    -- row per number, so a second request supersedes the first rather than joining it -
    -- which is the remedy for a text that never arrived, and the reason a second
    -- request cannot leave two live codes racing. No REFERENCES users(phone), for the
    -- reason every other owner column here gives, and one more that is this table's
    -- alone: there is no such column to reference until this row is answered.
    --
    -- It is the UNIQUE index rather than the primary key for the SQLite reason
    -- ``email_changes`` gives: a table may declare only one PRIMARY KEY, so an id column
    -- and a per-number key cannot both be one. ``ON CONFLICT(phone)`` targets any unique
    -- index and enforces the same one-row-per-number guarantee.
    --
    -- **Stored folded**, which the schema cannot enforce and the aggregate does: every
    -- write goes through ``checked_phone``, so ``08012345678`` and ``+2348012345678``
    -- cannot occupy two rows here and produce two accounts for one handset. The same
    -- guarantee ``users.email`` gets from ``fold_email``.
    --
    -- That the row is keyed this way is also what lets a *spent* request stay in the
    -- table, and its survival is what makes "this code was already used" distinguishable
    -- from "this code never existed". See ``PhoneVerificationRepository``.
    phone             TEXT NOT NULL UNIQUE,
    -- The hash, never the token, and UNIQUE for ``sessions``' reason: two requests
    -- sharing a hash cannot realistically happen, and if it did - a broken RNG, a row
    -- copied by hand - the store refuses it rather than letting one code answer two
    -- signups. It is also the index the claim reads through, and the *only* thing the
    -- claim reads through: the number is taken from the claimed row rather than from
    -- what a caller supplied.
    --
    -- Hashed at rest buys the same thing it buys for a session and no more: the server
    -- hashes whatever arrives and looks *that* up, so a copy of this table holds no
    -- token anybody can present.
    token_hash        TEXT NOT NULL UNIQUE,
    -- AWAITING or CONFIRMED. There is deliberately no stored EXPIRED: expiry is
    -- *checked*, not swept, and a request past its window is reported as expired from
    -- ``expires_at`` alone - so nothing has to write on a read. See
    -- ``PhoneVerification.status_as_of``.
    status            TEXT NOT NULL,
    requested_at      TEXT NOT NULL,   -- ISO moment
    expires_at        TEXT NOT NULL,   -- ISO moment; absolute, never extended
    -- When the request was answered: set by the same UPDATE that sets CONFIRMED, and
    -- NULL exactly while it is AWAITING. The pair is validated on load, so a row whose
    -- two halves disagree fails loudly rather than being interpreted.
    settled_at        TEXT
);

-- Who an account holder is, as opposed to which account they hold.
--
-- **A table of its own rather than columns on ``users``, and the reason is the
-- same one ``password_credentials`` above gives.** ``users`` is read by every
-- authenticated request and its row is rendered by ``translate.user_out``; every
-- column added to it is one forgotten omission away from being served to
-- whoever asks who they are. A legal name, a date of birth and a home address
-- are exactly the values that must not travel that way, so they live here,
-- keyed by the same ``user_id``, reached only by code that meant to reach them.
--
-- The split is not merely tidy - it is what keeps the two questions separable.
-- ``users`` answers "which account is this", which is security-shaped, and this
-- answers "who is this person", which is compliance-shaped. Merging them would
-- make one row's read permissions the *wider* of the two.
CREATE TABLE IF NOT EXISTS profiles (
    -- The primary key, and **not a synthetic id beside it**. A person has one
    -- profile, so a second row for the same user is not a state to be validated
    -- against - it is a state that cannot be written. That is the difference
    -- from ``email_changes`` and ``password_resets`` above, which need an id
    -- column *and* a per-account unique index because a request is a thing that
    -- happens repeatedly; a profile is a thing that exists once.
    --
    -- No REFERENCES users(user_id), matching every other owner column here. The
    -- foreign key would document an intent this project has decided not to
    -- enforce in the schema - see the note against ``wallets``.
    user_id           TEXT PRIMARY KEY,
    -- The one required field. A profile with nothing in it is a row that exists
    -- to say nothing, so the aggregate refuses it and the column is NOT NULL to
    -- agree.
    display_name      TEXT NOT NULL,
    -- Everything below is nullable, and that is the design rather than a stage
    -- of work: a person fills this in over more than one sitting, and a given
    -- name with no surname is a real state to be in the middle of. The tier is
    -- derived from which of these are present, so "half-filled" needs no
    -- representation of its own.
    legal_first_name  TEXT,
    legal_last_name   TEXT,
    -- **The only ``date`` column in the schema, and it is stored as a date.**
    -- Every other moment here is an ISO *datetime*, because every other moment
    -- is a thing that happened at a time. A birth date is a calendar fact with
    -- no time attached - nobody knows what time of day they were born, and a
    -- stored "T00:00:00" would invent a precision that does not exist. It also
    -- round-trips: ``text_to_datetime`` would hand the aggregate a ``datetime``,
    -- which ``Profile.__post_init__`` refuses, so the column type and the
    -- aggregate's check have to agree on which of the two this is.
    date_of_birth     TEXT,             -- ISO date, e.g. "1990-01-31"
    phone             TEXT,
    -- A two-letter code, uppercased by the aggregate. A shape rule rather than
    -- an ISO 3166 membership check - see ``Profile._checked_country`` - so a
    -- row can hold a code that is shaped right and not real, and the thing that
    -- would catch that is the identity check this system has not built.
    country           TEXT,
    address_line      TEXT,
    created_at        TEXT NOT NULL,    -- ISO moment
    updated_at        TEXT NOT NULL,    -- ISO moment; never before created_at
    -- The one invariant the schema can hold and the aggregate also holds, which
    -- is worth having twice: it is the pair that makes "when did this person
    -- last change their details" answerable, and a row where the second is
    -- before the first is a contradiction a reader could not interpret.
    CHECK (updated_at >= created_at)
);

-- There is no migration function for this table, for the reason recorded against
-- ``plan_notices``, ``outbound_messages``, ``users``, ``password_credentials``,
-- ``sessions``, ``notifications``, ``confirmations``, ``email_changes``,
-- ``password_resets`` and ``phone_verifications`` above: CREATE TABLE IF NOT EXISTS
-- creates a missing table on a database already in the wild for free. Adding a table
-- is not a migration; changing a table that is already on disk is.
--
-- **Which is what makes "Tier 0 for everyone on rollout" free.** Every account
-- that exists today has no row here, ``ProfileRepository.find_for_user`` answers
-- ``None`` for it, and ``tier_for(None)`` answers UNVERIFIED. There is nothing
-- to backfill and no moment at which the answers could disagree - the absence of
-- a row *is* the unverified state, so no write is needed to put anybody in it.

-- How often each subject may knock on each limited door, as the cold layer holds
-- it. The hot layer is a dict inside the process; this is what survives the process.
--
-- **The only table in this schema with no aggregate behind it and no port in
-- ``app/domain/repositories``.** Every other table here is the durable form of
-- something the domain has an opinion about - a wallet, a session, a request
-- waiting to be proven. This one is the durable form of a *transport* fact, and
-- the domain has no opinion about it: nothing under ``app/domain`` or
-- ``app/application`` reads or writes this table, and no use case knows it exists.
-- It is written by ``InMemoryRateCounter`` through ``SqliteRateLimitRepository``.
--
-- The row is keyed on what is being counted rather than on a request. A *bucket*
-- names the policy being applied and a *subject* names who or what it is applied
-- to - an address, a folded number, a Google subject, or the installation itself
-- under ``INSTALLATION``, which is not a special mechanism but simply a subject
-- that every caller shares. See ``app/presentation/api/rate_limits.py``.
--
-- **The window is part of the row rather than of the key**, and that is what makes
-- a rollover an overwrite instead of an accumulating history: the only window worth
-- storing is the live one, because a lapsed window can limit nobody again. So a
-- key has at most one row here, whatever it has done in the past.
--
-- ``count`` is a *sum of deltas* rather than any one process's total. Two workers
-- each flushing a total would overwrite one another, and the stored number would be
-- one worker's calls rather than both workers'; each flushing the increments since
-- its own last flush makes the stored number the sum of every process's calls. On a
-- single-worker deployment the two readings agree - which is exactly why this is
-- worth writing down, since it is invisible until the second worker arrives and by
-- then the numbers have been wrong for a while. See ``InMemoryRateCounter.pending``.
--
-- Nothing here is a credential and nothing here is personal data in the sense
-- ``profiles`` is: a row says that some subject made some calls, and the subject is
-- an identifier the caller supplied about themselves. It is still not a table to
-- serve to anybody - it is an operator's window onto the limiters, not a feature.
--
-- There is no migration function for this table, for the reason recorded against
-- ``plan_notices``, ``outbound_messages``, ``users``, ``password_credentials``,
-- ``sessions``, ``notifications``, ``confirmations``, ``email_changes``,
-- ``password_resets``, ``phone_verifications`` and ``profiles`` above: CREATE TABLE
-- IF NOT EXISTS creates a missing table on a database already in the wild for free.
-- Adding a table is not a migration; changing a table that is already on disk is.
CREATE TABLE IF NOT EXISTS rate_limits (
    bucket            TEXT NOT NULL,
    subject           TEXT NOT NULL,
    -- When the window this count belongs to opened. Compared against the wall clock
    -- to decide liveness; a row past the longest policy window is swept rather than
    -- read. ISO moment, like every other stored one.
    window_started_at TEXT NOT NULL,
    -- Calls spent in that window, **including the refused ones**. A refusal counts
    -- because a limiter that handed its own count back would let a caller try
    -- forever at exactly the limit's rate - see ``InMemoryRateCounter``.
    count             INTEGER NOT NULL,
    -- One row per key, for the reason above: the live window is the only one stored.
    PRIMARY KEY (bucket, subject),
    -- Only increments are ever written, so a row of zero is a row that should not
    -- exist. ``mark_flushed`` advances by a delta and never rewinds, so nothing in
    -- this system can produce one; the constraint is here so that a hand-written
    -- row or a future bug is refused by the store rather than read as "no calls".
    CHECK (count > 0)
);
"""


def _migrate_legacy_transaction_types(connection: sqlite3.Connection) -> None:
    """Rewrite transaction types that were persisted under their old names.

    ``enum_to_text`` stores an enum member's *name*, so the type column holds
    "SCHEDULED_RELEASE" for rows written before that member was renamed to
    "UNLOCK_FUNDS". Reading such a row would now fail in ``text_to_enum`` with a
    bare KeyError.

    Renaming an enum member looks like a pure refactor but is really a data
    migration: the stored string is a contract, and breaking it breaks existing
    databases. Real projects run migrations through a tool (Alembic, Django
    migrations). This one is a standing ``UPDATE`` because it is idempotent -
    once no row matches, it is a no-op - which makes it safe to run on every
    connection.
    """
    connection.execute(
        """
        UPDATE transactions
        SET type = 'UNLOCK_FUNDS'
        WHERE type = 'SCHEDULED_RELEASE'
        """
    )


def _migrate_add_destination_column(connection: sqlite3.Connection) -> None:
    """Add transactions.destination to a database created before it existed.

    ``CREATE TABLE IF NOT EXISTS`` creates a *missing* table - it does nothing
    to a table that already exists, so a new column in SCHEMA reaches brand-new
    databases only. Every database already on disk needs an explicit ALTER.

    That is the general rule: **adding a field to a model is a schema migration,
    not an edit to the model.** The SCHEMA constant is the shape of a *new*
    database; migrations describe the path from an old one to it.

    Guarded by a check because SQLite has no ``ADD COLUMN IF NOT EXISTS`` - the
    guard is what makes this safe to re-run on every connection.
    """
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(transactions)")
    }
    if "destination" not in columns:
        connection.execute("ALTER TABLE transactions ADD COLUMN destination TEXT")


def _migrate_add_plan_name_column(connection: sqlite3.Connection) -> None:
    """Add savings_plans.name to a database created before plans had names.

    Two things here are worth more than the code.

    First, the guard: SQLite has no ``ADD COLUMN IF NOT EXISTS``, so a check
    against ``PRAGMA table_info`` is what makes this safe to run on every
    connection. (Contrast ``_migrate_legacy_transaction_types``, where the
    idempotence comes from the ``WHERE`` clause instead - once no row matches,
    the statement does nothing.)

    Second, the DEFAULT. SQLite refuses to add a ``NOT NULL`` column to a table
    that already has rows unless a default is supplied - it has to put
    *something* in the existing rows, and ``NULL`` would violate the constraint.
    So the default is not a design choice about naming; it is the value every
    pre-existing plan gets, and it is written into the migration rather than
    into the SCHEMA for exactly that reason. A brand-new database never sees it.
    """
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(savings_plans)")
    }
    if "name" not in columns:
        connection.execute(
            "ALTER TABLE savings_plans "
            "ADD COLUMN name TEXT NOT NULL DEFAULT 'Untitled plan'"
        )


def _migrate_plan_run_due_at_to_datetime(connection: sqlite3.Connection) -> None:
    """Rewrite plan_runs.due_at from a bare date to a full moment.

    ``plan_runs`` is keyed on ``(plan_id, due_at)``, and the text of a keyed
    column is part of the key. Rows written while ``due_at`` was a ``date`` hold
    "2026-01-01"; every row written now holds "2026-01-01T00:00:00". Two
    different strings do not collide, so the invariant that key exists to hold -
    one run per plan per occurrence - would quietly stop holding: a plan whose
    run was recorded BLOCKED at "2026-01-01", later topped up and resumed, would
    append a *second* row for the same occurrence instead of updating the first.

    Only blocked runs make this reachable, which is why it is easy to miss. A
    successful run advances the plan's counter, so its occurrence is never
    re-derived; a blocked run deliberately does not advance it, so the same
    occurrence comes round again. The row that is hardest to notice is the one
    that breaks.

    ``T00:00:00`` is not an arbitrary choice: ``datetime(2026, 1, 1).isoformat()``
    produces exactly this, and a legacy plan's anchor loads as midnight (see
    ``text_to_schedule``), so its occurrences *are* midnight. The rewrite makes
    what is stored agree with what is derived.

    Idempotent through the ``WHERE`` rather than through a ``PRAGMA`` guard -
    unlike the two migrations above, nothing about the *shape* of the table is
    changing, so the only question is whether a row's value has been widened yet.
    Once no row matches, this is a no-op.

    One more thing that makes this safe, and it is not obvious: the migration
    runs on **every** connection open, before that connection can write anything.
    So it is not possible for this version to add a "2026-01-01T00:00:00" row
    while a "2026-01-01" row for the same occurrence is still there - the old row
    is always widened first. Were that ordering reversed, the UPDATE would collide
    with itself on the primary key, which is a loud failure rather than a silent
    one, but a failure this ordering means never happens.

    **Why ``transactions.internal_reference`` is not migrated**, since it also
    embeds ``due_at`` and reads "plan:{id}:2026-01-01:0" for old runs. A
    reference is written only by a run that *succeeded*, and success advances the
    counter in the same transaction that writes the ledger row. So no reference
    can exist for an occurrence the plan is still due for - there is nothing for
    a rewritten key to collide with, and nothing to rewrite. A blocked run writes
    no transaction at all, which is the entire point of the pre-flight check.
    """
    connection.execute(
        """
        UPDATE plan_runs
        SET due_at = due_at || 'T00:00:00'
        WHERE due_at NOT LIKE '%T%'
        """
    )


def _migrate_add_fund_id_column(connection: sqlite3.Connection) -> None:
    """Add transactions.fund_id to a database created before pots existed.

    Which pot a movement went through, when one did. Nullable, and not only
    because ALTER cannot add NOT NULL without a default: a payout in this phase
    legitimately names no pot, and a deposit to the available balance never had
    one. NULL here means "no pot was involved", which is a real answer rather
    than missing data.

    Structurally this is ``_migrate_add_destination_column`` again, and the
    reasoning there applies unchanged - ``CREATE TABLE IF NOT EXISTS`` gives a
    new column to new databases only, so old ones need the explicit ALTER, and
    the PRAGMA guard is what makes re-running on every connection safe.
    """
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(transactions)")
    }
    if "fund_id" not in columns:
        connection.execute("ALTER TABLE transactions ADD COLUMN fund_id TEXT")


def _migrate_add_plan_fund_id_column(connection: sqlite3.Connection) -> None:
    """Add savings_plans.fund_id to a database written before pots could be named.

    Nullable with **no backfill**, and the absence of a backfill is the whole
    decision. A plan saved earlier has no pot, and there is no honest value to
    invent: the wallet's locked money may be spread across several pots, and
    picking one would silently commit money the user never committed. ``NULL``
    instead means exactly what is true - "this plan predates naming, draw on the
    pool as it always did" - and ``Wallet.payout_from_locked`` still implements
    that draw. See the column comment in SCHEMA.

    Contrast ``_migrate_add_plan_name_column``, which *had* to choose a default
    because the column it added was not nullable. A default is only a design
    decision when the constraint forces one; here nothing forces it, so the
    truthful value wins over a convenient one.

    Structurally this is ``_migrate_add_destination_column`` again: the PRAGMA
    guard is what makes re-running on every connection safe.
    """
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(savings_plans)")
    }
    if "fund_id" not in columns:
        connection.execute("ALTER TABLE savings_plans ADD COLUMN fund_id TEXT")


def _migrate_add_plan_user_column(connection: sqlite3.Connection) -> None:
    """Add savings_plans.user_id to a database written before users existed.

    The column is ``NOT NULL``, and that forces the two decisions worth setting
    out here.

    **The default.** ALTER TABLE cannot add a NOT NULL column without one, so
    ``''`` it is - and it is the only value that keeps the backfill below total.
    This is the corner ``_migrate_add_plan_name_column`` was pushed into before,
    with the same consequence: a *migrated* database ends up with a default where
    a fresh one has none. On such a database an INSERT that forgot ``user_id``
    would write an empty string and produce a plan that no actor can ever be
    scoped to find - a row present in the table and invisible to every query the
    application is able to make. That is unreachable through the application,
    because ``SavingsPlan`` refuses a plan with no owner and the repository
    always writes one; the test that keeps it unreachable is named in the README
    decision rather than left to trust.

    **The backfill is derived, not invented.** Every plan already has an owner -
    it is on the wallet the plan draws on - so copying it across reads a fact
    that is already recorded rather than guessing one. The subquery cannot come
    back empty: ``savings_plans.wallet_id`` references ``wallets(wallet_id)``
    and ``PRAGMA foreign_keys = ON`` is set before this runs, so no plan can
    name a wallet that does not exist. **That totality is what makes NOT NULL
    safe here.** On a database where the reference could dangle, this UPDATE
    would have to tolerate NULLs and the column would have to accept them - and
    the honest design would be a nullable column plus a scheduler that refused to
    run an ownerless plan, rather than a constraint that quietly failed.

    Contrast ``_migrate_add_plan_fund_id_column``, which deliberately backfills
    nothing: there was no recorded fact to copy, only a choice somebody would
    have had to invent. Here there is one, and refusing to read it would leave
    every existing plan invisible to its own owner.
    """
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(savings_plans)")
    }
    if "user_id" in columns:
        return

    connection.execute(
        "ALTER TABLE savings_plans ADD COLUMN user_id TEXT NOT NULL DEFAULT ''"
    )
    connection.execute(
        """
        UPDATE savings_plans
           SET user_id = (
               SELECT wallets.user_id
                 FROM wallets
                WHERE wallets.wallet_id = savings_plans.wallet_id
           )
        """
    )


def _migrate_add_fund_commitment_columns(connection: sqlite3.Connection) -> None:
    """Add funds.sealed_at and funds.first_funded_at, and backfill both.

    The two moments ``Fund.authorises_early_payout`` is judged on, added to a
    database whose pots predate the rule. Backfilling them from ``created_at`` is
    the **conservative** direction, and it is worth being explicit about why,
    because the other direction is the tempting one.

    ``first_funded_at`` is the moment a commitment must predate to authorise an
    early payout. Backfilling it from ``created_at`` makes the recorded funding
    moment *earlier* than the money can have arrived - and an earlier anchor
    makes the exemption harder to obtain, because fewer commitments will be found
    to predate it. So no existing pot gains an exemption it did not already have.
    The alternative - leaving it NULL - would say "this pot has never been
    funded", which is false and would be a different bug.

    ``sealed_at`` is always set, so its backfill is unconditional: every existing
    pot's date is as old as the pot.

    **The empty pot is the one exception**, and it is the reason this cannot be a
    single unconditional UPDATE: a pot with nothing in it has never been funded,
    and ``NULL`` is the true answer for it. The test is a cast rather than a
    string comparison on purpose - ``balance`` is text, and "0", "0.00" and
    "0.000" are three different strings that all mean nothing. This is the one
    place in the codebase where SQL arithmetic on a money column is defensible,
    because the question is "is this zero?" and the answer is thrown away.

    ``NOT NULL DEFAULT ''`` for ``sealed_at`` follows ``_migrate_add_plan_name_column``:
    SQLite refuses to add a NOT NULL column to a table that already has rows
    without a default, so the default is the value the existing rows briefly
    hold before the UPDATE below replaces it. A brand-new database never sees it,
    because SCHEMA already carries the column.
    """
    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(funds)")
    }
    if "sealed_at" not in columns:
        connection.execute(
            "ALTER TABLE funds ADD COLUMN sealed_at TEXT NOT NULL DEFAULT ''"
        )
    if "first_funded_at" not in columns:
        connection.execute("ALTER TABLE funds ADD COLUMN first_funded_at TEXT")

    connection.execute(
        """
        UPDATE funds
        SET sealed_at = created_at
        WHERE sealed_at = ''
        """
    )
    connection.execute(
        """
        UPDATE funds
        SET first_funded_at = created_at
        WHERE first_funded_at IS NULL
          AND CAST(balance AS NUMERIC) <> 0
        """
    )


#: The first SQLite release with ``ALTER TABLE ... DROP COLUMN`` (2021-03-12).
#: Checked rather than assumed: the linked SQLite is a property of the machine
#: and the Python build, not of this code, so it is not something to be right
#: about on the developer's laptop and wrong on the user's.
_DROP_COLUMN_MINIMUM = (3, 35, 0)


#: The currencies a wallet may be opened in, in the spelling the store writes.
#:
#: Derived from ``Currency`` rather than written out, and the direction matters:
#: this is the set of rows the *current* build can read, so the enum is what
#: defines it. A member removed from ``Currency`` tomorrow takes its rows out of
#: this set, which is what lets the migration below keep working after the next
#: trim without being edited.
_SERVABLE_WALLET_CURRENCIES = frozenset(enum_to_text(member) for member in Currency)

#: What a closed wallet's status looks like in the store - ``enum_to_text`` writes
#: ``member.name``. Read from the enum rather than typed out, so the index's
#: predicate and the aggregate's spelling cannot drift apart.
_CLOSED_WALLET_STATUS = enum_to_text(WalletStatus.CLOSED)


class UnservableWalletRowsError(RuntimeError):
    """A wallet row this build cannot serve, holding something that cannot be thrown away.

    **Not a domain exception, and the class it is not is the point.** Everything
    under ``MoneyError`` is a sentence a caller reads about a request they made;
    this one is a fact about the *database*, no request produces it, and no request
    can fix it. It is raised while the connection is being opened, so the failure
    arrives at startup rather than as a 500 on somebody's landing page an hour
    later.

    **It is raised only when the migration cannot do the right thing by itself.** A
    wallet row in a currency this build no longer has, or a second wallet in a
    currency its owner already holds one in, is either provably empty - in which
    case the migration below deletes it and says so - or it holds money or history,
    and then there is nothing this code can do to it that is not destroying
    something. Re-denominating money is not a thing this system does, and merging
    two wallets' ledgers is a money operation rather than a cleanup. So it stops,
    names the rows, and leaves the decision to whoever can look inside them.
    """


def _wallet_holds_anything(
    connection: sqlite3.Connection, wallet_id: str, available_balance: str
) -> str | None:
    """What this wallet still has, as a phrase, or ``None`` if it is provably empty.

    **"Provably empty" is defined positively rather than as a default.** A row may
    be deleted by the migration below only when every one of these is checked and
    found absent, because the alternative - a rule like "nothing that looks
    important" - deletes whatever nobody thought of.

    The balance is compared as a ``Decimal`` and not with SQL's ``CAST``: ``CAST
    ... AS NUMERIC`` is a float conversion, and a rule about whether money is
    present is the last place in this codebase to introduce one. The four tables
    named below are the ones carrying a ``wallet_id``; between them they are every
    fact in the schema that removing the wallet would orphan.
    """
    if Decimal(available_balance) != 0:
        return f"a balance of {available_balance}"
    for table, noun in (
        ("funds", "a pot"),
        ("transactions", "a ledger row"),
        ("savings_plans", "a savings plan"),
        ("confirmations", "a confirmation request"),
    ):
        found = connection.execute(
            f"SELECT 1 FROM {table} WHERE wallet_id = ? LIMIT 1", (wallet_id,)
        ).fetchone()
        if found is not None:
            return noun
    return None


def _migrate_resolve_wallet_rows(connection: sqlite3.Connection) -> None:
    """Remove wallet rows this build cannot serve, and stop if one holds something.

    Two conditions, one idea: **both are states the code that wrote them could
    reach and this build cannot.** Neither is hypothetical for an installation that
    has been running.

    * **A currency no longer on ``Currency``.** No row like this can be read at
      all - ``text_to_enum`` raises ``KeyError`` - so it is not a wallet with an
      odd currency, it is a row that breaks whatever touches it. That is why this
      migration runs *first*: every later step in ``_connect`` reads
      ``wallets.currency``, ``_migrate_locked_balance_into_funds`` included.
    * **A second wallet in a currency the same person already holds one in.** The
      rule the application now enforces at the door, applied to data written before
      there was a door. It is here rather than left to the index because
      ``CREATE UNIQUE INDEX`` over a table that violates it does not tidy anything:
      it fails, and the database is exactly as unopenable as it was.

    **Which member of a pair survives is decided by what is in it, not by its age.**
    The oldest is kept by default - it is the row the landing page lists first and
    the one an existing link points at - but if exactly one member of the group
    holds anything, that one is kept instead, because an empty wallet and a funded
    wallet are not a real choice between. Only when the group cannot be resolved
    that way does this raise.

    **It deletes rows, so it prints what it deleted.** One line per row, naming the
    wallet and what was empty about it. Nothing is printed on a database that has
    none of these, which is every fresh one and every test. A print rather than a
    logger because this module has no logger and the statement is one line - and a
    print rather than silence because a person who finds a wallet missing is owed
    the story in their own logs.

    **Nothing is written until the whole answer is known.** The plan is worked out
    and every deletion verified empty before the first ``DELETE``, so a database
    this raises on is a database this left alone.
    """
    rows = connection.execute(
        """
        SELECT wallet_id, user_id, currency, status, available_balance
        FROM wallets
        ORDER BY rowid
        """
    ).fetchall()
    if not rows:
        return

    # Grouped in Python rather than in SQL: there are a handful of wallets in the
    # whole database, and the rule below is a read of each row's contents, which
    # is not a predicate SQL should be asked to express.
    groups: dict[tuple[str, str], list[sqlite3.Row]] = {}
    doomed: list[tuple[sqlite3.Row, str]] = []
    blocked: list[tuple[sqlite3.Row, str]] = []

    for row in rows:
        if row["currency"] not in _SERVABLE_WALLET_CURRENCIES:
            doomed.append((row, "its currency is no longer served by this build"))
        elif row["status"] != _CLOSED_WALLET_STATUS:
            # A closed wallet holds no currency slot, so it is skipped rather than
            # grouped: closing one frees its currency, which is the rule that makes
            # a closed wallet a legitimate companion to a live one.
            groups.setdefault((row["user_id"], row["currency"]), []).append(row)

    for (_owner, currency), members in groups.items():
        if len(members) == 1:
            continue
        holders = [
            (row, _wallet_holds_anything(connection, row["wallet_id"], row["available_balance"]))
            for row in members
        ]
        funded = [(row, held) for row, held in holders if held is not None]
        if len(funded) > 1:
            # Two wallets that both hold something, in one currency. There is no
            # reading of this that says which one the person meant, and there is no
            # merge here - see the class above.
            blocked.extend(
                (row, f"it is one of {len(members)} {currency} wallets, and holds {held}")
                for row, held in funded
            )
            continue
        survivor = funded[0][0] if funded else members[0]
        why = (
            f"a second {currency} wallet on one account"
            if funded
            else f"a second empty {currency} wallet on one account"
        )
        doomed.extend(
            (row, why) for row in members if row["wallet_id"] != survivor["wallet_id"]
        )

    for row, why in doomed:
        held = _wallet_holds_anything(
            connection, row["wallet_id"], row["available_balance"]
        )
        # Unreachable for the duplicates above, which are empty by construction -
        # and checked all the same, because this is the difference between a
        # promise and an argument: nothing below deletes a row that holds anything.
        if held is not None:
            blocked.append((row, f"{why}, and holds {held}"))

    if blocked:
        raise UnservableWalletRowsError(
            "this database holds wallet rows that cannot be served and cannot be "
            "deleted, so it has been left exactly as it was: "
            + "; ".join(
                f"{row['wallet_id']} ({row['currency']}): {why}" for row, why in blocked
            )
            + ". Neither removing money nor merging two ledgers is something this "
            "system does on its own - settle or move what is in these wallets, or "
            "remove the rows by hand, and start again."
        )

    for row, why in doomed:
        connection.execute(
            "DELETE FROM wallets WHERE wallet_id = ?", (row["wallet_id"],)
        )
        print(
            f"budget: removed wallet {row['wallet_id']} ({row['currency']}) - "
            f"{why}; it held no money and no history"
        )


def _migrate_one_wallet_per_currency(connection: sqlite3.Connection) -> None:
    """Enforce one wallet per currency in the store, for as long as a wallet is open.

    **A partial unique index, because a table constraint cannot be partial.** SQLite
    has no ``UNIQUE (user_id, currency) WHERE ...`` in a ``CREATE TABLE``, and the
    ``WHERE`` here is not decoration: it is the difference between "one wallet per
    currency" and "one wallet per currency forever". Closing a wallet is meant to
    free its currency - see ``DuplicateWalletCurrencyError`` - so the index ignores
    exactly the rows that are closed, and a person who closes a wallet and opens
    another in the same currency is not in violation of anything.

    **The predicate is the status as the store spells it**, which ``enum_to_text``
    writes as the enum *member name*: ``'CLOSED'``, not the value and not a number.
    It is read from ``WalletStatus`` rather than typed here so the two cannot
    disagree.

    **Where it belongs in the sequence, and why both halves are load-bearing.** It
    runs after ``_migrate_resolve_wallet_rows``, because ``CREATE UNIQUE INDEX`` over
    a table that already violates it fails rather than tidying - and the databases
    that violate it are precisely the ones this change exists for. It runs before
    everything else that reads ``wallets``, so the guarantee is in place for the
    rest of the open rather than arriving halfway through it.

    **This is the backstop, not the rule.** The rule is in ``WalletService.
    open_wallet``, which refuses with a domain error before the write. This exists
    for the gap between that check and this write - two requests can both read an
    empty list - and it is the same arrangement as ``funds``' ``UNIQUE (wallet_id,
    name)``: the aggregate refuses first, and the store refuses anything that got
    past it. A caller that trips this sees a ``sqlite3.IntegrityError`` rather than
    a sentence, which is the honest outcome for a race that should not have
    happened.
    """
    connection.execute(
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS one_wallet_per_currency
        ON wallets(user_id, currency)
        WHERE status <> '{_CLOSED_WALLET_STATUS}'
        """
    )


def _migrate_locked_balance_into_funds(connection: sqlite3.Connection) -> None:
    """Move each wallet's locked balance into a pot, then drop the column.

    Before pots, a wallet's locked money was one number in
    ``wallets.locked_balance``. Now it is the sum of that wallet's funds, and the
    column has nowhere to fit - so this is the one migration in the file that
    *removes* something rather than adding it.

    **The migration is invisible on purpose.** Every pot it creates is named
    "Locked", is ``PERSONAL``, and has ``maturity_date = NULL``, which is defined
    to mean "no maturity, always open". So money that was releasable
    unconditionally before this ran is releasable unconditionally after it. A
    user's database does not gain a lock they never asked for; it gains a name
    for money that was already set aside.

    **Why the rows are moved in Python and not by an ``INSERT ... SELECT``.** The
    fund needs a ``fund_id``, and SQLite has no ``uuid()`` function to generate
    one in the SELECT. Reading the rows and inserting them one at a time is what
    lets a real UUID be minted for each.

    **Idempotence comes from the WHERE clause**, not a PRAGMA guard on the shape
    of the table - the same choice ``_migrate_plan_run_due_at_to_datetime`` made,
    and for a sharper reason here. Migrations run in autocommit, before the unit
    of work's transaction begins, so a process killed between the inserts and the
    DROP would leave the column still present with the pots already written. On
    the next start, skipping wallets that already have funds means the inserts do
    nothing rather than colliding with themselves on ``UNIQUE (wallet_id, name)``.
    A failure there would be loud rather than silent, but an app that will not
    start is its own kind of wrong.

    Money decisions are made in Python rather than SQL. ``locked_balance`` is
    stored as text, so "is this balance non-zero?" in SQL would mean either
    comparing strings that may be written "0", "0.00" or "0.000" and are not
    equal, or casting to REAL - and this is the one codebase where reaching for a
    float to answer a question about money should look wrong. "Does this wallet
    already have funds?" is a question about existence, not about money, and SQL
    answers it exactly.
    """
    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(wallets)")
    }
    if "locked_balance" not in columns:
        # Already migrated, or a database created fresh from SCHEMA above, which
        # no longer has the column at all. Either way there is nothing to move.
        return

    if sqlite3.sqlite_version_info < _DROP_COLUMN_MINIMUM:
        raise RuntimeError(
            "this database still has wallets.locked_balance and needs "
            f"ALTER TABLE ... DROP COLUMN, which requires SQLite "
            f"{'.'.join(str(part) for part in _DROP_COLUMN_MINIMUM)} or newer; "
            f"this Python is linked against {sqlite3.sqlite_version}. "
            "Either upgrade SQLite, or move each wallet's locked balance into a "
            "fund by hand and drop the column."
        )

    rows = connection.execute(
        """
        SELECT wallet_id, currency, locked_balance
        FROM wallets
        WHERE NOT EXISTS (
            SELECT 1 FROM funds WHERE funds.wallet_id = wallets.wallet_id
        )
        """
    ).fetchall()

    # One reading of the clock for the whole migration, not one per pot: every
    # pot it creates is created at the same moment by the same run, and letting
    # the clock tick between them would invent an ordering that means nothing.
    moment = datetime.now()

    for row in rows:
        locked = text_to_money(
            row["locked_balance"], text_to_enum(Currency, row["currency"])
        )
        if locked.amount <= 0:
            # An empty pot is not a thing to open. A wallet that never locked
            # anything ends up with no funds, which is what it had before.
            continue
        connection.execute(
            """
            INSERT INTO funds
                (fund_id, wallet_id, name, kind, balance, maturity_date,
                 sealed_at, first_funded_at, created_at)
            VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?)
            """,
            (
                uuid_to_text(uuid.uuid4()),
                row["wallet_id"],
                "Locked",
                enum_to_text(FundKind.PERSONAL),
                money_to_text(locked),
                # One moment for all three timestamps on purpose. The pot is
                # born already holding this money, so it was created, sealed and
                # funded at the same instant - writing ``datetime.now()`` three
                # times would record a pot that was funded a few microseconds
                # after it was sealed, which is a distinction this row does not
                # contain. The balance check above guarantees it is non-empty, so
                # ``first_funded_at`` is a moment and not ``None``: an empty pot
                # never reaches this INSERT.
                datetime_to_text(moment),
                datetime_to_text(moment),
                datetime_to_text(moment),
            ),
        )

    connection.execute("ALTER TABLE wallets DROP COLUMN locked_balance")


def _migrate_make_user_email_optional(connection: sqlite3.Connection) -> None:
    """Rebuild ``users`` so ``email`` may be NULL, with ``phone`` beside it.

    **The first table rebuild in this codebase**, and it is here because SQLite
    offers no alternative: dropping ``NOT NULL`` has no ``ALTER TABLE`` form, so
    the only way to make an existing column optional is to build the table again
    and move the rows across. Every other migration in this file changes a column
    or rewrites values; this one *replaces* the table, and a reader who has read
    the others should know that before reading this one.

    Three things make it safe rather than merely necessary, and all three are
    consequences of decisions taken elsewhere:

    * **Nothing references ``users``.** Every owner column in this schema
      deliberately omits a foreign key to it - ``savings_plans``, ``confirmations``,
      ``email_changes``, ``password_resets`` and ``profiles`` each carry a comment
      saying so - so the ``DROP`` below cannot strand a child row and no
      foreign-key dance is owed. That the absence was forced by a SQLite
      limitation, recorded above ``users``, turns out to have bought this.
      (Note what it does *not* buy: ``wallets.user_id``'s missing foreign key is a
      different candidate and is still open. This rebuild is of ``users``, and it
      does not touch ``wallets``.)
    * **The guard is ``email``'s ``notnull`` flag**, not "does ``phone`` exist".
      A database that reached the new shape by some other route is still left
      alone, and a second start does nothing rather than rebuilding again.
    * **It runs as one transaction**, where every other migration here is a single
      statement that is safe in autocommit. This is four, and a process killed
      between the ``DROP`` and the ``RENAME`` would lose the table outright.
      ``BEGIN IMMEDIATE`` takes the write lock at the start, so a second process
      opening the same database waits rather than reading a half-built table.

    The copy names its columns on both sides instead of using ``SELECT *``. The
    two tables hold the same values but not in the same order, and a positional
    copy is the kind of thing that keeps working until somebody inserts a column
    in the middle.

    ``phone`` is written as ``NULL`` for every existing row, which is the honest
    value rather than a placeholder: an account predating phone signup has no
    number, and inventing one would be inventing an identifier somebody could
    later authenticate against. The column is read from the old table when it is
    somehow already there, so a database that gained ``phone`` by hand keeps it.
    """
    columns = {
        row["name"]: row["notnull"] for row in connection.execute("PRAGMA table_info(users)")
    }

    # A fresh database has the new shape from SCHEMA above and needs nothing.
    # ``email`` present but already nullable means the rebuild has run.
    if "email" in columns and not columns["email"]:
        return

    # One of two literals, chosen rather than interpolated from anything external:
    # a database that already carries ``phone`` keeps its values, and one that does
    # not gets NULL. Writing ``NULL`` unconditionally would silently discard them.
    phone_source = "phone" if "phone" in columns else "NULL"

    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            """
            CREATE TABLE users_rebuilt (
                user_id        TEXT PRIMARY KEY,
                email          TEXT UNIQUE,
                phone          TEXT UNIQUE,
                google_subject TEXT UNIQUE,
                created_at     TEXT NOT NULL
            )
            """
        )
        connection.execute(
            f"""
            INSERT INTO users_rebuilt
                (user_id, email, phone, google_subject, created_at)
            SELECT user_id, email, {phone_source}, google_subject, created_at
            FROM users
            """
        )
        connection.execute("DROP TABLE users")
        connection.execute("ALTER TABLE users_rebuilt RENAME TO users")
        connection.execute("COMMIT")
    except BaseException:
        connection.execute("ROLLBACK")
        raise


def open_sqlite_connection(db_path: str) -> sqlite3.Connection:
    """Open a connection in autocommit mode with the schema applied.

    isolation_level=None switches off sqlite3's implicit transaction handling,
    so transactions are started and ended explicitly (see
    SqliteUnitOfWorkFactory.start).
    """
    connection = sqlite3.connect(db_path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA)
    # Every call below is here because it changes something about a table that
    # may *already exist*. Nothing does the same for ``users``, ``plan_notices``,
    # ``outbound_messages``, ``password_credentials`` or ``sessions``, and that
    # absence is deliberate: ``CREATE TABLE IF NOT EXISTS`` above creates a missing
    # table on an existing database quite happily, so a brand-new table arrives for
    # free. Adding a table is not a migration; changing a table that is already on
    # disk is.
    #
    # ``users`` is the table that makes the distinction concrete rather than
    # abstract: the table itself is free, but the *plan owner* column below is
    # not, because ``savings_plans`` already exists on every database in the wild
    # and needs a value filled in for rows that predate it.
    #
    # The two identity tables are the newest example of the free kind, and they
    # are the reason Phase 2a touched no migration code at all: the password
    # credential went into a table of its own rather than becoming three columns
    # on ``users``, and a new table costs nothing here. That was a security
    # decision first - see ``user.py`` - and this is the second thing it bought.
    # First, and before anything else reads ``users``: this is the one migration
    # here that replaces a table rather than changing one, and every later step in
    # this function would otherwise be reading a shape that is about to change.
    # It is a no-op on a fresh database, where ``executescript`` has just created
    # the table in its new form.
    _migrate_make_user_email_optional(connection)
    # The first two steps that touch ``wallets``, and they are first on purpose.
    # Every step below this line reads ``wallets.currency`` through the enum, so
    # rows still carrying a currency this build no longer has must be gone before
    # any of them run - ``_migrate_locked_balance_into_funds`` would raise
    # ``KeyError`` on one. The index follows immediately, because it is the
    # guarantee the rest of the sequence can then assume, and because it cannot be
    # created over the duplicates the step above has just resolved. Both are
    # no-ops on a fresh database, which has no such rows and gains the index from
    # the same call. See each function for what it does and why it may delete.
    _migrate_resolve_wallet_rows(connection)
    _migrate_one_wallet_per_currency(connection)
    _migrate_add_destination_column(connection)
    _migrate_add_plan_name_column(connection)
    _migrate_plan_run_due_at_to_datetime(connection)
    _migrate_legacy_transaction_types(connection)
    # After the two above and after executescript, which is what has just created
    # the ``funds`` table this migration writes into - it is new, so per the note
    # inside it there is no migration for its creation, only for filling it from
    # the column it replaces.
    _migrate_add_fund_id_column(connection)
    _migrate_add_plan_fund_id_column(connection)
    # After executescript, which is what guarantees the ``wallets`` table this
    # reads from exists and is populated: it copies each plan's owner across
    # from the wallet that plan draws on. See the function for why that copy is
    # the reading of a recorded fact rather than a guess, and why the constraint
    # it rests on is what lets the column be NOT NULL.
    _migrate_add_plan_user_column(connection)
    # Before the migration below, and that ordering is load-bearing: the pot it
    # creates carries a NOT NULL ``sealed_at``, so the column has to exist on an
    # older database before the INSERT names it. Both of these are no-ops on a
    # fresh database, where SCHEMA already has the columns - which is why the
    # ``funds`` table gets its columns two ways and neither is redundant.
    _migrate_add_fund_commitment_columns(connection)
    _migrate_locked_balance_into_funds(connection)
    return connection


class SqliteUnitOfWork(UnitOfWork):
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection
        self.wallets = SqliteWalletRepository(connection)
        self.transactions = SqliteTransactionRepository(connection)
        self.plans = SqliteSavingsPlanRepository(connection)
        self.plan_runs = SqlitePlanRunRepository(connection)
        self.notices = SqlitePlanNoticeRepository(connection)
        self.notifications = SqliteNotificationRepository(connection)
        self.outbound_messages = SqliteOutboundMessageRepository(connection)
        self.users = SqliteUserRepository(connection)
        self.password_credentials = SqlitePasswordCredentialRepository(connection)
        self.sessions = SqliteSessionRepository(connection)
        # Correctness, not convenience: the claim that spends a confirmation and
        # the ledger row it authorises must land in one transaction. See the
        # attribute's declaration on ``UnitOfWork``.
        self.confirmations = SqliteConfirmationRepository(connection)
        # Correctness, not convenience, and written by a *different* repository
        # from the account it moves: the spend of the mailed token and the address
        # change it authorises must land in one transaction, or a crash between
        # them leaves a token that still works after it has been used. See the
        # attribute's declaration on ``UnitOfWork``.
        self.email_changes = SqliteEmailChangeRepository(connection)
        # Correctness, not convenience, and the largest pairing of the four: the
        # spend of the mailed token, the replacement of the account's password and
        # the deletion of every session the account holds must land in one
        # transaction. A crash between any two of them leaves something worse than
        # either alone - sessions alive under a password that no longer works, or a
        # spent code that changed nothing. It is written by *three* repositories -
        # ``password_resets`` here, ``password_credentials`` and ``sessions`` above
        # - which is why the declaration exists at all rather than being left to
        # the use case to remember. See the attribute's declaration on
        # ``UnitOfWork``.
        self.password_resets = SqlitePasswordResetRepository(connection)
        # Correctness, not convenience, and the pairing is this slice's own: the spend
        # of the texted code and the creation of the account it authorises must land in
        # one transaction, or a crash between them claims a number that no account then
        # holds - and since ``UNIQUE(phone)`` is what stops a second person taking it,
        # the true owner is locked out of their own number until the row expires. The
        # two halves are written by *three* repositories - ``phone_verifications`` here,
        # ``users`` and ``password_credentials`` above - which is why the declaration
        # exists at all rather than being left to the use case to remember. See the
        # attribute's declaration on ``UnitOfWork``.
        self.phone_verifications = SqlitePhoneVerificationRepository(connection)
        self.profiles = SqliteProfileRepository(connection)
        # **The one repository here that no use case reads**, and it is declared on
        # this object anyway - which is the decision worth a sentence rather than the
        # exception worth hiding. What this class provides is not "the repositories
        # the domain needs" but "one connection, one transaction, and everything that
        # writes through them", and the rate limiter's flusher needs exactly that: a
        # write and a sweep that must land together, or neither. Giving it its own
        # connection instead would be a second way to reach the database, which is the
        # thing ``app/presentation/api/__init__.py`` refuses to have two of. So the
        # attribute lives here and nothing under ``app/domain`` or ``app/application``
        # mentions it; see ``SqliteRateLimitRepository`` for the full argument.
        self.rate_limits = SqliteRateLimitRepository(connection)

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()


class SqliteUnitOfWorkFactory:
    """Creates a fresh Unit of Work (own connection + transaction) per call.

    Every start() opens a new connection to the same database file, so
    committed work is visible to later units but no two units ever share a
    connection or a transaction.

    **And no two units ever hold a write intent at once** - see ``start``, which
    is where that is decided and where the price of it is written down.
    """

    def __init__(self, db_path: str = "budget.db"):
        self.db_path = db_path

    def start(self) -> SqliteUnitOfWork:
        """Open a unit: a fresh connection, a fresh transaction, and the write lock.

        **``BEGIN IMMEDIATE`` rather than the plain ``BEGIN`` this used to run, and
        the difference is a rule that cannot be enforced without it.** A plain
        ``BEGIN`` is *deferred*: it takes no lock until the first statement, a read
        takes only a shared one, and the transaction upgrades to a write lock when
        it first writes. So two units that each read before either writes can both
        read the same value and both act on it:

            1. confirm A reads today's outflow: 90,000 of a 100,000 cap
            2. confirm B reads today's outflow: 90,000 of a 100,000 cap
            3. A moves 8,000 and commits
            4. B moves 8,000 and commits

        The cap is now breached by 6,000 and neither operation did anything wrong
        by its own reading. SQLite's single-writer rule does not save it: both read
        *before* either wrote, so the second write does not fail - it waits for the
        first to commit, and then succeeds. ``BEGIN IMMEDIATE`` takes the write
        lock at the start instead, which makes the read in step 1 and the write in
        step 3 one critical section: B cannot read until A has committed, so B
        reads 98,000 and refuses. That is what makes a *read-then-write* rule
        enforceable between two callers at all, and the tier ceilings are the first
        such rule in this system.

        **This is every unit in the system, not only the money ones**, and that is
        the honest description rather than a claim about scope. ``start`` is the
        only door and there is no way to say "this one is a movement" from here; a
        parameter meaning that would be a parameter a future money path could
        forget to pass, and the failure mode of forgetting is a silent over-limit
        rather than a loud refusal. The cost, stated plainly: a unit holds the
        write lock for its whole life including the read-only ones, so a slow read
        delays every writer behind it. It does *not* stop readers running together
        - a reserved lock coexists with shared ones, and these connections are in
        the default rollback-journal mode (nothing sets ``journal_mode``) - so what
        is removed is two units *disagreeing* about a value one of them is about to
        write.

        What it does not fix, said here so nobody has to discover it: the lock is
        held until commit, and the wait before SQLite gives up is the connection's
        default busy timeout. Two units that contend longer than that raise
        ``OperationalError`` rather than writing anything inconsistent - a loud
        failure, which is the direction a financial control may fail in.
        """
        connection = open_sqlite_connection(self.db_path)
        connection.execute("BEGIN IMMEDIATE")
        return SqliteUnitOfWork(connection)
