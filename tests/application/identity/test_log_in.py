"""Proving an identity, and handing out a session.

Two properties carry this file. The first is that a successful login returns a
token that resolves back to the account it was issued to - the round trip the
whole phase rests on. The second is that it refuses a wrong address and a wrong
password with **the same answer**, which is decision 55 applied to identities and
the one thing here worth an unusual amount of test.
"""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from app.application.identity.log_in import LogIn
from app.application.identity.sign_up import SignUp
from app.domain.identity.exception import (
    InvalidCredentialsError,
    InvalidPasswordError,
    WeakPasswordError,
)
from app.domain.identity.session import SESSION_LIFETIME, hash_session_token
from app.domain.identity.user import User
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)

NOW = datetime(2026, 3, 2, 12, 0)
PASSWORD = "correct-horse-battery"
ADDRESS = "ada@example.com"


@pytest.fixture
def factory(tmp_path):
    return SqliteUnitOfWorkFactory(str(tmp_path / "identity.db"))


@pytest.fixture
def sign_up(factory, password_hasher):
    return SignUp(factory, password_hasher=password_hasher)


@pytest.fixture
def log_in(factory, password_hasher):
    return LogIn(factory, password_hasher=password_hasher)


@pytest.fixture
def account(sign_up):
    return sign_up.execute(ADDRESS, PASSWORD, NOW)


class TestASuccessfulLogin:
    def test_it_returns_the_account_the_credentials_belong_to(self, log_in, account):
        logged_in = log_in.execute(ADDRESS, PASSWORD, NOW)

        assert logged_in.user.user_id == account.user_id
        assert logged_in.user.email == ADDRESS

    def test_it_returns_a_session_and_the_token_that_matches_it(self, log_in, account):
        """The pair, and the relation between them - not two things that arrived together.

        The token is what the client keeps and the session is what the server
        stores, so the property that matters is that hashing the first gives the
        second. Asserting only "there is a token" would pass for a token that
        authenticates nothing.
        """
        logged_in = log_in.execute(ADDRESS, PASSWORD, NOW)

        assert logged_in.session.user_id == account.user_id
        assert logged_in.session.token_hash == hash_session_token(logged_in.token)

    def test_the_session_is_stored(self, log_in, factory, account):
        """Handing a token to a client without a row to match it against is a login that does not last.

        Read back through the repository rather than trusting the returned
        aggregate, which is a value the use case built and could return whether or
        not it saved it.
        """
        logged_in = log_in.execute(ADDRESS, PASSWORD, NOW)

        uow = factory.start()
        try:
            stored = uow.sessions.find_by_token_hash(hash_session_token(logged_in.token))
        finally:
            uow.rollback()

        assert stored is not None
        assert stored.session_id == logged_in.session.session_id

    def test_the_session_lasts_the_configured_lifetime(self, log_in, account):
        logged_in = log_in.execute(ADDRESS, PASSWORD, NOW)

        assert logged_in.session.issued_at == NOW
        assert logged_in.session.expires_at == NOW + SESSION_LIFETIME

    def test_the_token_is_not_stored(self, log_in, factory, account):
        """The plaintext exists in one return value and nowhere else.

        A store that kept the token - as a column beside the hash, or in a second
        table "for debugging" - would be a copy of every live credential sitting in
        the database, and reading it would be enough to be anybody. The lookup
        by plaintext finding nothing is what says it is not there.
        """
        logged_in = log_in.execute(ADDRESS, PASSWORD, NOW)

        uow = factory.start()
        try:
            assert uow.sessions.find_by_token_hash(logged_in.token) is None
        finally:
            uow.rollback()

    def test_it_does_not_return_the_password(self, log_in, account):
        """``LoggedIn`` has three fields and the password is not one of them.

        Structural rather than behavioural, and worth pinning because adding a
        fourth field is exactly the change somebody makes when they want to
        "re-hash on login" - which is a real feature and must take the password as
        an argument, not smuggle it out in the result.
        """
        logged_in = log_in.execute(ADDRESS, PASSWORD, NOW)

        assert not hasattr(logged_in, "password")
        assert PASSWORD not in repr(logged_in)

    def test_it_does_not_return_the_password_hash_either(self, log_in, account):
        """The one value a login is uniquely positioned to leak.

        This is the only method in the codebase that loads a credential *and*
        returns something to a caller. Attaching the hash to the returned ``User``
        would put it on the object the API renders through ``translate.user_out``
        for the rest of the request - see ``PasswordCredential``, and the decision
        that put the hash in its own table.
        """
        logged_in = log_in.execute(ADDRESS, PASSWORD, NOW)

        assert not hasattr(logged_in.user, "password_hash")

    def test_two_logins_are_two_sessions(self, log_in, factory, account):
        """A laptop and a phone, and neither ends the other.

        The alternative - one live session per account - is a design some systems
        choose, and it means signing in on a second device silently signs you out
        of the first. That is a decision to make deliberately; this asserts it was
        not made by accident.
        """
        first = log_in.execute(ADDRESS, PASSWORD, NOW)
        second = log_in.execute(ADDRESS, PASSWORD, NOW)

        assert first.token != second.token
        assert first.session.session_id != second.session.session_id

        uow = factory.start()
        try:
            assert uow.sessions.find_by_token_hash(hash_session_token(first.token))
            assert uow.sessions.find_by_token_hash(hash_session_token(second.token))
        finally:
            uow.rollback()

    def test_it_takes_a_moment_and_issues_the_session_at_it(self, log_in, account):
        """``now`` is used, not read - the same contract as ``Session.issue``.

        Asserted with a moment far from the real clock, so a ``datetime.now()``
        hiding anywhere in the path produces an expiry that does not match.
        """
        long_ago = datetime(2020, 1, 1, 9, 30)

        logged_in = log_in.execute(ADDRESS, PASSWORD, long_ago)

        assert logged_in.session.issued_at == long_ago
        assert logged_in.session.expires_at == long_ago + SESSION_LIFETIME


