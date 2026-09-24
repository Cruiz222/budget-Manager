"""Creating an account from a verified Google identity.

Three claims are worth testing narrowly here, and each has tests below that would
fail if it stopped being true: the account is written with a subject and **no
credential**, an address Google has not proved is refused before anything is
written, and a collision is refused rather than linked.

The suite uses ``FakeGoogleIdentityVerifier``, which decides validity by script
rather than by signature - so what these tests check is this use case's policy and
not the cryptography. The cryptography is
``tests/infrastructure/identity/test_pyjwt_google_identity_verifier.py``'s.
"""

from datetime import datetime

import pytest

from app.application.identity.log_in import LogIn
from app.application.identity.sign_up_with_google import SignUpWithGoogle
from app.domain.identity.exception import (
    DuplicateEmailError,
    DuplicateGoogleSubjectError,
    InvalidCredentialsError,
    InvalidGoogleTokenError,
    InvalidUserEmailError,
    UnusableEmailError,
    UnverifiedGoogleEmailError,
)
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)
from decimal import Decimal

from app.domain.money.currency import Currency
from app.domain.money.money import Money
from app.domain.money.walletStatus import WalletStatus
from app.domain.payments.virtualAccountStatus import VirtualAccountStatus

NOW = datetime(2026, 3, 2, 12, 0)
SUBJECT = "114988223156872419036"
OTHER_SUBJECT = "109876543210987654321"
ADDRESS = "chinedu@example.com"


@pytest.fixture
def factory(tmp_path):
    return SqliteUnitOfWorkFactory(str(tmp_path / "identity.db"))


@pytest.fixture
def sign_up_with_google(factory, build_google_verifier):
    def _build(**kwargs):
        verifier = build_google_verifier(**kwargs)
        return SignUpWithGoogle(factory, verifier=verifier), verifier

    return _build


def test_it_creates_the_account(factory, sign_up_with_google):
    service, verifier = sign_up_with_google()
    token = verifier.mint(subject=SUBJECT, email=ADDRESS)

    user = service.execute(token, NOW)

    uow = factory.start()
    try:
        stored = uow.users.find_by_google_subject(SUBJECT)
    finally:
        uow.rollback()

    assert stored is not None
    assert stored.user_id == user.user_id
    assert stored.email == ADDRESS
    assert stored.google_subject == SUBJECT
    assert stored.created_at == NOW


def test_it_opens_an_ngn_wallet_with_a_pending_virtual_account(
    factory,
    sign_up_with_google,
):
    service, verifier = sign_up_with_google()
    token = verifier.mint(
        subject=SUBJECT,
        email=ADDRESS,
    )

    user = service.execute(token, NOW)

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
    

def test_it_stores_no_credential(factory, sign_up_with_google):
    """**The row that is deliberately missing**, and it is the difference between
    this flow and every other way in.

    ``record_new_account`` writes a user and a credential together, and its
    original docstring said a user with no credential is an account nobody can
    ever log into. That is still true of the accounts *it* writes - and false of
    this one, which is the exception the docstring was rewritten to accommodate.
    Writing a credential here would mean inventing a hash for a password nobody
    chose, which is worse than the absence: the account would look
    password-protected to anything that counts credentials.

    Asserted against the store rather than inferred from the absence of an
    argument, because "we did not pass a hash" and "no row exists" are different
    claims and only the second one matters.
    """
    service, verifier = sign_up_with_google()
    token = verifier.mint(subject=SUBJECT, email=ADDRESS)

    user = service.execute(token, NOW)

    uow = factory.start()
    try:
        credential = uow.password_credentials.find_by_user_id(user.user_id)
    finally:
        uow.rollback()

    assert credential is None


def test_the_account_cannot_be_logged_into_with_a_password(
    factory, sign_up_with_google, password_hasher
):
    """The consequence of the row above, pinned rather than left implied.

    A Google account has no password, so every password is the wrong one - and
    ``LogIn`` already answers that with its shared refusal, because its own
    docstring already anticipated this account ("the account has none, which is
    what a Google sign-in produces"). No code was written to make this true; it is
    asserted so that a later change to ``LogIn`` - or a "helpful" credential row
    written by mistake - has to break a test rather than quietly hand somebody an
    account.
    """
    service, verifier = sign_up_with_google()
    service.execute(verifier.mint(subject=SUBJECT, email=ADDRESS), NOW)

    log_in = LogIn(factory, password_hasher=password_hasher)

    with pytest.raises(InvalidCredentialsError):
        log_in.execute(ADDRESS, "any-password-at-all", NOW)


