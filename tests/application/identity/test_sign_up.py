"""Registering an account.

The use case's job is narrow and worth testing narrowly: it decides whether an
address is free, it writes two rows that must land together, and it returns
something that is explicitly *not* a session. Every one of those is a claim the
phase makes, and each has a test below that would fail if it stopped being true.
"""

from datetime import datetime

import pytest

from app.application.identity.sign_up import SignUp
from app.domain.identity.exception import (
    DuplicateEmailError,
    InvalidPasswordError,
    InvalidUserEmailError,
    UnusableEmailError,
    WeakPasswordError,
)
from app.domain.identity.password import PlainPassword
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)
from tests.conftest import FakePasswordHasher

NOW = datetime(2026, 3, 2, 12, 0)
PASSWORD = "correct-horse-battery"


@pytest.fixture
def factory(tmp_path):
    return SqliteUnitOfWorkFactory(str(tmp_path / "identity.db"))


@pytest.fixture
def sign_up(factory, password_hasher):
    return SignUp(factory, password_hasher=password_hasher)


def test_it_creates_the_account(factory, sign_up):
    user = sign_up.execute("ada@example.com", PASSWORD, NOW)

    uow = factory.start()
    try:
        stored = uow.users.find_by_email("ada@example.com")
    finally:
        uow.rollback()

    assert stored is not None
    assert stored.user_id == user.user_id
    assert stored.email == "ada@example.com"
    assert stored.created_at == NOW


def test_it_stores_a_credential_with_the_password(factory, sign_up):
    """The second row, and the one that makes the account usable at all.

    Without it the sign-up would report success, the address would be taken, and
    nobody - including the person who just made it - could ever log in. That is
    the state ``SignUp``'s docstring calls unreachable, and this is the test that
    says the row is really written.
    """
    user = sign_up.execute("ada@example.com", PASSWORD, NOW)

    uow = factory.start()
    try:
        credential = uow.password_credentials.find_by_user_id(user.user_id)
    finally:
        uow.rollback()

    assert credential is not None
    assert credential.updated_at == NOW
    assert credential.password_hash != PASSWORD
    assert PASSWORD not in credential.password_hash


def test_the_stored_hash_is_one_the_hasher_can_verify(factory, sign_up, password_hasher):
    """Round trip, through the same port that wrote it.

    A test asserting only "the hash is not the password" would pass for a hash of
    the empty string. This is what says the credential is actually usable, which
    is the property the whole login path rests on.
    """
    user = sign_up.execute("ada@example.com", PASSWORD, NOW)

    uow = factory.start()
    try:
        credential = uow.password_credentials.find_by_user_id(user.user_id)
    finally:
        uow.rollback()

    assert password_hasher.verify(PlainPassword(PASSWORD), credential.password_hash)


def test_it_does_not_sign_anybody_in(factory, sign_up):
    """Registering and proving are separate acts, and this is the separation.

    The password is in hand and a session could be minted immediately, which is
    exactly why the absence is worth a test: a registration that silently
    authenticated would mean the first session on a machine came from a command
    that never checked a password. Nothing here may create a session row.
    """
    user = sign_up.execute("ada@example.com", PASSWORD, NOW)

    uow = factory.start()
    try:
        assert uow.sessions.find_by_token_hash("anything") is None
    finally:
        uow.rollback()

    # Returns the identity and nothing else - no token, no session, no pair.
    assert not hasattr(user, "token")


def test_it_returns_the_identity_with_no_credential_on_it(factory, sign_up):
    """Decision 53, at the boundary where it would be easiest to break.

    The use case holds the hash at the moment it returns. Attaching it to the
    returned ``User`` would be the tempting way to "save a read" - and it would
    put a value that must never be printed onto the object every authenticated
    request loads, hands to services and renders through ``translate.user_out``.
    """
    user = sign_up.execute("ada@example.com", PASSWORD, NOW)

    assert not hasattr(user, "password_hash")


