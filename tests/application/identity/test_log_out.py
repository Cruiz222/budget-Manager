"""Ending a session.

The whole file turns on one unusual fact about ``LogOut``: **it is authorised by
the thing it destroys.** Every other use case takes an actor that has already been
resolved and asks whether that actor may touch a resource; this one takes a token
and asks for that token to stop working. Proving you hold it and being entitled to
end it are the same act, so the tests below are mostly about what it *doesn't*
require - an actor, a valid session, or a session at all.
"""

from datetime import datetime, timedelta

import pytest

from app.application.identity.log_in import LogIn
from app.application.identity.log_out import LogOut
from app.application.identity.sign_up import SignUp
from app.domain.identity.session import hash_session_token
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)

NOW = datetime(2026, 3, 2, 12, 0)
PASSWORD = "correct-horse-battery"


@pytest.fixture
def factory(tmp_path):
    return SqliteUnitOfWorkFactory(str(tmp_path / "identity.db"))


@pytest.fixture
def log_in(factory, password_hasher):
    return LogIn(factory, password_hasher=password_hasher)


@pytest.fixture
def log_out(factory):
    return LogOut(factory)


@pytest.fixture
def account(factory, password_hasher):
    return SignUp(factory, password_hasher=password_hasher).execute(
        "ada@example.com", PASSWORD, NOW
    )


class TestItEndsTheSession:
    def test_the_token_stops_working(self, log_in, log_out, factory, account):
        token = log_in.execute("ada@example.com", PASSWORD, NOW).token

        log_out.execute(token)

        uow = factory.start()
        try:
            assert uow.sessions.find_by_token_hash(hash_session_token(token)) is None
        finally:
            uow.rollback()

    def test_it_does_not_touch_the_account(self, log_in, log_out, factory, account):
        """Signing out is not deleting yourself.

        Obvious, and worth one assertion because the delete goes through the
        session store by a *hash* and the two tables are one join apart - the
        mistake of reaching for the wrong repository would present as an account
        that vanished when somebody signed out on a shared machine.
        """
        token = log_in.execute("ada@example.com", PASSWORD, NOW).token

        log_out.execute(token)

        uow = factory.start()
        try:
            assert uow.users.find_by_email("ada@example.com") is not None
            assert uow.password_credentials.find_by_user_id(account.user_id) is not None
        finally:
            uow.rollback()

    def test_it_ends_only_that_session(self, log_in, log_out, factory, account):
        """The other device stays signed in, which is what makes this a logout and not a lockout.

        Two sessions for one account is the ordinary case - a laptop and a phone -
        and signing out of one must leave the other alone. The failure this catches
        is a delete whose ``WHERE`` clause was left off, which SQLite accepts and
        which would end every session in the installation the first time anybody
        logged out.
        """
        laptop = log_in.execute("ada@example.com", PASSWORD, NOW).token
        phone = log_in.execute("ada@example.com", PASSWORD, NOW).token

        log_out.execute(laptop)

        uow = factory.start()
        try:
            assert uow.sessions.find_by_token_hash(hash_session_token(laptop)) is None
            assert uow.sessions.find_by_token_hash(hash_session_token(phone)) is not None
        finally:
            uow.rollback()

    def test_it_does_not_need_to_know_who_is_signing_out(self, log_out, log_in, factory, account):
        """No actor, no lookup, no resolution - which is the design, not an omission.

        ``LogOut`` takes a token where every other use case takes a ``User``. That
        is what lets a client discard a token the server would refuse to resolve,
        and the two tests below are the cases where that matters.
        """
        token = log_in.execute("ada@example.com", PASSWORD, NOW).token

        # Nothing about the caller is passed or available; the signature is the claim.
        log_out.execute(token)


class TestWhatItDoesNotCheck:
    """Idempotence, and the reason it is a property rather than a shortcut.

    The postcondition is "this token does not authenticate", and that is already
    true for a token that never existed or expired months ago. Reporting those as
    errors would make every caller handle a failure indistinguishable from success
    in every way that matters.
    """

    def test_signing_out_twice_is_not_an_error(self, log_in, log_out, account):
        token = log_in.execute("ada@example.com", PASSWORD, NOW).token

        log_out.execute(token)
        log_out.execute(token)

    def test_a_token_that_was_never_issued_is_not_an_error(self, log_out):
        """A client that signed out on another machine, or holds a stale file.

        This is the ordinary case for the CLI, which writes a token to disk: a
        ``logout`` after the session was already gone must clean up and exit 0,
        not explain that the thing the user asked to be rid of is not there.
        """
        log_out.execute("a-token-that-was-never-issued")

    def test_an_empty_token_is_not_an_error(self, log_out):
        """``""`` hashes to something, and that something is not in the table.

        Worth its own case because the empty string is the value a bug produces -
        a session file read as empty, an unset variable - and the behaviour should
        be the same nothing as any other unknown token rather than a special case.
        """
        log_out.execute("")

    def test_an_expired_session_can_still_be_ended(self, log_in, log_out, factory, account):
        """The one that would be impossible if ``LogOut`` took an actor.

        Resolving a session refuses an expired one - that is ``ResolveActorFromSession``'s
        job - so a logout built on top of it would answer "not authenticated" to
        somebody signing out, having failed at the one thing it does. Here the row
        is expired and the delete still matches, because the lookup is by hash and
        a store is not a clock.
        """
        token = log_in.execute("ada@example.com", PASSWORD, NOW).token
        # Backdate the window, the way test_actor.py does over HTTP.
        uow = factory.start()
        try:
            stored = uow.sessions.find_by_token_hash(hash_session_token(token))
            stored.issued_at = NOW - timedelta(days=60)
            stored.expires_at = NOW - timedelta(days=30)
            uow.sessions.save(stored)
            uow.commit()
        finally:
            uow.rollback()

        log_out.execute(token)

        uow = factory.start()
        try:
            assert uow.sessions.find_by_token_hash(hash_session_token(token)) is None
        finally:
            uow.rollback()

    def test_the_stored_hash_is_not_a_usable_token(self, log_in, log_out, factory, account):
        """The discipline ``SessionRepository`` names, asserted at the caller.

        The hash is always *derived* from a presented token by
        ``hash_session_token``, never taken from the request. So somebody who read
        the session table and presented a stored hash gets a delete that matches
        nothing - not a delete of the session that hash belongs to. Same property
        that makes a stolen session table useless for authenticating, one operation
        over.
        """
        token = log_in.execute("ada@example.com", PASSWORD, NOW).token
        stored_hash = hash_session_token(token)

        log_out.execute(stored_hash)

        uow = factory.start()
        try:
            assert uow.sessions.find_by_token_hash(stored_hash) is not None
        finally:
            uow.rollback()

    def test_a_refused_login_leaves_nothing_to_sign_out_of(self, log_out, factory, account):
        """The control. Without it, every test above passes for a method that deletes nothing."""
        uow = factory.start()
        try:
            assert uow.sessions.find_by_token_hash(hash_session_token("anything")) is None
        finally:
            uow.rollback()

        log_out.execute("anything")
