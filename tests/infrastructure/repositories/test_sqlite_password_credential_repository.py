import sqlite3
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.identity.password_credential import PasswordCredential
from app.infrastructure.persistence.sqlite_unit_of_work import open_sqlite_connection
from app.infrastructure.repositories.sqlite_password_credential_repository import (
    SqlitePasswordCredentialRepository,
)

MOMENT = datetime(2026, 3, 2, 12, 0)
ENCODED = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA"


def build_repository():
    """A credential store over its own in-memory database.

    ``open_sqlite_connection`` rather than a raw ``sqlite3.connect``, so these
    tests run the real schema - which matters more here than usual, because this
    table's primary key is a *foreign key into* ``users`` and a hand-built table
    would not have it.
    """
    return SqlitePasswordCredentialRepository(open_sqlite_connection(":memory:"))


def build(user_id=None, password_hash=ENCODED, updated_at=MOMENT):
    return PasswordCredential(
        user_id=user_id if user_id is not None else uuid4(),
        password_hash=password_hash,
        updated_at=updated_at,
    )


def test_save_and_find_round_trips_the_credential():
    user_id = uuid4()
    repository = build_repository()

    repository.save(build(user_id=user_id))

    stored = repository.find_by_user_id(user_id)
    assert stored.user_id == user_id
    assert stored.password_hash == ENCODED
    assert stored.updated_at == MOMENT


def test_a_user_with_no_credential_is_none():
    """``None`` rather than an exception, for the reason ``find_by_email`` gives.

    "Does this account have a password?" is a *question*, and the answer "no" is
    ordinary: an account created through a Google identity has no password
    credential (Phase 2c), and ``LogIn`` has to be able to ask. An exception here
    would make the caller catch an error to express a branch.
    """
    repository = build_repository()
    repository.save(build(user_id=uuid4()))

    assert repository.find_by_user_id(uuid4()) is None


def test_saving_again_replaces_the_password():
    """A password change is one row rewritten, not a row appended.

    Which is what the primary key on ``user_id`` buys, and why it is the key rather
    than a surrogate id: an account has one password at a time, so "the credential
    for this user" is a single-valued fact and the schema should say so. A
    surrogate key would allow two rows and leave every reader to pick one.
    """
    user_id = uuid4()
    repository = build_repository()
    repository.save(build(user_id=user_id))

    repository.save(
        build(user_id=user_id, password_hash="$argon2id$changed", updated_at=MOMENT)
    )

    stored = repository.find_by_user_id(user_id)
    assert stored.password_hash == "$argon2id$changed"


def test_a_credential_can_be_written_for_a_user_the_store_has_never_seen():
    """The store does not check, and saying so is more useful than pretending otherwise.

    ``password_credentials.user_id`` is a primary key and **not** a foreign key into
    ``users`` - the identity tables were added without one, following ``wallets``
    rather than ``funds``. So this write succeeds, and the pairing between an
    account and its credential is guaranteed one layer up: ``SignUp`` writes both
    rows in a *single* unit of work, so they land together or not at all.

    Written as a test rather than left unsaid because the alternative is a reader
    assuming the database is doing work it is not. A constraint here would be a
    real backstop and belongs with the ``wallets`` foreign-key rebuild the roadmap
    already carries; what must not happen is somebody reasoning about this table's
    integrity as though it were already there.
    """
    repository = build_repository()

    repository.save(build(user_id=uuid4()))

    assert repository.find_by_user_id(uuid4()) is None


def test_the_hash_column_refuses_a_null_beneath_the_aggregate():
    """The column's ``NOT NULL``, proved by going around the aggregate that hides it.

    ``PasswordCredential`` refuses ``None`` *and* ``''`` in ``__post_init__``, so
    neither can reach the store through ``save`` - the obvious version of this test
    raises ``InvalidCredentialHashError`` from the aggregate and never involves
    SQLite at all. That is the aggregate doing its job, and it means the column's
    constraint can only be observed by writing SQL that does not pass through it.

    Which is what this does, and why it is worth a test rather than a shrug: the
    constraint is still worth having - a second writer, a hand-run migration, a
    restore from a file - and it is worth *knowing* that the ordinary path cannot
    exercise it. A reader who assumed otherwise would write the obvious test, watch
    the wrong exception come back, and conclude the column was unconstrained.

    ``connection.execute`` rather than a commit-then-assert, because the refusal is
    immediate: SQLite raises at the statement, and autocommit mode means there is no
    transaction left open to roll back.
    """
    connection = open_sqlite_connection(":memory:")

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO password_credentials (user_id, password_hash, updated_at) "
            "VALUES (?, ?, ?)",
            (str(uuid4()), None, "2026-03-02T12:00:00"),
        )


def test_the_stored_hash_survives_a_round_trip_unchanged():
    """Byte for byte, which is not a formality.

    An encoded argon2 hash contains ``$``, ``,``, ``=`` and base64 - and it is
    verified by being parsed, so a store that trimmed, truncated or re-encoded it
    would produce a credential that fails to verify for reasons nothing in the
    login path could explain. The realistic culprit is a ``VARCHAR(n)`` too short
    for the encoding, which SQLite would not enforce but another engine would.
    """
    user_id = uuid4()
    repository = build_repository()
    long_hash = "$argon2id$v=19$m=65536,t=3,p=4$" + "c2FsdA" * 20 + "$" + "aGFzaA" * 20

    repository.save(build(user_id=user_id, password_hash=long_hash))

    assert repository.find_by_user_id(user_id).password_hash == long_hash


def test_updated_at_keeps_its_time_of_day():
    """The trap a bare ``date`` column would set, at the point it would be set.

    Every aggregate in this codebase refuses a ``date`` where a moment belongs,
    and this is the store's end of it. A credential whose ``updated_at`` came back
    as midnight would read as set at the start of its day - which is harmless for
    a "your password is old" notice and a real bug the moment anything compares
    two of them.
    """
    user_id = uuid4()
    moment = datetime(2026, 3, 2, 23, 59, 59)
    repository = build_repository()

    repository.save(build(user_id=user_id, updated_at=moment))

    stored = repository.find_by_user_id(user_id).updated_at
    assert stored == moment
    assert stored.hour == 23


def test_two_users_have_two_credentials():
    """One row per account, and the lookup does not confuse them.

    Worth writing because the key is a user id and the whole lookup is by user id:
    a repository that ignored its argument and returned the only row would pass
    every test above in a database with one credential in it.
    """
    first, second = uuid4(), uuid4()
    repository = build_repository()

    repository.save(build(user_id=first, password_hash="$argon2id$first"))
    repository.save(build(user_id=second, password_hash="$argon2id$second"))

    assert repository.find_by_user_id(first).password_hash == "$argon2id$first"
    assert repository.find_by_user_id(second).password_hash == "$argon2id$second"


def test_a_window_of_moments_survives_the_text_encoding():
    """Microseconds and a negative offset are not part of the format, and this says so.

    The store writes moments as text through ``serialization.datetime_to_text``.
    Nothing in this phase stores a moment with microseconds or a timezone - every
    one comes from a ``datetime.now()`` or a fixture - and pinning the round trip
    for the ordinary case is what would catch a serialiser quietly switching to a
    format that drops the seconds.
    """
    user_id = uuid4()
    repository = build_repository()
    moment = datetime(2026, 1, 1) + timedelta(hours=13, minutes=45, seconds=9)

    repository.save(build(user_id=user_id, updated_at=moment))

    assert repository.find_by_user_id(user_id).updated_at == moment
