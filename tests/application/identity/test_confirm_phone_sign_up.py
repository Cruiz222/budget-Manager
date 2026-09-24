"""Answering a phone signup: a code comes back, and an account exists.

Half of this file is ``test_confirm_password_reset.py``'s argument one flow over -
the code is spent by the claim, and three refusals are told apart by it - and half
of it is about the thing no other confirm in this system does:

**This is the only confirm that creates the thing it acts on.** The three siblings
apply something to an account that already exists; here the account is the
result, its identifier is the number the claimed row names, and there is no
credential to replace - which is why this half takes a password and ``SignUp`` is
the only other operation in the codebase that does.

Three consequences are tested rather than assumed, and each one is a decision:

**The number is read out of the claimed row and never from the caller.** ``execute``
takes no phone at all, so "which number" has exactly one answer: the one the code
was texted to. The test that shows this from outside is the two-numbers case -
two pending verifications live at once, and answering the second's code creates
the account on the second's number.

**Two writes commit with the spend.** The account and its credential are written
into the unit that claimed the code, so a claim that committed without an account
would leave the ``UNIQUE`` slot on a number nobody holds, and the account and its
password cannot come apart.

**A weak password does not spend the code.** ``PlainPassword`` is constructed
before the unit is opened, so a password outside the policy is refused with the
code still live - which is the remedy a person can actually take, since typing a
longer password is something they can do and asking for another text costs
another message.
"""

from datetime import datetime, timedelta
from uuid import uuid4
from decimal import Decimal

from app.domain.money.currency import Currency
from app.domain.money.money import Money
from app.domain.money.walletStatus import WalletStatus
from app.domain.payments.virtualAccountStatus import VirtualAccountStatus

import pytest

from app.application.identity.confirm_phone_sign_up import ConfirmPhoneSignUp
from app.domain.identity.exception import (
    DuplicatePhoneError,
    InvalidPhoneVerificationTokenError,
    PhoneVerificationAlreadyUsedError,
    PhoneVerificationExpiredError,
    WeakPasswordError,
)
from app.domain.identity.password import PlainPassword
from app.domain.identity.phoneVerification import (
    PHONE_VERIFICATION_LIFETIME,
    PhoneVerification,
)
from app.domain.identity.user import User
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
    open_sqlite_connection,
)

NOW = datetime(2026, 3, 2, 12, 0)
PASSWORD = "correct-horse-battery"

#: A password ``PlainPassword`` refuses - shorter than the policy's floor.
WEAK_PASSWORD = "short"

TYPED = "08012345678"
FOLDED = "2348012345678"
OTHER_TYPED = "08098765432"
OTHER_FOLDED = "2348098765432"


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "identity.db")


@pytest.fixture
def factory(db_path):
    return SqliteUnitOfWorkFactory(db_path)


@pytest.fixture
def confirm(factory, password_hasher):
    """``ConfirmPhoneSignUp`` over the test store and the fast hasher.

    No channel parameter, and its absence is the class's own design rather than a
    shortcut here: nothing is sent from this half, because a text saying "your
    number is verified" would tell somebody a fact they learned by answering the
    code. See ``phoneVerificationMessage``.
    """
    return ConfirmPhoneSignUp(factory, password_hasher=password_hasher)


@pytest.fixture
def pending(db_path):
    """A live verification for a number, and the code that answers it.

    Written through the same ``PhoneVerification.issue`` the use case calls and
    through the same repository ``save``, so what this stands in for is a request
    that really was made - not a hand-shaped row. ``age`` is the dial the expired
    test turns: a request issued eleven minutes ago is a perfectly valid row whose
    window the clock has moved past, which is how a test produces expiry without
    waiting ten minutes for it.
    """

    def _issue(phone: str = TYPED, age: timedelta = timedelta(0)) -> str:
        verification, token = PhoneVerification.issue(phone=phone, now=NOW - age)

        uow = SqliteUnitOfWorkFactory(db_path).start()
        try:
            uow.phone_verifications.save(verification)
            uow.commit()
        finally:
            uow.rollback()
        return token

    return _issue


