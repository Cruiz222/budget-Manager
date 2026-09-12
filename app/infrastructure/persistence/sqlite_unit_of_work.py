"""SQLite-backed Unit of Work.

One SqliteUnitOfWork is one connection with an open transaction. The service
opens one per business operation: the reads and writes made through its
repositories all land in that single transaction and become durable together on
commit() - or are all discarded by rollback().
"""

import sqlite3
import uuid
from datetime import datetime

from app.application.unit_of_work import UnitOfWork
from app.domain.money.currency import Currency
from app.domain.money.fundKind import FundKind
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
from app.infrastructure.repositories.sqlite_notification_repository import (
    SqliteNotificationRepository,
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
    email          TEXT NOT NULL UNIQUE,   -- folded to lowercase by the aggregate
    -- The Google account this user signs in with, when they have one. NULL is
    -- the ordinary case for an account created any other way, and SQLite's
    -- UNIQUE permits any number of NULLs - which is exactly right here, and is
    -- why an empty string would be wrong instead: every Google-less account
    -- would collide on "" and the second signup would fail against the first.
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

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()


class SqliteUnitOfWorkFactory:
    """Creates a fresh Unit of Work (own connection + transaction) per call.

    Every start() opens a new connection to the same database file, so
    committed work is visible to later units but no two units ever share a
    connection or a transaction.
    """

    def __init__(self, db_path: str = "budget.db"):
        self.db_path = db_path

    def start(self) -> SqliteUnitOfWork:
        connection = open_sqlite_connection(self.db_path)
        connection.execute("BEGIN")
        return SqliteUnitOfWork(connection)
