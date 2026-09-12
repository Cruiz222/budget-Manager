import sqlite3
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.identity.exception import InvalidSessionWindowError
from app.domain.identity.session import SESSION_LIFETIME, Session, hash_session_token
from app.infrastructure.persistence.sqlite_unit_of_work import open_sqlite_connection
from app.infrastructure.repositories.sqlite_session_repository import (
    SqliteSessionRepository,
)

ISSUED = datetime(2026, 3, 2, 12, 0)
TOKEN = "a-token-the-client-holds"


def build_repository():
    """A session store over its own in-memory database.

    ``open_sqlite_connection`` rather than a raw ``sqlite3.connect``, so these tests
    run the real schema - which is what makes the ``UNIQUE`` on ``token_hash`` and
    the text encoding of the two moments real rather than assumed.
    """
    return SqliteSessionRepository(open_sqlite_connection(":memory:"))


def build(token=TOKEN, user_id=None, issued_at=ISSUED, lifetime=SESSION_LIFETIME):
    return Session(
        session_id=uuid4(),
        user_id=user_id if user_id is not None else uuid4(),
        token_hash=hash_session_token(token),
        issued_at=issued_at,
        expires_at=issued_at + lifetime,
    )


def test_save_and_find_round_trips_the_session():
    user_id = uuid4()
    repository = build_repository()
    session = build(user_id=user_id)

    repository.save(session)

    stored = repository.find_by_token_hash(hash_session_token(TOKEN))
    assert stored.session_id == session.session_id
    assert stored.user_id == user_id
    assert stored.token_hash == session.token_hash
    assert stored.issued_at == ISSUED
    assert stored.expires_at == ISSUED + SESSION_LIFETIME


def test_a_token_hash_nobody_holds_is_none():
    repository = build_repository()
    repository.save(build())

    assert repository.find_by_token_hash(hash_session_token("some-other-token")) is None


def test_the_lookup_is_by_hash_and_the_plaintext_finds_nothing():
    """The property that makes this table safe to lose, asserted at the store.

    What is stored is the *hash*, and the only way to find a row is to present the
    token it was made from - because the caller hashes what it was given and looks
    that up. So a copy of this table is a list of sessions that cannot be used:
    nothing in it can be presented to authenticate, and hashing it again would
    match nothing.

    The second assertion is the one worth having. A repository that stored the
    token itself would pass every other test in this file and fail this one.
    """
    repository = build_repository()
    repository.save(build(token=TOKEN))

    assert repository.find_by_token_hash(TOKEN) is None
    assert repository.find_by_token_hash(hash_session_token(TOKEN)) is not None


def test_finding_a_session_does_not_care_whether_it_has_expired():
    """A store is not a clock, and this is the boundary between the two.

    ``find_by_token_hash`` answers "is there a session for this hash" and
    ``Session.is_expired`` answers "and is it still good". Keeping them apart is
    what lets expiry be tested without a store and a store be tested without a
    clock - and it means a read here never quietly performs a deletion, which is
    the kind of thing discovered during an incident.

    An expired session is a row this repository returns, and ``LogOut`` depends on
    that: a client discarding a token it has held too long must still be able to.
    """
    repository = build_repository()
    long_ago = datetime(2020, 1, 1)
    repository.save(build(issued_at=long_ago, lifetime=timedelta(days=1)))

    found = repository.find_by_token_hash(hash_session_token(TOKEN))

    assert found is not None
    assert found.is_expired(ISSUED) is True


def test_delete_removes_the_session():
    repository = build_repository()
    repository.save(build())

    repository.delete_by_token_hash(hash_session_token(TOKEN))

    assert repository.find_by_token_hash(hash_session_token(TOKEN)) is None


def test_deleting_a_session_that_is_not_there_is_allowed():
    """Idempotent, and it must not raise - see ``LogOut``.

    The row being absent is the state the caller asked for, so reporting it as
    failure would turn "sign out twice" into an error. It is also the ordinary
    case for a client that signed out on another machine, or whose session was
    already gone.
    """
    repository = build_repository()

    repository.delete_by_token_hash(hash_session_token("never-existed"))