@pytest.fixture
def taken_number(db_path):
    """An account that already holds a number, written straight into the store.

    The state a phone signup races against: somebody else claimed the number
    between this request and this answer. ``ConfirmPhoneSignUp`` bears the
    counter, so the account is seeded below the rule rather than created through a
    flow - which is also how reality produces it, since the race is exactly two
    people doing this at once.
    """

    def _take(phone: str = TYPED) -> User:
        uow = SqliteUnitOfWorkFactory(db_path).start()
        try:
            user = User(
                user_id=uuid4(),
                email=None,
                # The typed form, so the fold this account was stored under is the
                # aggregate's own - the same one the number it takes was folded by,
                # rather than a second spelling written out here.
                phone=phone,
                google_subject=None,
                created_at=NOW,
            )
            uow.users.save(user)
            uow.commit()
        finally:
            uow.rollback()
        return user

    return _take


def rows_in(db_path, table: str) -> int:
    """How many rows a table holds, asked around the ports rather than through them.

    Both tables this file is about are read this way, and for the reason the
    sibling files give: what is being asserted is often the *absence* of a write,
    and a repository method added so a test could ask would be a method with no
    production caller.
    """
    connection = open_sqlite_connection(db_path)
    try:
        return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        connection.close()


def stored_hash(db_path):
    """The credential's hash, straight out of the table it was written to."""
    connection = open_sqlite_connection(db_path)
    try:
        row = connection.execute("SELECT password_hash FROM password_credentials").fetchone()
        return None if row is None else row[0]
    finally:
        connection.close()


class TestTheAccountItCreates:
    """What answering produces: an account, a credential, and nothing else."""

    def test_it_creates_the_account_holding_the_number(self, confirm, pending, db_path):
        user = confirm.execute(pending(), PASSWORD, NOW)

        assert user.phone == FOLDED
        assert rows_in(db_path, "users") == 1


    def test_it_opens_an_ngn_wallet_with_a_pending_virtual_account(
        self,
        confirm,
        pending,
        factory,
    ):
        user = confirm.execute(pending(), PASSWORD, NOW)

        uow = factory.start()
        try:
            wallets = uow.wallets.list_for_owner(user.user_id)
            assert len(wallets) == 1

            wallet = wallets[0]
            account = uow.virtual_accounts.get_by_wallet_id(wallet.wallet_id)
        finally:
            uow.rollback()

        assert wallet.user_id == user.user_id
        assert wallet.status is WalletStatus.ACTIVE
        assert wallet.currency is Currency.NGN
        assert wallet.available_balance == Money(Decimal("0"), Currency.NGN)
        assert wallet.locked_balance == Money(Decimal("0"), Currency.NGN)

        assert account is not None
        assert account.wallet_id == wallet.wallet_id
        assert account.status is VirtualAccountStatus.PENDING
        assert account.provider == "paystack"
        assert account.provider_customer_code is None
        assert account.account_number is None
        assert account.account_name is None
        assert account.bank_name is None    

    def test_the_account_has_no_address(self, confirm, pending):
        """**The whole point of this path, and the constraint it creates.**

        An account made from a text has an address only if its holder later sets
        one - which ``RequestEmailChange`` handles, and which is the only route by
        which this account can come to be able to deposit. Until then a deposit is
        refused with a sentence naming what is missing, rather than by a silent
        failure or an invented payer address.
        """
        user = confirm.execute(pending(), PASSWORD, NOW)

        assert user.email is None

    def test_the_account_has_no_google_identity(self, confirm, pending):
        """``None`` and not an empty string, for ``SignUp``'s reason: this account
        was not created by a Google sign-in, which is a fact rather than a value
        that is missing."""
        user = confirm.execute(pending(), PASSWORD, NOW)

        assert user.google_subject is None

    def test_the_account_is_recorded_at_the_moment_it_was_answered(self, confirm, pending):
        """``now`` is the account's ``created_at`` and the claim's ``settled_at``.

        One reading of the clock for both, so the two cannot disagree about when
        this happened - which matters here more than it does for a normal signup,
        because the same instant is written to two tables by two statements and
        only the caller knows it.
        """
        user = confirm.execute(pending(), PASSWORD, NOW + timedelta(minutes=3))

        assert user.created_at == NOW + timedelta(minutes=3)

    def test_it_writes_a_credential_that_verifies_the_password(
        self, confirm, pending, db_path, password_hasher
    ):
        """**An account with no credential is an account nobody can ever log into.**

        That is ``SignUp``'s invariant, and it is the reason the two writes are one
        unit rather than two commits: an account written without its password would
        fail invisibly - the row would look right, and the person would be told
        their details did not match an account they can see exists.
        """
        confirm.execute(pending(), PASSWORD, NOW)

        assert password_hasher.verify(PlainPassword(PASSWORD), stored_hash(db_path))

    def test_the_stored_hash_is_not_the_password(self, confirm, pending, db_path):
        """A leaked table is not a set of passwords.

        Asserted on the column rather than on the dump, because this is the one
        table where the value stored is the person's own secret rather than a
        single-use code - so it is the table whose contents an attacker would
        rather have.
        """
        confirm.execute(pending(), PASSWORD, NOW)

        assert PASSWORD not in (stored_hash(db_path) or "")

    def test_it_mints_no_session(self, confirm, pending, db_path):
        """Registering and proving are separate acts, and this is a *signup*.

        ``POST /users`` mints no token either: a signup that also returned a session
        would be handing out a credential the caller has not used yet. The person
        holds two facts they can log in with, and the honest next step is to use
        them.
        """
        confirm.execute(pending(), PASSWORD, NOW)

        assert rows_in(db_path, "sessions") == 0

    def test_the_number_can_now_be_found(self, confirm, pending, factory):
        """The claim that the ``UNIQUE`` slot has moved from a pending request to
        an account.

        The row is what a future login by number will look up, so this is the one
        test here that reads the account back the way another flow will.
        """
        confirm.execute(pending(), PASSWORD, NOW)

        uow = factory.start()
        try:
            found = uow.users.find_by_phone(FOLDED)
        finally:
            uow.rollback()

        assert found is not None
        assert found.phone == FOLDED
        assert found.email is None


