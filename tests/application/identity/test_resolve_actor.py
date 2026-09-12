"""Turning a token into a person, or refusing to.

This replaces the tests ``ResolveUserByEmail`` used to have, and the difference is
the phase: that class answered "who is this?" from an assertion and *decided* the
answer, creating an account for any address it had not seen. This one only looks
up, and there is no branch in which it creates anything - so a caller who presents
a token they made up gets a refusal and never an account. That is the property the
class below named ``TestItNeverInventsAnActor`` exists to pin.
"""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from app.application.identity.log_in import LogIn
from app.application.identity.resolve_actor import ResolveActorFromSession
from app.application.identity.sign_up import SignUp
from app.domain.identity.exception import InvalidSessionError, UserNotFoundError
from app.domain.identity.session import hash_session_token
from app.domain.identity.user import User
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
    open_sqlite_connection,
)

NOW = datetime(2026, 3, 2, 12, 0)
PASSWORD = "correct-horse-battery"
ADDRESS = "ada@example.com"


@pytest.fixture
def factory(tmp_path):
    return SqliteUnitOfWorkFactory(str(tmp_path / "identity.db"))


@pytest.fixture
def resolve(factory):
    return ResolveActorFromSession(factory)


@pytest.fixture
def account(factory, password_hasher):
    return SignUp(factory, password_hasher=password_hasher).execute(
        ADDRESS, PASSWORD, NOW
    )


@pytest.fixture
def log_in(factory, password_hasher):
    return LogIn(factory, password_hasher=password_hasher)


def backdate(factory, token, *, issued_at, expires_at):
    """Move a session's window, so expiry can be tested without waiting.

    The same technique ``test_actor.py`` uses over HTTP, and it is the only way to
    test an expired session at all: ``SESSION_LIFETIME`` is thirty days, and a test
    that slept would not be a test. Written through the repository rather than
    through SQL so that the row stays one the store itself could have produced.
    """
    uow = factory.start()
    try:
        stored = uow.sessions.find_by_token_hash(hash_session_token(token))
        stored.issued_at = issued_at
        stored.expires_at = expires_at
        uow.sessions.save(stored)
        uow.commit()
    finally:
        uow.rollback()


def delete_account(factory, user_id):
    """Remove a user row, underneath the repositories, on its own connection.

    **Nothing in the application deletes an account**, and this says so rather
    than working around it: ``UserRepository`` has no ``delete``, deliberately,
    because deleting a person is a feature with questions attached - what happens
    to their wallets, their plans, their transaction history - rather than a
    method. So reaching the orphaned-session state means going under the
    repository with SQL, and going under it *here* is what keeps the test honest
    about having built a state the application cannot produce.

    A connection of its own rather than the unit of work's, because the unit of
    work holds its connection privately and a test should not be the reason that
    changes. ``open_sqlite_connection`` is in autocommit mode, so the delete is
    durable on its own.
    """
    connection = open_sqlite_connection(factory.db_path)
    try:
        connection.execute("DELETE FROM users WHERE user_id = ?", (str(user_id),))
    finally:
        connection.close()


def _user_count(factory) -> int:
    """How many accounts exist, read through SQL rather than through a repository.

    There is no ``count`` on ``UserRepository`` and adding one for a test would put
    a method on a port to serve a single assertion - the same reason the orphan
    test goes under the repository. Read-only, on its own connection, for the same
    reason again.
    """
    connection = open_sqlite_connection(factory.db_path)
    try:
        return connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        connection.close()