class TestTheRefusals:
    def test_an_unverified_address_is_refused(self, factory, sign_up_with_google):
        service, verifier = sign_up_with_google()
        token = verifier.mint(
            subject=SUBJECT, email=ADDRESS, email_verified=False
        )

        with pytest.raises(UnverifiedGoogleEmailError):
            service.execute(token, NOW)

    def test_an_unverified_address_writes_nothing(self, factory, sign_up_with_google):
        """**The refusal has to be before the write, not after it.**

        The chain this closes runs through the reset flow: an account holding an
        address Google has not proved would have a working reset code mailed to
        whoever controls that address. So the refusal is not a message - it is the
        absence of a row, and it is the row that would be hijackable.
        """
        service, verifier = sign_up_with_google()
        token = verifier.mint(subject=SUBJECT, email=ADDRESS, email_verified=False)

        with pytest.raises(UnverifiedGoogleEmailError):
            service.execute(token, NOW)

        uow = factory.start()
        try:
            assert uow.users.find_by_google_subject(SUBJECT) is None
            assert uow.users.find_by_email(ADDRESS) is None
        finally:
            uow.rollback()

    def test_an_address_with_no_at_sign_is_refused_as_malformed(
        self, sign_up_with_google
    ):
        """Shape before usability, as ``SignUp`` puts it.

        ``not-an-address`` has no ``@`` *and* its domain has no dot, so both rules
        refuse it - and the one that reaches the person has to be the one they can
        act on. This asserts which.
        """
        service, verifier = sign_up_with_google()
        token = verifier.mint(subject=SUBJECT, email="not-an-address")

        with pytest.raises(InvalidUserEmailError):
            service.execute(token, NOW)

    def test_an_address_at_a_domain_with_no_dot_is_refused_as_unusable(
        self, sign_up_with_google
    ):
        """The minting rule, applied here because this flow mints an address.

        ``refuse_unusable_email`` is a policy about *creating* an address rather
        than about storing one - ``User`` deliberately does not hold it, or every
        account already stranded at such an address would be unreadable rather
        than rescuable. This flow is one of the sites that mints one, so it is one
        of the sites that applies it.
        """
        service, verifier = sign_up_with_google()
        token = verifier.mint(subject=SUBJECT, email="live@localhost")

        with pytest.raises(UnusableEmailError):
            service.execute(token, NOW)

    def test_an_address_that_is_already_registered_is_refused(
        self, factory, sign_up_with_google
    ):
        """**The 409 the user chose**, and the reuse of ``DuplicateEmailError``.

        That class already means "the address is already held by an account" and is
        already graded a conflict, so this flow raises it rather than shadowing it
        with a Google-specific twin - see ``DuplicateGoogleSubjectError`` for the
        collision that does need its own class, and why the two are different.
        """
        service, verifier = sign_up_with_google()
        service.execute(verifier.mint(subject=SUBJECT, email=ADDRESS), NOW)

        other = verifier.mint(subject=OTHER_SUBJECT, email=ADDRESS)

        with pytest.raises(DuplicateEmailError):
            service.execute(other, NOW)

    def test_a_subject_that_already_has_an_account_is_refused(
        self, sign_up_with_google
    ):
        service, verifier = sign_up_with_google()
        service.execute(verifier.mint(subject=SUBJECT, email=ADDRESS), NOW)

        # A second token for the same Google account. Its address has changed, so
        # the email lookup would not catch this - only the subject does.
        again = verifier.mint(subject=SUBJECT, email="somewhere-else@example.com")

        with pytest.raises(DuplicateGoogleSubjectError):
            service.execute(again, NOW)

    def test_the_subject_collision_is_reported_before_the_address_one(
        self, sign_up_with_google
    ):
        """Which sentence a person reads when both are true, which they can be.

        A client that called the create route for a Google account that already
        has one here, whose address is also taken - because it is the same account
        - needs to be told to log in, not to use another address. The second would
        send them to make a second account.
        """
        service, verifier = sign_up_with_google()
        service.execute(verifier.mint(subject=SUBJECT, email=ADDRESS), NOW)

        again = verifier.mint(subject=SUBJECT, email=ADDRESS)

        with pytest.raises(DuplicateGoogleSubjectError):
            service.execute(again, NOW)

    def test_an_unknown_token_is_refused(self, factory, sign_up_with_google):
        """And nothing is written, because the refusal precedes the unit.

        ``InvalidGoogleTokenError`` rather than a bare ``Exception``, so the
        assertion names the refusal it means - and the second half is what pins
        the *ordering*: verification does not happen inside the transaction, so an
        unverifiable token cannot have opened one to roll back.
        """
        service, _ = sign_up_with_google()

        with pytest.raises(InvalidGoogleTokenError):
            service.execute("a-token-nobody-minted", NOW)

        uow = factory.start()
        try:
            assert uow.users.find_by_email(ADDRESS) is None
        finally:
            uow.rollback()