class TestTheSameRefusalForBoth:
    """Decision 55, at the login form.

    This class is the reason ``InvalidCredentialsError`` exists as one class. Every
    test in it would pass - individually - against a login that distinguished "no
    such account" from "wrong password", which is what makes the pair of
    assertions below rather than a single one the point.
    """

    def test_an_address_with_no_account_is_refused(self, log_in):
        with pytest.raises(InvalidCredentialsError):
            log_in.execute("nobody@example.com", PASSWORD, NOW)

    def test_a_wrong_password_is_refused(self, log_in, account):
        with pytest.raises(InvalidCredentialsError):
            log_in.execute(ADDRESS, "not-the-password", NOW)

    def test_the_two_refusals_are_indistinguishable(self, log_in, account):
        """The oracle, closed.

        A caller who can tell these apart can walk a list of addresses and learn
        which ones are registered without guessing a single password. So the check
        is not just that both raise, but that the *class* and the *message* agree -
        the class is what the API sends as ``error`` and the message is its
        ``detail``, so either one differing is the leak.

        ``from None`` on the comparison side would be wrong here; the exception
        objects are what is being compared.
        """
        with pytest.raises(InvalidCredentialsError) as unknown_address:
            log_in.execute("nobody@example.com", PASSWORD, NOW)

        with pytest.raises(InvalidCredentialsError) as wrong_password:
            log_in.execute(ADDRESS, "not-the-password", NOW)

        assert type(unknown_address.value) is type(wrong_password.value)
        assert str(unknown_address.value) == str(wrong_password.value)

    def test_neither_refusal_names_the_address_it_was_given(self, log_in, account):
        """A message that echoes the input is a message that confirms it.

        "no account for ada@example.com" and "that address is already registered"
        read very differently to a person, and only one of them is safe here. It
        also keeps the address out of a response body and out of whatever logs the
        API's error handler writes to.
        """
        with pytest.raises(InvalidCredentialsError) as raised:
            log_in.execute(ADDRESS, "not-the-password", NOW)

        assert ADDRESS not in str(raised.value)

    def test_a_refused_login_leaves_no_session_behind(self, log_in, factory, account):
        """Nothing half-done. The refusal happens before the session exists.

        Ordering, and the observable consequence of getting it wrong is a session
        that was never handed to anybody but is a live row - which nobody would
        notice, because a token nobody holds cannot be presented.
        """
        with pytest.raises(InvalidCredentialsError):
            log_in.execute(ADDRESS, "not-the-password", NOW)

        uow = factory.start()
        try:
            assert uow.sessions.find_by_token_hash(hash_session_token("anything")) is None
        finally:
            uow.rollback()

    def test_an_account_with_no_credential_is_refused_the_same_way(
        self, factory, log_in
    ):
        """The branch Phase 2c will make reachable, exercised before it is.

        An account with no password is what a Google sign-in produces. Nothing
        creates one today, so this test writes the user row directly - which is
        the only way to reach the branch, and the reason to reach it is that the
        alternative is ``None`` arriving at the hasher and raising a ``TypeError``
        that reports as a 500 rather than as a refusal.

        Note it must be ``InvalidCredentialsError`` and not something more
        specific: telling an OIDC account's owner "that account has no password"
        is the same disclosure the shared refusal exists to prevent.
        """
        user = User(
            user_id=uuid4(),
            email=ADDRESS,
            google_subject=None,
            created_at=NOW,
        )
        uow = factory.start()
        try:
            uow.users.save(user)
            uow.commit()
        finally:
            uow.rollback()

        with pytest.raises(InvalidCredentialsError):
            log_in.execute(ADDRESS, PASSWORD, NOW)