class TestItResolvesAToken:
    def test_it_returns_the_account_the_token_was_issued_to(self, resolve, log_in, account):
        token = log_in.execute(ADDRESS, PASSWORD, NOW).token

        actor = resolve.execute(token, NOW)

        assert actor.user_id == account.user_id
        assert actor.email == ADDRESS

    def test_it_returns_a_user_and_not_a_bare_id(self, resolve, log_in, account):
        """The type, asserted because the id is all most callers use.

        Re-reading the account on each request is what keeps the actor a fact about
        the present rather than a claim from the past, and it is where a later
        "this account was deleted or disabled" belongs. A change to returning
        ``session.user_id`` would be invisible to every route - and would quietly
        remove the only read that can notice an account is gone.
        """
        token = log_in.execute(ADDRESS, PASSWORD, NOW).token

        assert isinstance(resolve.execute(token, NOW), User)

    def test_two_tokens_resolve_to_the_people_they_were_issued_to(
        self, resolve, log_in, factory, password_hasher
    ):
        """The control for the whole file.

        A method that returned the only user in the database would pass every
        resolution test above. Two accounts and two tokens is the cheapest thing
        that cannot.
        """
        sign_up = SignUp(factory, password_hasher=password_hasher)
        ada = sign_up.execute(ADDRESS, PASSWORD, NOW)
        grace = sign_up.execute("grace@example.com", PASSWORD, NOW)

        ada_token = log_in.execute(ADDRESS, PASSWORD, NOW).token
        grace_token = log_in.execute("grace@example.com", PASSWORD, NOW).token

        assert resolve.execute(ada_token, NOW).user_id == ada.user_id
        assert resolve.execute(grace_token, NOW).user_id == grace.user_id

    def test_the_same_token_resolves_the_same_way_every_time(self, resolve, log_in, account):
        """Two requests, one actor - which is what "stable across requests" rests on.

        Holds because the lookup is by hash and a hash is a pure function. Worth an
        assertion here rather than only at the HTTP boundary because this is the
        layer that would break it, by writing something time-dependent into the
        token handling.
        """
        token = log_in.execute(ADDRESS, PASSWORD, NOW).token

        first = resolve.execute(token, NOW)
        second = resolve.execute(token, NOW + timedelta(days=29))

        assert first.user_id == second.user_id


class TestItNeverInventsAnActor:
    """The phase's thesis, at the layer where it has a referent.

    ``ResolveUserByEmail`` found or *created* an account for whatever address it
    was handed, so an unknown caller became a user. There is no such branch here,
    and these tests say so from three directions - a token that was never issued, a
    token shaped like an address, and a stored hash presented as though it were a
    token.
    """

    def test_a_token_that_was_never_issued_is_refused(self, resolve, account):
        with pytest.raises(InvalidSessionError):
            resolve.execute("a-token-nobody-was-given", NOW)

    def test_a_refused_token_creates_no_account(self, resolve, factory, account):
        """The old behaviour, asserted as gone rather than assumed gone.

        This is the difference between the phase and its predecessor in one
        assertion: an unknown token must leave the user table exactly as it found
        it. Under ``ResolveUserByEmail`` the equivalent call returned a *new*
        account, which is how the shim turned an assertion into a user.
        """
        before = _user_count(factory)

        with pytest.raises(InvalidSessionError):
            resolve.execute("a-token-nobody-was-given", NOW)

        assert _user_count(factory) == before

    def test_a_token_shaped_like_an_address_is_refused(self, resolve, account):
        """The specific string the header used to carry, now meaningless.

        ``test_actor.py`` sends this over HTTP and asserts a 401; this is the same
        claim one layer down, where it is cheap to state and where the name of the
        old shim does not appear. If somebody reinstated an email-shaped fast path
        "for convenience", this is what would fail.
        """
        with pytest.raises(InvalidSessionError):
            resolve.execute(ADDRESS, NOW)

    def test_the_stored_hash_is_not_a_usable_token(self, resolve, log_in, factory, account):
        """The read that makes a stolen session table useless, asserted at the reader.

        The hash is always *derived* from a presented token by
        ``hash_session_token``, never taken from the request. So presenting the
        stored value hashes it again and matches nothing - somebody who reads the
        session table learns which sessions exist and cannot use a single one of
        them. That is exactly the property a password hash does *not* have.
        """
        token = log_in.execute(ADDRESS, PASSWORD, NOW).token

        with pytest.raises(InvalidSessionError):
            resolve.execute(hash_session_token(token), NOW)


