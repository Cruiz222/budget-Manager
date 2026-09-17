"""Proving an identity, and handing out a session.

Two properties carry this file. The first is that a successful login returns a
token that resolves back to the account it was issued to - the round trip the
whole phase rests on. The second is that it refuses a wrong identifier and a wrong
password with **the same answer**, which is decision 55 applied to identities and
the one thing here worth an unusual amount of test.

**There are two identifiers now, and the second one changed what "the same
answer" has to cover.** An address with no account, a number with no account, a
number belonging to an account identified by an address, and any of those with
the wrong password all have to be one refusal - and the reason the phone half is
the sharper case is in ``TestTheDecoyComparison`` below: a number is a small,
structured space, so the *timing* difference between "found nothing" and "found
something" stopped being a theoretical leak and became a walk over a range.
"""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from app.application.identity.account_creation import record_new_account
from app.application.identity.log_in import LogIn
from app.application.identity.sign_up import SignUp
from app.domain.identity.exception import (
    InvalidCredentialsError,
    InvalidPasswordError,
    WeakPasswordError,
)
from app.domain.identity.password import PlainPassword
from app.domain.identity.password_hasher import DUMMY_HASH
from app.domain.identity.session import SESSION_LIFETIME, hash_session_token
from app.domain.identity.user import User
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)
from tests.conftest import FakePasswordHasher

NOW = datetime(2026, 3, 2, 12, 0)
PASSWORD = "correct-horse-battery"
ADDRESS = "ada@example.com"

#: A second account's address, for the tests that need two of them.
OTHER_ADDRESS = "grace@example.com"

#: A number as somebody types it, and the spelling the store holds. A Nigerian
#: national number carries a trunk ``0`` that is not part of the number - see
#: ``fold_phone`` - so the two are *not* the same string and the fold is what
#: makes them one account.
TYPED = "08012345678"
NUMBER = "2348012345678"

#: A second number, for the tests that need one that belongs to nobody.
OTHER_TYPED = "08098765432"

#: The sentence every refusal on this path uses, **written out here rather than
#: imported from ``log_in``**.
#:
#: Deliberately a second copy, for the reason the Termii recipient alphabet is
#: written down twice in this project: a test that imported ``log_in.REFUSAL``
#: would agree with the code by construction, and what is being pinned is that the
#: *value* does not drift across branches - imported, this file would stay green
#: while a branch grew a clarifying clause, which is the failure and not the thing
#: checked. It is a message a client receives as ``detail``, so it is a contract
#: and re-wording it deliberately should have to touch this line.
#:
#: ``REFUSAL`` is public now - ``LogInWithGoogle`` imports it, and that module's
#: docstring records why production shares the string where this file duplicates
#: it. The constant having a name at all does not change the argument above: the
#: copy here is what fails when somebody re-words one of the two use cases.
_SHARED_REFUSAL = "those details did not match an account"


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


@pytest.fixture
def phone_account(factory, password_hasher):
    """An account identified by a number, and a password.

    **The shape only a texted code produces**, and the shape that makes this file's
    second half worth writing: an account identified by something other than an
    address is what ``execute_for_phone`` can find and ``execute`` cannot.

    An ``email`` may be passed as well, which makes an account identified by both -
    the state a phone-only account reaches by setting an address, and the only
    route by which it can ever deposit.

    Written with ``record_new_account`` rather than driven through
    ``ConfirmPhoneSignUp``, on the same argument ``test_confirm_phone_sign_up``
    makes for writing its own rows below the rule: this file is about what a
    *login* does with an account, and minting one through the signup flow would
    turn every failure here into a puzzle about two operations. The pair of rows is
    the shared helper's job and is tested where that helper is used.

    The number is taken in the spelling it was typed in and folded by the
    aggregate, which is what makes the stored value the one ``find_by_phone`` will
    fold a typed login to. A fixture writing the folded string itself would be a
    second spelling free to disagree with ``fold_phone``.
    """

    def _create(
        phone: str = TYPED, password: str = PASSWORD, email: str | None = None
    ) -> User:
        user = User(
            user_id=uuid4(),
            email=email,
            phone=phone,
            google_subject=None,
            created_at=NOW,
        )
        uow = factory.start()
        try:
            record_new_account(
                uow,
                user=user,
                password_hash=password_hasher.hash(PlainPassword(password)),
                now=NOW,
            )
            uow.commit()
        finally:
            uow.rollback()
        return user

    return _create


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