def test_deleting_one_session_leaves_the_others():
    """By hash, so it removes one row and not the table.

    The mistake this catches is a ``DELETE`` with the argument left out of the
    ``WHERE`` clause - which SQLite accepts, which removes every session in the
    database, and which would sign out every user of the installation the first
    time anybody logged out. A single-session test would not notice.
    """
    mine, theirs = "my-token", "their-token"
    repository = build_repository()
    repository.save(build(token=mine))
    repository.save(build(token=theirs))

    repository.delete_by_token_hash(hash_session_token(mine))

    assert repository.find_by_token_hash(hash_session_token(mine)) is None
    assert repository.find_by_token_hash(hash_session_token(theirs)) is not None


def test_two_sessions_of_one_user_are_two_rows():
    """A person on a laptop and a phone, which is the ordinary case.

    Deliberately *not* unique on ``user_id``: nothing in this design says a person
    has one device, and signing in on a second one must not evict the first.
    """
    user_id = uuid4()
    repository = build_repository()
    repository.save(build(token="laptop", user_id=user_id))
    repository.save(build(token="phone", user_id=user_id))

    assert repository.find_by_token_hash(hash_session_token("laptop")) is not None
    assert repository.find_by_token_hash(hash_session_token("phone")) is not None


def test_two_sessions_cannot_share_a_token_hash():
    """``UNIQUE``, as the backstop rather than the rule.

    Two sessions sharing a token cannot realistically happen - it is 256 bits of
    CSPRNG output - and the constraint is here for the case where it happens
    anyway: a broken RNG, a row copied by hand. Without it, one token would resolve
    to two identities and the lookup would return whichever row the table happened
    to produce first, which is a failure with no visible cause.

    Distinct ``session_id``s, so the collision that fires is the token and not the
    key.
    """
    repository = build_repository()
    repository.save(build(token=TOKEN))

    with pytest.raises(sqlite3.IntegrityError):
        repository.save(build(token=TOKEN))


def test_save_overwrites_the_row_with_the_same_id():
    """An upsert keyed on ``session_id``, and its purpose is honesty.

    Nothing in this codebase re-saves a session - there is nothing about one that
    changes, since expiry is fixed at issue and revocation is deletion. The update
    branch exists so that a future "extend this session" does not have to be
    discovered as a silent no-op first.

    Note the token hash moves with it, which is what makes the ``UNIQUE``
    constraint above meaningful: an upsert that changed only ``expires_at`` would
    leave the old token working.
    """
    user_id = uuid4()
    repository = build_repository()
    session = build(token="old-token", user_id=user_id)
    repository.save(session)

    repository.save(
        Session(
            session_id=session.session_id,
            user_id=user_id,
            token_hash=hash_session_token("new-token"),
            issued_at=ISSUED,
            expires_at=ISSUED + timedelta(days=1),
        )
    )

    assert repository.find_by_token_hash(hash_session_token("old-token")) is None
    assert repository.find_by_token_hash(hash_session_token("new-token")) is not None


def test_the_moments_keep_their_time_of_day():
    """Every aggregate here refuses a ``date`` where a moment belongs; this is the store's end.

    A session window that came back at midnight at both ends would still compare
    correctly against another moment - which is exactly why this is worth pinning
    rather than inferring. Nothing would fail until somebody asked what time a
    session was issued.
    """
    repository = build_repository()
    moment = datetime(2026, 3, 2, 23, 59, 59)
    repository.save(build(issued_at=moment, lifetime=timedelta(hours=1)))

    stored = repository.find_by_token_hash(hash_session_token(TOKEN))

    assert stored.issued_at == moment
    assert stored.expires_at == moment + timedelta(hours=1)
    assert stored.issued_at.hour == 23


def test_a_row_whose_window_is_backwards_is_refused_at_load():
    """The read is not a trust exercise: the constructor runs on the way back in.

    ``Session`` refuses a window that closes before it opens, and
    ``_row_to_session`` runs that constructor - so a corrupt row is refused where
    it is read rather than becoming a session that is dead on arrival. The failure
    it prevents is a login that reports success and then does not work, which is
    the hardest kind to trace back to its cause.

    Written by going under the repository with SQL, because the repository itself
    cannot produce this state - which is the point.
    """
    connection = open_sqlite_connection(":memory:")
    repository = SqliteSessionRepository(connection)
    connection.execute(
        """
        INSERT INTO sessions
            (session_id, user_id, token_hash, issued_at, expires_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            str(uuid4()),
            str(uuid4()),
            hash_session_token(TOKEN),
            "2026-03-02T12:00:00",
            "2026-03-01T12:00:00",
        ),
    )

    with pytest.raises(InvalidSessionWindowError):
        repository.find_by_token_hash(hash_session_token(TOKEN))