class TestWhatTheTokenDecides:
    def test_the_client_cannot_name_the_subject(self, factory, sign_up_with_google):
        """**The subject comes from the token and from nowhere else.**

        There is no parameter for it, which is the strongest form of this
        guarantee - and the assertion below is what makes it visible: the subject
        stored is the one the *verifier* reported, so a future signature change
        that accepted one from the caller would fail here.
        """
        service, verifier = sign_up_with_google()
        token = verifier.mint(subject=SUBJECT, email=ADDRESS)

        service.execute(token, NOW)

        uow = factory.start()
        try:
            assert uow.users.find_by_google_subject(OTHER_SUBJECT) is None
            assert uow.users.find_by_google_subject(SUBJECT) is not None
        finally:
            uow.rollback()

    def test_the_address_comes_from_the_token(self, factory, sign_up_with_google):
        service, verifier = sign_up_with_google()
        token = verifier.mint(subject=SUBJECT, email=ADDRESS)

        service.execute(token, NOW)

        uow = factory.start()
        try:
            assert uow.users.find_by_email(ADDRESS) is not None
        finally:
            uow.rollback()

    def test_the_address_is_folded_on_the_way_in(self, factory, sign_up_with_google):
        """Google's spelling is not necessarily the stored one.

        The adapter and ``GoogleIdentity`` both keep Google's spelling - folding is
        ``User``'s rule, applied where an address is stored - so ``User`` applies it
        here, and the duplicate lookup below folds too. That is why
        ``Chinedu@Example.com`` cannot become a second account alongside
        ``chinedu@example.com``.
        """
        service, verifier = sign_up_with_google()
        token = verifier.mint(subject=SUBJECT, email="Chinedu@Example.com")

        user = service.execute(token, NOW)

        assert user.email == "chinedu@example.com"

    def test_two_spellings_of_one_address_collide(self, sign_up_with_google):
        """The fold, asserted as the claim it is: these two are one account."""
        service, verifier = sign_up_with_google()
        service.execute(verifier.mint(subject=SUBJECT, email=ADDRESS), NOW)

        other = verifier.mint(subject=OTHER_SUBJECT, email="CHINEDU@EXAMPLE.COM")

        with pytest.raises(DuplicateEmailError):
            service.execute(other, NOW)


class TestWhatIsNotWritten:
    def test_it_writes_no_phone(self, factory, sign_up_with_google):
        """``None`` and not ``""``, mirroring ``SignUp``'s treatment of a subject.

        An account created from Google was not signed up with a handset, and the
        column is ``UNIQUE`` - so an empty string would collide with every other
        account that arrived this way, failing the second signup against the first.
        """
        service, verifier = sign_up_with_google()
        token = verifier.mint(subject=SUBJECT, email=ADDRESS)

        user = service.execute(token, NOW)

        assert user.phone is None

    def test_it_returns_no_session(self, factory, sign_up_with_google):
        """Registering and proving stay separate acts, as ``SignUp`` decided.

        The return type is the enforcement - a ``User`` has no token on it - so this
        test asserts the *shape* of what came back, which is what a caller would
        have to change to break the separation.
        """
        service, verifier = sign_up_with_google()
        token = verifier.mint(subject=SUBJECT, email=ADDRESS)

        user = service.execute(token, NOW)

        assert not hasattr(user, "token")
        assert not hasattr(user, "session")