class TestItRefusesEveryFailureTheSameWay:
    """One refusal for three situations, and the oracle that closes.

    Unknown, expired and orphaned all raise ``InvalidSessionError``. A caller who
    can tell them apart learns which tokens were once real, and there is nothing a
    client does differently in any of the three cases - all of them mean signing in
    again. The comparison below is therefore the test, not any single one of them.
    """

    def test_an_expired_session_is_refused(self, resolve, log_in, factory, account):
        token = log_in.execute(ADDRESS, PASSWORD, NOW).token
        backdate(
            factory,
            token,
            issued_at=NOW - timedelta(days=60),
            expires_at=NOW - timedelta(days=30),
        )

        with pytest.raises(InvalidSessionError):
            resolve.execute(token, NOW)

    def test_it_is_refused_at_the_instant_it_expires(self, resolve, log_in, factory, account):
        """The boundary, carried up from ``Session.is_expired``'s ``>=``.

        A session is expired *at* its expiry, not a moment later. The domain test
        pins the comparison; this pins that the use case asks the question with the
        moment it was handed rather than one it computed - a ``now`` read inside
        would make this assertion true by accident, and the one below false.
        """
        token = log_in.execute(ADDRESS, PASSWORD, NOW).token
        expiry = NOW + timedelta(days=1)
        backdate(factory, token, issued_at=NOW, expires_at=expiry)

        with pytest.raises(InvalidSessionError):
            resolve.execute(token, expiry)

    def test_it_is_still_good_a_moment_before(self, resolve, log_in, factory, account):
        """The other half of the boundary, without which the test above passes for a method that refuses everything."""
        token = log_in.execute(ADDRESS, PASSWORD, NOW).token
        expiry = NOW + timedelta(days=1)
        backdate(factory, token, issued_at=NOW, expires_at=expiry)

        assert resolve.execute(token, expiry - timedelta(seconds=1)).user_id == account.user_id

    def test_a_token_for_an_account_that_no_longer_exists_is_refused(
        self, resolve, log_in, factory, account
    ):
        """The orphan, and why it is not a 404.

        A session whose user row is gone - the account was deleted between the
        login and this request. ``get_by_id`` raises ``UserNotFoundError``, which
        would reach a client as "no such user" and is a *different answer to a
        question with one answer*. So it is caught and re-raised as the shared
        refusal, ``from None`` so the original does not surface in the message.

        The user is deleted underneath the repository, because nothing in the
        application deletes one - which is the point: this is the state a future
        account deletion produces, exercised before it exists.
        """
        token = log_in.execute(ADDRESS, PASSWORD, NOW).token
        delete_account(factory, account.user_id)

        with pytest.raises(InvalidSessionError):
            resolve.execute(token, NOW)

    def test_the_three_refusals_are_indistinguishable(
        self, resolve, log_in, factory, account
    ):
        """The oracle, closed - the reason the three cases share one class.

        The class name is what the API sends as ``error`` and the message is its
        ``detail``, so either one differing is the leak. An orphaned session is the
        interesting one here: it arrives from a *different* exception
        (``UserNotFoundError``) raised two frames away, and it is the case where a
        careless ``from`` or a second ``raise`` would let a distinguishing detail
        through.
        """
        expired = log_in.execute(ADDRESS, PASSWORD, NOW).token
        backdate(
            factory,
            expired,
            issued_at=NOW - timedelta(days=60),
            expires_at=NOW - timedelta(days=30),
        )

        orphaned = log_in.execute(ADDRESS, PASSWORD, NOW).token
        delete_account(factory, account.user_id)

        refusals = []
        for token in ("never-issued", expired, orphaned):
            with pytest.raises(InvalidSessionError) as raised:
                resolve.execute(token, NOW)
            refusals.append(raised.value)

        for refusal in refusals:
            assert type(refusal) is type(refusals[0])
            assert str(refusal) == str(refusals[0])

    def test_no_refusal_names_the_token_it_was_given(self, resolve, log_in, factory, account):
        """A message that echoes the token is a message that puts it somewhere.

        The refusal travels to ``errors._detail``, which renders it into a response
        body, and to whatever logs the API writes. A token in either is a live
        credential outside the database - the same reasoning that keeps the password
        out of ``PlainPassword``'s repr.
        """
        token = "a-token-that-should-not-be-echoed-back"

        with pytest.raises(InvalidSessionError) as raised:
            resolve.execute(token, NOW)

        assert token not in str(raised.value)

    def test_an_empty_token_is_refused(self, resolve, account):
        """The value a bug produces - an unset variable, a session file read as empty."""
        with pytest.raises(InvalidSessionError):
            resolve.execute("", NOW)