def test_a_second_account_at_the_same_address_is_refused(sign_up):
    sign_up.execute("ada@example.com", PASSWORD, NOW)

    with pytest.raises(DuplicateEmailError):
        sign_up.execute("ada@example.com", "a-different-password", NOW)


def test_the_duplicate_check_is_not_fooled_by_a_different_spelling(sign_up):
    """The fold, at the point where a second account would otherwise be created.

    ``Chinedu@Example.com`` and ``chinedu@example.COM`` are one address, and
    ``find_by_email`` compares it folded. Without that, this would create a second
    account - and the ``UNIQUE`` constraint on the column would then refuse the
    write, so the failure would be a raw ``IntegrityError`` rather than the
    ``DuplicateEmailError`` the caller is expecting.
    """
    sign_up.execute("chinedu@example.com", PASSWORD, NOW)

    with pytest.raises(DuplicateEmailError):
        sign_up.execute("  Chinedu@Example.COM  ", PASSWORD, NOW)


def test_the_refusal_confirms_the_account_exists_and_that_is_unavoidable(sign_up):
    """``DuplicateEmailError`` is the one refusal on this side that does confirm it.

    ``LogIn`` deliberately does not behave this way, and the difference is worth
    stating rather than leaving as an inconsistency: the alternative at sign-up is
    letting two people register one address and discover it at the login form. The
    class name is what a client sees, so this is the API's behaviour too.
    """
    sign_up.execute("ada@example.com", PASSWORD, NOW)

    with pytest.raises(DuplicateEmailError) as raised:
        sign_up.execute("ada@example.com", PASSWORD, NOW)

    assert "ada@example.com" in str(raised.value)


def test_two_accounts_are_two_accounts(factory, sign_up):
    """The control. Without it, everything above passes for a use case that stores nothing."""
    first = sign_up.execute("ada@example.com", PASSWORD, NOW)
    second = sign_up.execute("grace@example.com", PASSWORD, NOW)

    uow = factory.start()
    try:
        assert uow.users.find_by_email("ada@example.com").user_id == first.user_id
        assert uow.users.find_by_email("grace@example.com").user_id == second.user_id
    finally:
        uow.rollback()


def test_a_weak_password_is_refused_and_nothing_is_written(factory, sign_up):
    """The policy is ``PlainPassword``'s, and the rollback is what keeps it clean.

    A refused sign-up must leave no account behind. If it did, the address would
    be taken by an account nobody can log into - which is the invisible failure
    ``SignUp`` writes two rows in one unit to avoid, arriving by a different door.
    """
    with pytest.raises(WeakPasswordError):
        sign_up.execute("ada@example.com", "short", NOW)

    uow = factory.start()
    try:
        assert uow.users.find_by_email("ada@example.com") is None
    finally:
        uow.rollback()


def test_a_malformed_address_is_refused_as_a_shape_problem(sign_up):
    """**The ordering assertion.** Not an address at all, and it must be *told* that.

    ``not-an-address`` fails both rules at once - it has no ``@``, so the shape rule
    refuses it, and it has no dot after an ``@`` that is not there, so the usability
    rule would too. Which one answers is therefore decided by the order the two are
    asked in, and the order is load-bearing rather than tidy: this answer names the
    thing the person can act on ("that is not an address"), where
    ``UnusableEmailError`` names a domain rule they did not break. The two errors
    send a reader to different files.

    This used to say the rule lived on ``User`` and not in this method. That is no
    longer true and the change is deliberate: ``SignUp`` now calls ``checked_email``
    itself, the same function ``User.__post_init__`` calls, so there is still exactly
    one definition of the shape rule - it is simply applied earlier, which is what
    makes room for the second rule below it.
    """
    with pytest.raises(InvalidUserEmailError) as raised:
        sign_up.execute("not-an-address", PASSWORD, NOW)

    assert not isinstance(raised.value, UnusableEmailError)