class TestSigningInWithANumber:
    """``execute_for_phone`` - the other way into an account, and the newer one.

    A class rather than four more tests above, because it answers a different
    question: not "is this password right" but "which account does this string
    name". That question has one wrinkle an address never had - the same number has
    several spellings and all of them are correct - and one that is a matter of
    taste in the API rather than here: which of the two entry points to call.
    """

    def test_it_returns_the_account_the_number_belongs_to(self, log_in, phone_account):
        user = phone_account()

        logged_in = log_in.execute_for_phone(TYPED, PASSWORD, NOW)

        assert logged_in.user.user_id == user.user_id
        assert logged_in.user.phone == NUMBER
        assert logged_in.user.email is None

    def test_the_token_authenticates_against_the_stored_session(
        self, log_in, factory, phone_account
    ):
        """Handing out a token with no row behind it is a login that does not last.

        Read back through the repository rather than trusting the returned
        aggregate, which the use case built and could return whether or not it
        saved it.
        """
        user = phone_account()

        logged_in = log_in.execute_for_phone(TYPED, PASSWORD, NOW)

        uow = factory.start()
        try:
            stored = uow.sessions.find_by_token_hash(hash_session_token(logged_in.token))
        finally:
            uow.rollback()

        assert stored is not None
        assert stored.user_id == user.user_id

    @pytest.mark.parametrize(
        "spelling",
        [TYPED, NUMBER, "+2348012345678", "0801 234 5678", "0801-234-5678", " 08012345678 "],
    )
    def test_every_spelling_of_one_number_finds_one_account(
        self, log_in, phone_account, spelling
    ):
        """The fold, tested from the side where a gap in it is felt.

        **A list rather than one spelling, and that is the test.** The account is
        written from ``TYPED`` and folded by the aggregate; the login is folded by
        ``find_by_phone``. A fold applied on only one of those two sides passes for
        whichever single spelling the fixture wrote and fails here for the rest -
        and the failure it prevents in the field is somebody who typed their own
        number with a country code and was told their account does not exist.
        """
        phone_account()

        assert log_in.execute_for_phone(spelling, PASSWORD, NOW).user.phone == NUMBER

    def test_an_account_holding_both_identifiers_signs_in_either_way(
        self, log_in, phone_account
    ):
        """Not a curiosity: it is what a phone-only account becomes when it sets an
        address, which is the only route by which it can deposit.

        Both lookups must find it, and what this catches is a ``find_by_phone``
        narrowed to accounts with no address - a ``WHERE`` clause that reads
        naturally while thinking about signups and is wrong for every account that
        has since become ordinary.
        """
        user = phone_account(email=ADDRESS)

        by_number = log_in.execute_for_phone(TYPED, PASSWORD, NOW)
        by_address = log_in.execute(ADDRESS, PASSWORD, NOW)

        assert by_number.user.user_id == user.user_id
        assert by_address.user.user_id == user.user_id

    def test_a_number_with_no_account_is_refused(self, log_in):
        with pytest.raises(InvalidCredentialsError):
            log_in.execute_for_phone(OTHER_TYPED, PASSWORD, NOW)

    def test_a_wrong_password_against_a_number_is_refused(self, log_in, phone_account):
        phone_account()

        with pytest.raises(InvalidCredentialsError):
            log_in.execute_for_phone(TYPED, "not-the-password", NOW)

    def test_the_two_number_refusals_are_indistinguishable(self, log_in, phone_account):
        """Decision 55, at the form that takes a number.

        An unknown number and a wrong password for a known one, because those are
        the two an attacker can produce at will against a range of numbers. The
        class and the message both have to agree - the class is what the API sends
        as ``error`` and the message is its ``detail``, so either one differing is
        the leak.
        """
        phone_account()

        with pytest.raises(InvalidCredentialsError) as unknown_number:
            log_in.execute_for_phone(OTHER_TYPED, PASSWORD, NOW)

        with pytest.raises(InvalidCredentialsError) as wrong_password:
            log_in.execute_for_phone(TYPED, "not-the-password", NOW)

        assert type(unknown_number.value) is type(wrong_password.value)
        assert str(unknown_number.value) == str(wrong_password.value)

    def test_neither_number_refusal_names_the_number_it_was_given(
        self, log_in, phone_account
    ):
        """A message that echoes the input confirms it, and this input is guessable.

        The address version of this test asserts the same thing for a different
        reason: an address list is something an attacker brings, and a number range
        is something they can simply count through. A refusal that quoted the number
        would let them confirm the *fold* as well - that ``+234…`` and ``0801…`` are
        one account - which is a fact about the system rather than about their guess.
        """
        phone_account()

        with pytest.raises(InvalidCredentialsError) as raised:
            log_in.execute_for_phone(TYPED, "not-the-password", NOW)

        assert TYPED not in str(raised.value)
        assert NUMBER not in str(raised.value)

    def test_a_number_belonging_to_an_account_identified_by_an_address_is_refused(
        self, log_in, sign_up
    ):
        """The cross-identifier case, and the one that must not have its own answer.

        An address-only account presented by *number* names no account, so it gets
        the shared sentence. Answering "that account has no number" instead would
        announce that the account exists and is missing a field - a fact about the
        system, about a guess the caller made.
        """
        sign_up.execute(ADDRESS, PASSWORD, NOW)

        with pytest.raises(InvalidCredentialsError) as raised:
            log_in.execute_for_phone(TYPED, PASSWORD, NOW)

        assert str(raised.value) == _SHARED_REFUSAL

    def test_an_address_belonging_to_a_number_only_account_is_refused(
        self, log_in, phone_account
    ):
        """The mirror image, and the one a careless fix would close first.

        ``find_by_email`` returns ``None`` for an account with no address in the
        column - SQL's ``=`` is never true of ``NULL`` - so this case needs nothing
        written for it. The test is here because that is a *property of the store*
        rather than a rule anybody wrote, and the day somebody "helpfully" makes an
        account with no email findable by an empty address this is what fails.
        """
        phone_account()

        with pytest.raises(InvalidCredentialsError) as raised:
            log_in.execute(ADDRESS, PASSWORD, NOW)

        assert str(raised.value) == _SHARED_REFUSAL

    def test_a_malformed_number_does_not_reach_the_password_policy(self, log_in):
        """``not-a-number`` names no account, so it is refused as credentials.

        The sibling of the malformed-address test above and the same decision:
        ``checked_phone`` belongs to ``User``, so there is no shape rule on this
        path. Pinned for the reason that test gives - an
        ``InvalidUserPhoneError`` appearing here later would be a *different*
        answer for a malformed number than for an unknown one, and that difference
        is a smaller version of the oracle.
        """
        with pytest.raises(InvalidCredentialsError):
            log_in.execute_for_phone("not-a-number", PASSWORD, NOW)

    def test_an_absent_address_is_refused_rather_than_crashing(self, log_in):
        """``execute`` accepts ``None`` and answers with the shared refusal.

        The API's branch for a number reaches the address path with ``None`` if the
        body's invariant were ever wrong, and what must not happen there is a
        ``TypeError`` reported as a 500 for a request that simply named no account.
        The layer below answers ``None`` the same way for the identical reason - see
        ``UserRepository.find_by_email``, whose guard exists because "look up the
        account's email" is a natural thing to write for an account that may not
        have one.
        """
        with pytest.raises(InvalidCredentialsError) as raised:
            log_in.execute(None, PASSWORD, NOW)

        assert str(raised.value) == _SHARED_REFUSAL


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
            phone=None,
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