class TestItTakesTheMoment:
    """The clock is passed in, never read - the rule every aggregate here follows.

    Worth its own class because this is where the wall clock would enter the
    application layer if somebody reached for it: the method is called once per
    authenticated request, and a ``datetime.now()`` inside would be invisible until
    the day a test needed to ask what it thought at a particular moment.
    """

    def test_a_session_is_live_against_a_moment_it_covers(self, resolve, log_in, account):
        token = log_in.execute(ADDRESS, PASSWORD, NOW).token

        assert resolve.execute(token, NOW).user_id == account.user_id

    def test_and_dead_against_one_it_does_not(self, resolve, log_in, account):
        """The same session, the same call, two moments - so the moment is being used.

        Asserted with a moment far in the future rather than by changing the row,
        which is what makes this a statement about the argument: a method that read
        its own clock would refuse here only if the real clock happened to be past
        the session's expiry, and it is not.
        """
        token = log_in.execute(ADDRESS, PASSWORD, NOW).token

        with pytest.raises(InvalidSessionError):
            resolve.execute(token, datetime(2099, 1, 1))

    def test_a_session_issued_in_the_future_is_not_refused_here(self, resolve, log_in, factory, account):
        """No opinion about the clock, and the absence is ``Session``'s.

        A session whose window is ahead of the moment being asked about is a
        machine whose clock disagrees with the store's. ``is_expired`` answers one
        question and this is not it: the check is "has it ended", so a window that
        has not started yet has not ended. Adding a "not yet valid" rule here would
        be a second policy, in a second place, that nothing else knows about.
        """
        token = log_in.execute(ADDRESS, PASSWORD, NOW).token
        backdate(
            factory,
            token,
            issued_at=NOW + timedelta(days=1),
            expires_at=NOW + timedelta(days=31),
        )

        assert resolve.execute(token, NOW).user_id == account.user_id


def test_a_token_for_a_deleted_account_is_not_a_404(resolve, log_in, factory, password_hasher):
    """The one-branch rule stated as a fact about what is *not* raised.

    ``UserNotFoundError`` is a 404 at the API boundary and ``InvalidSessionError``
    is a 401, so which one escapes this method decides what a client is told. A
    caller who has a valid token for an account that was deleted is not "asking
    about a user that does not exist" - they are unauthenticated, and the fix is
    the same as for any other dead token.
    """
    sign_up = SignUp(factory, password_hasher=password_hasher)
    account = sign_up.execute("gone@example.com", PASSWORD, NOW)
    token = LogIn(factory, password_hasher=password_hasher).execute(
        "gone@example.com", PASSWORD, NOW
    ).token

    delete_account(factory, account.user_id)

    with pytest.raises(InvalidSessionError) as raised:
        resolve.execute(token, NOW)

    # ``UserNotFoundError`` is what the store raised on the way through, and the
    # assertion is that it did not escape: the two errors are different classes
    # answering different questions, and only one of them is about the caller.
    assert not isinstance(raised.value, UserNotFoundError)
    assert type(raised.value) is InvalidSessionError


def test_the_uuid_of_a_session_is_not_enough_to_resolve_it(resolve, log_in, factory, account):
    """A session id is not a secret and must not be usable as one.

    Everything about a session except the token is ordinary data: the id, the user
    id, the two moments. Only the token is unguessable, so only the token may be
    what authenticates - and a lookup by ``session_id`` would make a value that
    appears in a database dump sufficient to be the person it belongs to.
    """
    logged_in = log_in.execute(ADDRESS, PASSWORD, NOW).token
    assert logged_in

    uow = factory.start()
    try:
        stored = uow.sessions.find_by_token_hash(hash_session_token(logged_in))
    finally:
        uow.rollback()

    with pytest.raises(InvalidSessionError):
        resolve.execute(str(stored.session_id), NOW)

    with pytest.raises(InvalidSessionError):
        resolve.execute(str(uuid4()), NOW)