def test_an_unusable_address_is_refused_at_the_door(factory, sign_up):
    """The rule this whole slice was built for, at the one place a fresh address enters.

    ``nobody@localhost`` is a perfectly well-formed address that no payment provider
    will bill, and before this rule the system would create an account holding one -
    which meant the account's *first deposit* was refused, weeks later, by a third
    party, with a message about a parameter. The person had no way to fix it: the
    address was taken forever by the ``UNIQUE`` on ``users.email``, and the only
    remedy was a second account.
    """
    with pytest.raises(UnusableEmailError):
        sign_up.execute("nobody@localhost", PASSWORD, NOW)


def test_a_refused_unusable_address_leaves_no_account_behind(factory, sign_up):
    """The half that makes the rule a rescue rather than a different trap.

    If the row landed anyway, the address would be taken by an account nobody could
    ever deposit from *and* nobody could register again - which is the exact state
    this refuses to create. ``test_a_weak_password_is_refused_and_nothing_is_written``
    makes the same assertion for the other refusal, from the same cause: the whole
    operation is one unit, and nothing commits until the last line.
    """
    with pytest.raises(UnusableEmailError):
        sign_up.execute("nobody@localhost", PASSWORD, NOW)

    uow = factory.start()
    try:
        assert uow.users.find_by_email("nobody@localhost") is None
    finally:
        uow.rollback()


def test_the_unusable_refusal_beats_the_duplicate_answer(sign_up):
    """Usability is asked *before* the store is consulted, and both reasons agree.

    The first is what the person can act on: an address that is taken **and**
    unusable reported as taken invites somebody to log in to an account no provider
    will bill - true, and useless. The second is a disclosure argument, and it is the
    sharper one: usability is a property of the string that was typed, while the
    duplicate answer is a property of the store. Asking the local question first
    means an unauthenticated caller learns nothing about which addresses are
    registered until they have named one that could be.

    Seeded with a *usable* address first so the store genuinely holds something - a
    test that never wrote a row would pass whether the order was right or not.
    """
    sign_up.execute("ada@example.com", PASSWORD, NOW)

    with pytest.raises(UnusableEmailError):
        sign_up.execute("nobody@localhost", PASSWORD, NOW)



def test_the_password_is_validated_before_the_hash_is_computed(tmp_path):
    """Ordering, and the reason is cost rather than correctness.

    A hash is the expensive part of this method - argon2 by design - and there is
    no reason to pay for one in order to discover the password was three characters
    long. ``test_a_weak_password_is_refused_and_nothing_is_written`` above already
    covers the storage half; this one is solely about the work not being done.

    Observed through a hasher that counts its calls, so the claim is about what
    happened rather than about the order the lines appear in.

    **A real factory, and the first version of this test got that wrong.** It used a
    stub whose ``start`` raised, on the theory that the refusal happens *before any
    storage is touched at all* - and it failed, because ``execute`` opens its unit
    of work first and then validates inside it. That is the right shape: the whole
    operation is one transaction, so it opens one, and a connection is cheap where a
    hash is not. What is being saved here is the hash, not the connection, and
    reaching for the stronger claim produced a test that contradicted the design
    rather than one that checked it.
    """
    class CountingHasher(FakePasswordHasher):
        def __init__(self):
            self.calls = 0

        def hash(self, password):
            self.calls += 1
            return super().hash(password)

    counting = CountingHasher()
    service = SignUp(
        SqliteUnitOfWorkFactory(str(tmp_path / "identity.db")), password_hasher=counting
    )

    with pytest.raises(WeakPasswordError):
        service.execute("ada@example.com", "short", NOW)

    assert counting.calls == 0


def test_an_empty_password_is_refused_as_a_boundary_bug(sign_up):
    """``""`` is a caller that passed nothing, and it is not "too short".

    The distinction is ``PlainPassword``'s and this test exists so that a caller
    can rely on it: "you sent no password at all" and "that password is too weak"
    have different fixes, and collapsing them would make a programming error look
    like a user's mistake.
    """
    with pytest.raises(InvalidPasswordError) as raised:
        sign_up.execute("ada@example.com", "", NOW)

    assert not isinstance(raised.value, WeakPasswordError)