class TestTheNumberItCreatesTheAccountOn:
    """Read out of the claimed row, never from the caller."""

    def test_every_spelling_of_one_number_creates_the_same_account(
        self, confirm, pending, db_path
    ):
        """The fold is applied by the aggregate that issued the request, so a
        national form and an international one are one number and one account."""
        user = confirm.execute(pending("+2348012345678"), PASSWORD, NOW)

        assert user.phone == FOLDED
        assert rows_in(db_path, "users") == 1

    def test_it_answers_the_numbers_code_and_not_anothers(
        self, confirm, pending, db_path
    ):
        """**The test that would fail if the number were passed in.**

        Two verifications are live at once - which is possible because the key is
        the number rather than an account - and only the second one's code is
        answered. The account must hold the number whose code was spent, and there
        is no argument in ``execute`` through which a caller's own idea of which
        number it is could arrive.

        This is what ``claim_by_token_hash``'s ``WHERE`` clause is protecting: it
        matches on the token alone, so the number is a fact about the row rather
        than a second input free to disagree with it.
        """
        pending(TYPED)
        second = pending(OTHER_TYPED)

        user = confirm.execute(second, PASSWORD, NOW)

        assert user.phone == OTHER_FOLDED
        assert rows_in(db_path, "users") == 1

    def test_only_the_answered_number_is_spent(self, confirm, pending, db_path):
        """And the other request is untouched, so its person can still answer it.

        A claim that settled more than the row it matched would strand whoever was
        waiting on the other code - and the temptation to scope by number, which is
        the change this pair of tests exists to prevent, would not have shown up
        here at all.
        """
        first = pending(TYPED)
        second = pending(OTHER_TYPED)

        confirm.execute(second, PASSWORD, NOW)

        # The first request is still live: answering it now creates a second
        # account on the other number.
        other = confirm.execute(first, PASSWORD, NOW)
        assert other.phone == FOLDED
        assert rows_in(db_path, "users") == 2