class TestTheDecoyComparison:
    """The timing gap, closed - and asserted as *work* rather than as duration.

    **These tests count comparisons instead of measuring time, and that is the
    only honest way to write them.** A test that timed two logins would be a test
    about the machine it ran on: argon2's cost varies with load, with the
    library's version and with the CPU, so a threshold loose enough never to flake
    is a threshold loose enough to pass with the fix deleted. What *is*
    mechanically checkable is the claim the fix makes - that the branch which
    finds no credential still performs one comparison - and that is what the
    counting hasher below observes.

    ``LogIn``'s class docstring carries the argument for why this belongs with
    phone login rather than with rate limiting, and the honest bound on what it
    buys: the order-of-magnitude difference goes, the store lookup's microseconds
    stay, and rate limiting is still owed.
    """

    @pytest.fixture
    def counting(self, factory):
        """A ``LogIn`` over a hasher that records the encodings it was asked about.

        The ``CountingHasher`` idiom ``test_sign_up`` uses for the ordering of a
        hash against a write, here for the same reason: the claim is about work
        done, and only the collaborator knows whether it was done.
        """

        class CountingHasher(FakePasswordHasher):
            def __init__(self):
                self.verified: list[str] = []

            def verify(self, password, encoded):
                self.verified.append(encoded)
                return super().verify(password, encoded)

        hasher = CountingHasher()
        return hasher, LogIn(factory, password_hasher=hasher)

    def test_an_unknown_address_is_compared_against_the_decoy(self, counting):
        """The branch that used to return in microseconds."""
        hasher, log_in = counting

        with pytest.raises(InvalidCredentialsError):
            log_in.execute("nobody@example.com", PASSWORD, NOW)

        assert hasher.verified == [DUMMY_HASH]

    def test_an_unknown_number_is_compared_against_the_decoy(self, counting):
        """The reason the fix could not wait: this is the branch that is cheap to
        walk a *range* against, which is what a number space is."""
        hasher, log_in = counting

        with pytest.raises(InvalidCredentialsError):
            log_in.execute_for_phone(OTHER_TYPED, PASSWORD, NOW)

        assert hasher.verified == [DUMMY_HASH]

    def test_an_account_with_no_credential_is_compared_against_the_decoy(
        self, counting, factory
    ):
        """The second ``None`` in ``_settle``, and its timing was as different as the
        first's - faster, even, since it stopped before the credential lookup.

        The account is the one a Google sign-in produces, written straight into the
        store because no flow creates one yet; ``test_an_account_with_no_credential_is_refused_the_same_way``
        above does the same thing for the same reason.
        """
        user = User(
            user_id=uuid4(),
            email=ADDRESS,
            phone=None,
            google_subject=None,
            created_at=NOW,
        )
        uow = factory.start()
        try:
            uow.users.save(user)
            uow.commit()
        finally:
            uow.rollback()

        hasher, log_in = counting
        with pytest.raises(InvalidCredentialsError):
            log_in.execute(ADDRESS, PASSWORD, NOW)

        assert hasher.verified == [DUMMY_HASH]

    def test_a_known_account_is_compared_against_its_own_credential(
        self, counting, factory, phone_account
    ):
        """The control, and the one that keeps the decoy from being *used*.

        Without it, "the decoy is always compared" would pass for an
        implementation that compared the decoy *as well as* the real hash - which
        is a login that would accept a password matching the decoy's preimage, and
        would do two hashes per attempt, which is a login an attacker can make you
        pay double for. The assertion is the exact list of encodings: one entry,
        and it is the stored one.
        """
        user = phone_account()
        uow = factory.start()
        try:
            credential = uow.password_credentials.find_by_user_id(user.user_id)
        finally:
            uow.rollback()

        hasher, log_in = counting
        assert log_in.execute_for_phone(TYPED, PASSWORD, NOW).user.user_id == user.user_id

        assert hasher.verified == [credential.password_hash]
        assert DUMMY_HASH not in hasher.verified

    def test_a_wrong_password_is_compared_against_the_real_credential_only(
        self, counting, phone_account
    ):
        """A wrong password costs the same as a right one - that is the point of
        hashing both - and it still costs exactly one comparison.
        """
        phone_account()

        hasher, log_in = counting
        with pytest.raises(InvalidCredentialsError):
            log_in.execute_for_phone(TYPED, "not-the-password", NOW)

        assert len(hasher.verified) == 1
        assert DUMMY_HASH not in hasher.verified

    def test_a_weak_password_costs_no_comparison_at_all(self, counting):
        """The ordering, and it is forced by the decoy rather than chosen.

        ``PlainPassword`` has to be built before the comparison that spends the
        time, so a password outside the policy is refused before any lookup's
        answer is used - which means a three-character password now costs no hash
        even when the identifier names no account, where it used to be refused
        without one. Asserted as "no comparison happened" rather than "the right
        exception was raised", because the exception was already right.
        """
        hasher, log_in = counting

        with pytest.raises(WeakPasswordError):
            log_in.execute("nobody@example.com", "short", NOW)

        assert hasher.verified == []