class TestWhatIsRefusedBeforeTheHasher:
    """A password that could not be one is refused as malformed, not as wrong.

    Stated in ``LogIn``'s docstring and worth a test, because collapsing the two
    would send somebody to reset a password they had typed correctly - and
    because it leaks nothing: the length policy is public and every stored
    password satisfies it, so a password failing it was never anybody's.
    """

    def test_a_too_short_password_is_refused_as_weak_rather_than_wrong(
        self, log_in, account
    ):
        with pytest.raises(WeakPasswordError):
            log_in.execute(ADDRESS, "short", NOW)

    def test_an_empty_password_is_refused_as_a_boundary_bug(self, log_in, account):
        with pytest.raises(InvalidPasswordError) as raised:
            log_in.execute(ADDRESS, "", NOW)

        assert not isinstance(raised.value, WeakPasswordError)

    def test_a_malformed_address_does_not_reach_the_password_policy(self, log_in):
        """An address that is not one has no account, so it is refused as credentials.

        ``LogIn`` looks the address up rather than constructing a ``User``, so there
        is no email policy on this path and nothing to validate it - which means the
        refusal a caller gets for ``not-an-address`` is the same shared one. Worth
        pinning so that an ``InvalidUserEmailError`` appearing here later is a
        deliberate change rather than a drive-by, since it would be a *different*
        answer for a malformed address than for an unknown one, and that difference
        is a smaller version of the oracle.
        """
        with pytest.raises(InvalidCredentialsError):
            log_in.execute("not-an-address", PASSWORD, NOW)


def test_a_login_is_only_good_for_the_password_it_was_given(log_in, sign_up, factory):
    """Two accounts, two passwords, and no confusion between them.

    The control for the whole file: a ``find_by_user_id`` that ignored its argument
    and returned the only credential would pass every test above in a database
    with one account in it.
    """
    sign_up.execute(ADDRESS, PASSWORD, NOW)
    sign_up.execute("grace@example.com", "a-completely-different-one", NOW)

    assert log_in.execute(ADDRESS, PASSWORD, NOW).user.email == ADDRESS
    assert (
        log_in.execute("grace@example.com", "a-completely-different-one", NOW).user.email
        == "grace@example.com"
    )

    with pytest.raises(InvalidCredentialsError):
        log_in.execute("grace@example.com", PASSWORD, NOW)


def test_the_address_is_folded_on_the_way_in(log_in, sign_up):
    """A person who signed up as ``Ada@Example.com`` and types it in lower case gets in.

    The fold is the application's, applied at sign-up and by ``find_by_email`` -
    which is why this passes without ``LogIn`` doing anything about it. Asserted
    from this side because "I can register but not log in" is the shape this bug
    takes, and it is the one place the two halves have to agree.
    """
    sign_up.execute("  Ada@Example.com  ", PASSWORD, NOW)

    assert log_in.execute("ada@example.com", PASSWORD, NOW).user.email == "ada@example.com"


def test_a_session_issued_now_is_not_yet_expired(log_in, account):
    """The round trip through the clock, which is what every request depends on.

    ``is_expired`` is tested at its boundary in the domain. This is the integration
    end of it: a session issued at ``NOW`` and checked at ``NOW`` must be live, and
    a login whose expiry came out in the past would produce a token that works
    exactly once - or never, depending on where the request lands relative to it.
    """
    logged_in = log_in.execute(ADDRESS, PASSWORD, NOW)

    assert logged_in.session.is_expired(NOW) is False
    assert logged_in.session.is_expired(NOW + timedelta(seconds=1)) is False
    assert logged_in.session.is_expired(NOW + SESSION_LIFETIME) is True