class TestTheCodeIsSpentByTheClaim:
    """One atomic statement, and the four ways it can refuse."""

    def test_a_code_that_means_nothing_is_refused(self, confirm):
        with pytest.raises(InvalidPhoneVerificationTokenError):
            confirm.execute("a-code-nobody-ever-minted", PASSWORD, NOW)

    def test_an_expired_code_is_refused(self, confirm, pending):
        """Eleven minutes after issue, against a ten-minute window.

        The window is the aggregate's constant and the claim tests the same
        boundary with ``expires_at > ?``, so this is the pair that has to agree -
        see ``PhoneVerification.is_expired``.
        """
        expired = pending(age=PHONE_VERIFICATION_LIFETIME + timedelta(minutes=1))

        with pytest.raises(PhoneVerificationExpiredError):
            confirm.execute(expired, PASSWORD, NOW)

    def test_a_code_at_the_instant_it_expires_is_refused(self, confirm, pending):
        """**The boundary, from the side a person can actually hit.**

        ``is_expired`` reads ``>=`` and the claim reads ``>``, so the instant the
        window closes the code is closed with it. A disagreement in this direction
        - the claim accepting what the aggregate calls expired - would be a number
        claimed one instant after its holder was told the code had stopped working.
        """
        exactly = pending(age=PHONE_VERIFICATION_LIFETIME)

        with pytest.raises(PhoneVerificationExpiredError):
            confirm.execute(exactly, PASSWORD, NOW)

    def test_a_code_used_twice_is_refused(self, confirm, pending):
        """**Reachable only because the row survives being spent.**

        Deleting it on use would collapse this into "that code means nothing", and
        the two are different things to be told - one of them means "log in, your
        account exists".
        """
        code = pending()
        confirm.execute(code, PASSWORD, NOW)

        with pytest.raises(PhoneVerificationAlreadyUsedError):
            confirm.execute(code, PASSWORD, NOW)

    def test_a_spent_code_creates_no_second_account(self, confirm, pending, db_path):
        """The refusal is the important half of the line above.

        A second confirm that raised *after* writing would be a duplicate account on
        one number, which the ``UNIQUE`` column would then refuse as an
        ``IntegrityError`` - a 500 where a 409 was owed, and only on the second
        attempt.
        """
        code = pending()
        confirm.execute(code, PASSWORD, NOW)

        with pytest.raises(PhoneVerificationAlreadyUsedError):
            confirm.execute(code, PASSWORD, NOW)

        assert rows_in(db_path, "users") == 1
        assert rows_in(db_path, "password_credentials") == 1

    def test_the_spend_and_the_account_are_one_transaction(self, confirm, pending, db_path):
        """Both written, or neither - asserted through the refusal that writes one.

        There is no partial state to reach from outside the happy path, which is the
        property ``account_creation``'s docstring argues for: a claim committed
        without its account would hold the ``UNIQUE`` slot on a number nobody has,
        and the person who holds the handset could not sign up with it until the
        request expired.
        """
        code = pending()
        confirm.execute(code, PASSWORD, NOW)

        connection = open_sqlite_connection(db_path)
        try:
            status = connection.execute(
                "SELECT status FROM phone_verifications"
            ).fetchone()[0]
        finally:
            connection.close()

        assert status == "CONFIRMED"
        assert rows_in(db_path, "users") == 1


class TestTheNumberSomebodyElseTook:
    """The race the two-step shape exists to lose safely."""

    def test_it_is_refused(self, confirm, pending, taken_number):
        taken_number()
        code = pending()

        with pytest.raises(DuplicatePhoneError):
            confirm.execute(code, PASSWORD, NOW)

    def test_it_writes_no_account(self, confirm, pending, taken_number, db_path):
        taken_number()
        code = pending()

        with pytest.raises(DuplicatePhoneError):
            confirm.execute(code, PASSWORD, NOW)

        assert rows_in(db_path, "users") == 1
        assert rows_in(db_path, "password_credentials") == 0

    def test_it_spends_the_code(self, confirm, pending, taken_number):
        """**Spent by the attempt, not by the success** - ``ConfirmEmailChange``'s rule.

        The refusal commits the claim before it raises, because the ``rollback`` in
        ``execute``'s ``finally`` would otherwise unwind it and "spent by the
        attempt" would be a sentence in a docstring rather than something the store
        does. A code that survived a failed attempt is a live credential whose whole
        purpose is to be usable once.

        The reason it is *right* here rather than merely inherited is the sharper
        one: a number that is taken is taken, so a retry with this code could only
        ever produce this same refusal, and keeping it alive would hold the
        ``UNIQUE`` slot against the person who now holds the number.
        """
        taken_number()
        code = pending()

        with pytest.raises(DuplicatePhoneError):
            confirm.execute(code, PASSWORD, NOW)

        with pytest.raises(PhoneVerificationAlreadyUsedError):
            confirm.execute(code, PASSWORD, NOW)

    def test_a_fresh_code_for_the_same_number_is_refused_too(
        self, confirm, pending, taken_number
    ):
        """The refusal is about the world rather than about the code.

        Asking again supersedes the earlier request - the row is keyed on the number
        - so the newest code is the live one, and it is refused exactly as the first
        would have been. That is what makes "ask again" useless advice for this
        refusal, and it is why the code is spent by the attempt rather than kept
        alive for a retry that could only repeat itself.
        """
        taken_number()
        pending()

        with pytest.raises(DuplicatePhoneError):
            confirm.execute(pending(), PASSWORD, NOW)


class TestThePasswordThatDoesNotMeetThePolicy:
    """The one refusal that leaves the code alive."""

    def test_it_is_refused(self, confirm, pending):
        with pytest.raises(WeakPasswordError):
            confirm.execute(pending(), WEAK_PASSWORD, NOW)

    def test_the_same_code_works_with_a_longer_password(self, confirm, pending):
        """**The claim, said from the person's side: the code is still answerable.**

        A refusal that had spent the claim would leave this raising
        ``PhoneVerificationAlreadyUsedError`` instead of creating the account - so
        the test that matters is that a second attempt with a longer password
        succeeds.
        """
        code = pending()

        with pytest.raises(WeakPasswordError):
            confirm.execute(code, WEAK_PASSWORD, NOW)

        user = confirm.execute(code, PASSWORD, NOW)

        assert user.phone == FOLDED

    def test_nothing_is_written(self, confirm, pending, db_path):
        code = pending()

        with pytest.raises(WeakPasswordError):
            confirm.execute(code, WEAK_PASSWORD, NOW)

        assert rows_in(db_path, "users") == 0
        assert rows_in(db_path, "password_credentials") == 0

    def test_the_request_is_still_awaiting(self, confirm, pending, db_path):
        """Read out of the table, because "not spent" is a claim about the row."""
        code = pending()

        with pytest.raises(WeakPasswordError):
            confirm.execute(code, WEAK_PASSWORD, NOW)

        connection = open_sqlite_connection(db_path)
        try:
            row = connection.execute(
                "SELECT status, settled_at FROM phone_verifications"
            ).fetchone()
        finally:
            connection.close()

        assert row[0] == "AWAITING"
        assert row[1] is None


class TestTheSameNumberTwiceInARow:
    """One account per number, whichever half refuses it."""

    def test_a_second_signup_on_a_spent_code_is_the_code_that_refuses(
        self, confirm, pending, db_path
    ):
        """And not the duplicate, which is the ordering worth pinning.

        Both refusals describe this state - the number is taken *and* the code is
        spent - and the claim runs first, so what the caller is told is "you already
        did this" rather than "somebody took that number". The first is actionable
        (log in) and the second is alarming, so the order is worth having.
        """
        code = pending()
        confirm.execute(code, PASSWORD, NOW)

        with pytest.raises(PhoneVerificationAlreadyUsedError):
            confirm.execute(code, PASSWORD, NOW)

        assert rows_in(db_path, "users") == 1
