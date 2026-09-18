"""``InitiateDeposit``: recording that money was asked for, and nothing else.

**The assertion this file exists for is a balance that does not move.** Every
other test here is scaffolding around it or a refusal that happens before it. A
deposit use case that credited the wallet on intent would be creating money out
of a promise, and the reason the mirror is not obvious is that the existing
template - ``WalletOperation`` - does exactly that: it writes the row and then
calls ``_apply``. So the file's first class is the case for *not* being a
``WalletOperation``, stated as a property rather than as an argument.

The refusals come next, and their *ordering* is the second thing worth reading.
A closed wallet is refused before the provider is called, which is not a
performance note: a collection opened against a wallet nothing can credit would
take a real person's money and have nowhere to put it. The test for that asserts
the provider's ``requests`` list is empty, because "it raised" and "it never
called out" are different claims and only the second one is the point.

**One refusal is about the characters in the key itself**, and a real provider is
what taught it. The reference built here is also the idempotency key Paystack is
handed, and their alphabet is narrower than a Python string's; see
``TestAKeyTheProviderWouldRefuse`` for the bug that cost.

**The newest refusal is about the rail rather than about the request**, and it is
the one this file's ``CurrencyMismatchError`` test sits next to rather than
duplicates. A wallet may be opened in any of five currencies; the rail collects
in particular ones; ``TestACurrencyTheRailCannotCollect`` is the meeting of the
two, and it is deliberately the newer of a pair of refusals that read alike -
which is why the ordering between them is pinned rather than assumed.

**Every test here seeds a user row**, and that is a consequence of a design
decision rather than an inconvenience. The email the provider requires is read
from the actor's own record (``_prepare`` does
``uow.users.get_by_id(self._actor)``) rather than taken as an argument, so a test
that skipped the seed would fail on a lookup rather than on anything about
deposits. See ``TestThePayerIsTheActor`` for the half of that decision that is
about behaviour.

**The SQLite factory is built here rather than taken from a fixture**, because
these tests are about two units of work rather than one - the read-only unit that
validates and the writing unit that records - and a test that shared a factory
with something else could not say which of them a row came from.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.payments.initiate_deposit import InitiateDeposit
from app.domain.identity.exception import TierLimitExceededError
from app.domain.identity.limitKind import LimitKind
from app.domain.identity.profile import Profile
from app.domain.identity.tier import Tier, limits_for
from app.domain.money.currency import Currency
from app.domain.money.exception import (
    CurrencyMismatchError,
    InvalidAmountError,
    InvalidIdempotencyKeyError,
    WalletClosedError,
    WalletNotFoundError,
)
from app.domain.money.money import Money
from app.domain.money.transactionStatus import TransactionStatus
from app.domain.money.transactionType import TransactionType
from app.domain.money.walletStatus import WalletStatus
from app.domain.payments.exception import (
    CurrencyNotCollectableError,
    DepositAlreadyInitiatedError,
    PayerEmailMissingError,
    PayerEmailRefusedError,
    PaymentProviderUnavailableError,
)
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)
from tests.conftest import TEST_USER_ID

NGN = Currency.NGN
USD = Currency.USD

ACTOR = TEST_USER_ID

#: The most an account with no profile may hold, in the wallet's own currency.
#:
#: Read from the table rather than typed out, for the reason every limit test in
#: this suite reads it: a test that hard-coded the number would go on passing -
#: and asserting a refusal that no longer happens - after somebody raised the cap.
CAP = limits_for(Tier.UNVERIFIED, NGN).max_balance

#: The moment every synthetic row in this file is stamped with, for the reason
#: ``tests/conftest.POT_MOMENT`` exists: a stored value a test asserts about should
#: not depend on when the suite ran.
MOMENT = datetime(2026, 1, 1)

#: A second account, for the test that a deposit cannot be opened against
#: somebody else's wallet.
STRANGER = uuid4()

#: The address the payer is registered at. Not ``TEST_USER_EMAIL``, so that the
#: test asserting the provider was told the actor's address is asserting a value
#: that could only have come from the row this test seeded - a shared constant
#: would pass if the adapter fell back to something else that happened to match.
#:
#: ``example.com`` rather than ``localhost`` because this address is one a payment
#: provider is asked to bill, and it will not bill a domain that cannot exist -
#: ``app.domain.payments.payerEmail`` refuses one before the call, so a payer at
#: ``localhost`` would make every deposit test here a test of the refusal.
PAYER = "payer@example.com"


def a_deposit_service(tmp_path, provider, actor=ACTOR, name="deposits.db"):
    factory = SqliteUnitOfWorkFactory(str(tmp_path / name))
    return InitiateDeposit(factory, provider, actor=actor), factory


def seed(factory, wallet, user=None):
    """Put a wallet and its owner in the store, in one unit.

    Both, always, even when a test is not about the user: ``_prepare`` reads the
    actor's row for its email, so a fixture that seeded only the wallet would
    make every test in this file fail on a lookup that has nothing to do with
    what it is testing.
    """
    uow = factory.start()
    uow.users.save(user)
    uow.wallets.save(wallet)
    uow.commit()


def rows(factory, wallet):
    """Every ledger row on this wallet's ledger, oldest first.

    Read through the same repository method ``GET /wallets/{id}/transactions``
    uses, so a row that was written but is invisible to a client shows up here as
    a missing row rather than as a passing test.
    """
    uow = factory.start()
    try:
        return uow.transactions.get_by_wallet_id(wallet.wallet_id)
    finally:
        uow.rollback()


def stored(factory, wallet):
    """The wallet as the store holds it - read back, not the object in hand.

    Every balance assertion in this file goes through here, and that is the
    point: the ``Wallet`` a test built is the one it handed to ``seed``, so
    asserting on it would be asserting on a local variable. This reads the row.
    """
    uow = factory.start()
    try:
        return uow.wallets.get_owned(wallet.wallet_id, wallet.user_id)
    finally:
        uow.rollback()


def store_profile(factory, user_id=ACTOR, **overrides):
    """Give an account a profile - complete unless a field is knocked out.

    Written straight to the repository rather than through ``ProfileService``, and
    the reason is the one that keeps these tests about deposits: the service has
    its own tests and its own argument for what a save does. What matters here is
    only the state it leaves behind, which is what ``tier_for`` reads.
    """
    fields = dict(
        display_name="Ada",
        legal_first_name="Adaeze",
        legal_last_name="Okafor",
        date_of_birth=date(1990, 5, 17),
        phone="+2348000000000",
        country="NG",
        address_line="12 Marina Road, Lagos",
    )
    fields.update(overrides)
    uow = factory.start()
    uow.profiles.save(
        Profile(user_id=user_id, created_at=MOMENT, updated_at=MOMENT, **fields)
    )
    uow.commit()


@pytest.fixture
def payer(build_user):
    return build_user(email=PAYER)


class TestNothingIsCredited:
    """The case for the mirror, and the reason this is not a ``WalletOperation``."""

    def test_the_balance_is_untouched(
        self, tmp_path, build_wallet, payer, build_payment_provider
    ):
        """**A 201 that credits nothing, which is the whole of decision 97.**

        ``WalletOperation.execute`` writes the row and then calls ``_apply``,
        which for a withdrawal debits the wallet. This is that template's mirror:
        the row is written and nothing else happens, because the money does not
        exist yet and only the party that actually moves it can say that it does.
        """
        wallet = build_wallet(available="1000")
        service, factory = a_deposit_service(tmp_path, build_payment_provider())
        seed(factory, wallet, payer)

        service.execute(wallet.wallet_id, Money(5000, NGN), "invoice-7")

        assert stored(factory, wallet).available_balance == Money(1000, NGN)

    def test_the_row_is_recorded_and_it_is_pending(
        self, tmp_path, build_wallet, payer, build_payment_provider
    ):
        """The other half: something *is* written, and it says what it is.

        A use case that credited nothing and recorded nothing would also pass the
        test above, and it would be a deposit that vanished. The row is the
        request, and its being PENDING is how a settled deposit will later be told
        from one that never arrived.
        """
        wallet = build_wallet(available="1000")
        service, factory = a_deposit_service(tmp_path, build_payment_provider())
        seed(factory, wallet, payer)

        service.execute(wallet.wallet_id, Money(5000, NGN), "invoice-7")

        ledger = rows(factory, wallet)
        assert len(ledger) == 1
        assert ledger[0].type is TransactionType.DEPOSIT
        assert ledger[0].status is TransactionStatus.PENDING
        assert ledger[0].amount == Money(5000, NGN)
        assert ledger[0].completed_at is None

    def test_the_reference_the_provider_was_given_is_the_one_recorded(
        self, tmp_path, build_wallet, payer, build_payment_provider
    ):
        """**The join between the two halves of the feature.**

        A webhook knows a reference and nothing else, so the column it will be
        matched on has to hold the name the provider was told. Here the provider
        answers with a reference of its own, which is the case that would break if
        the use case recorded its own input instead of the provider's answer.
        """
        wallet = build_wallet()
        provider = build_payment_provider(provider_reference="ps_minted")
        service, factory = a_deposit_service(tmp_path, provider)
        seed(factory, wallet, payer)

        service.execute(wallet.wallet_id, Money(5000, NGN), "invoice-7")

        assert rows(factory, wallet)[0].provider_reference == "ps_minted"

    def test_the_key_is_scoped_to_the_wallet(
        self, tmp_path, build_wallet, payer, build_payment_provider
    ):
        """Two wallets may both ask for ``invoice-7``, and both are real deposits.

        The scope is the *wallet* rather than the owner, because a wallet has
        exactly one owner - so scoping by owner would leave one case open: the
        same person posting the same key to two of their own wallets. The
        reference carries the wallet id, which is what makes the two distinct at
        the provider as well as here.
        """
        wallet = build_wallet()
        provider = build_payment_provider()
        service, factory = a_deposit_service(tmp_path, provider)
        seed(factory, wallet, payer)

        result = service.execute(wallet.wallet_id, Money(5000, NGN), "invoice-7")

        assert provider.requests[0]["reference"].startswith(str(wallet.wallet_id))
        assert result.provider_reference == provider.requests[0]["reference"]

    def test_it_returns_what_the_client_needs_and_not_the_row(
        self, tmp_path, build_wallet, payer, build_payment_provider
    ):
        """``InitiatedDeposit`` is a report about a *request*, and it says so.

        The status travels with the answer so a client is told ``pending`` in so
        many words rather than inferring it from a balance that did not move. The
        ledger row is deliberately not returned: it will change, and this will
        not.
        """
        wallet = build_wallet()
        provider = build_payment_provider()
        service, factory = a_deposit_service(tmp_path, provider)
        seed(factory, wallet, payer)

        result = service.execute(wallet.wallet_id, Money(5000, NGN), "invoice-7")

        assert result.authorization_url.startswith("https://")
        assert result.amount == Money(5000, NGN)
        assert result.status is TransactionStatus.PENDING


class TestThePayerIsTheActor:
    def test_the_provider_is_told_the_actors_own_address(
        self, tmp_path, build_wallet, payer, build_payment_provider
    ):
        """The one fact about a person this system hands a third party.

        Read from the actor's stored record rather than taken as an argument,
        which is a narrowing rather than a convenience: an ``email`` parameter
        would be a value the caller supplies alongside an actor id, free to
        disagree with it - and this is not the place to accept an unverified fact
        about a person.
        """
        wallet = build_wallet()
        provider = build_payment_provider()
        service, factory = a_deposit_service(tmp_path, provider)
        seed(factory, wallet, payer)

        service.execute(wallet.wallet_id, Money(5000, NGN), "invoice-7")

        assert provider.requests[0]["email"] == PAYER

    def test_a_stranger_cannot_open_a_collection_on_somebody_elses_wallet(
        self, tmp_path, build_wallet, payer, build_payment_provider
    ):
        """404, and the same one a wallet that never existed gets.

        The provider is asserted to have been called *zero* times, because "it
        raised" and "nothing was sent" are different claims - and the second is
        what a client is trusting when it is told the wallet is not there.
        """
        wallet = build_wallet()
        provider = build_payment_provider()
        service, factory = a_deposit_service(tmp_path, provider, actor=STRANGER)
        seed(factory, wallet, payer)

        with pytest.raises(WalletNotFoundError):
            service.execute(wallet.wallet_id, Money(5000, NGN), "invoice-7")

        assert provider.requests == []


class TestTheRefusals:
    """Everything refused before a collection is opened, and none of it calls out."""

    def test_a_closed_wallet_is_refused_before_the_provider_is_called(
        self, tmp_path, build_wallet, payer, build_payment_provider
    ):
        """**The reason this check is here rather than three layers down.**

        ``Wallet.apply_deposit`` refuses a CLOSED wallet, so a collection opened
        against one would take a payer's money that nothing could ever credit -
        the payer would be sent to a checkout page and the money would arrive with
        nowhere to go. Refusing at the door means they are never sent anywhere,
        and the empty ``requests`` list is that claim as an assertion.
        """
        wallet = build_wallet(status=WalletStatus.CLOSED)
        provider = build_payment_provider()
        service, factory = a_deposit_service(tmp_path, provider)
        seed(factory, wallet, payer)

        with pytest.raises(WalletClosedError):
            service.execute(wallet.wallet_id, Money(5000, NGN), "invoice-7")

        assert provider.requests == []
        assert rows(factory, wallet) == []

    def test_a_deposit_that_would_leave_the_wallet_above_its_cap_is_refused(
        self, tmp_path, build_wallet, payer, build_payment_provider
    ):
        """**The refusal that saves a payer a pointless payment.**

        A collection opened for money this wallet could not accept would send
        somebody to a checkout page to buy a rejection. Nothing about the request
        is wrong - the amount is positive, in the wallet's own currency, and the
        wallet is open - so it is refused for the only reason it can be: the
        account is not allowed to hold that much.

        The empty ``requests`` list is the claim rather than the exception. "It
        raised" and "it never called out" are different statements, and only the
        second one is worth anything to the person who would have been billed.
        """
        wallet = build_wallet(available=str(CAP.amount))
        provider = build_payment_provider()
        service, factory = a_deposit_service(tmp_path, provider)
        seed(factory, wallet, payer)

        with pytest.raises(TierLimitExceededError) as refused:
            service.execute(wallet.wallet_id, Money(1, NGN), "invoice-7")

        assert refused.value.kind is LimitKind.MAX_BALANCE
        assert provider.requests == []
        assert rows(factory, wallet) == []

    def test_a_deposit_that_lands_exactly_on_the_cap_is_allowed(
        self, tmp_path, build_wallet, payer, build_payment_provider
    ):
        """The boundary, at this door as well as in the domain.

        ``check_credit`` refuses a balance that would be *above* the cap, so a
        wallet that lands exactly on it is within its limits. The comparison is
        pinned here because this check is a courtesy and a courtesy is exactly the
        kind of code that quietly becomes ``>=`` - the deposit would simply be
        refused while it fitted, and nothing else would notice.
        """
        wallet = build_wallet(available=str(CAP.amount - Decimal("5000")))
        service, factory = a_deposit_service(tmp_path, build_payment_provider())
        seed(factory, wallet, payer)

        result = service.execute(wallet.wallet_id, Money(5000, NGN), "invoice-7")

        assert result.status is TransactionStatus.PENDING

    def test_a_pot_counts_toward_the_cap(
        self, tmp_path, build_wallet, payer, build_payment_provider
    ):
        """Locked money is money this account still holds.

        A deposit into a wallet whose pot already reaches the cap is refused even
        though the *available* balance is zero. The cap is a statement about what
        the wallet holds, and a pot is inside the wallet, so reading only the
        available balance would make a fully-locked wallet the one way past the
        ceiling - which is the shape of hole a second, hand-written comparison
        would leave and ``check_credit`` cannot.
        """
        wallet = build_wallet(available="0", locked=str(CAP.amount))
        provider = build_payment_provider()
        service, factory = a_deposit_service(tmp_path, provider)
        seed(factory, wallet, payer)

        with pytest.raises(TierLimitExceededError):
            service.execute(wallet.wallet_id, Money(1, NGN), "invoice-7")

        assert provider.requests == []

    def test_the_cap_is_the_owners_and_a_completed_profile_raises_it(
        self, tmp_path, build_wallet, payer, build_payment_provider
    ):
        """The tier comes off the actor's own profile and from nowhere else.

        The same wallet that is refused above accepts a smaller deposit here, and
        the only thing that changed between the two is a row on the actor's own
        account. Nothing in the request carries a tier, so there is no field a
        caller could set to reach the higher ceiling - which is the property the
        whole control rests on.
        """
        wallet = build_wallet(available=str(CAP.amount))
        provider = build_payment_provider()
        service, factory = a_deposit_service(tmp_path, provider)
        seed(factory, wallet, payer)
        store_profile(factory)

        result = service.execute(wallet.wallet_id, Money(5000, NGN), "invoice-7")

        assert result.status is TransactionStatus.PENDING

    def test_a_frozen_wallet_is_allowed(
        self, tmp_path, build_wallet, payer, build_payment_provider
    ):
        """The asymmetry with closed, asserted rather than left to the docstring.

        Freezing stops value *leaving*, and this is value arriving - into a
        balance the freeze already protects. Refusing would mean a user who froze
        a wallet because they were worried about it could not be paid.
        """
        wallet = build_wallet(status=WalletStatus.FROZEN)
        service, factory = a_deposit_service(tmp_path, build_payment_provider())
        seed(factory, wallet, payer)

        result = service.execute(wallet.wallet_id, Money(5000, NGN), "invoice-7")

        assert result.status is TransactionStatus.PENDING

    @pytest.mark.parametrize("amount", [0, -1])
    def test_an_amount_that_is_not_a_movement_is_refused(
        self, tmp_path, build_wallet, payer, build_payment_provider, amount
    ):
        """Zero and negative, refused before anything is asked for.

        ``Money`` permits both - it has an ``is_debt`` property and a wallet may
        hold a negative balance - so the check has to be a rule about *deposits*
        rather than about amounts.
        """
        wallet = build_wallet()
        provider = build_payment_provider()
        service, factory = a_deposit_service(tmp_path, provider)
        seed(factory, wallet, payer)

        with pytest.raises(InvalidAmountError):
            service.execute(wallet.wallet_id, Money(amount, NGN), "invoice-7")

        assert provider.requests == []

    def test_an_amount_that_is_not_money_is_refused(
        self, tmp_path, build_wallet, payer, build_payment_provider
    ):
        """A bare number, which a caller reaching this from a CLI could easily pass.

        Refused as an amount rather than as a currency, and the ordering is
        deliberate: a caller that sent something which is not a ``Money`` at all
        would otherwise be told its currency was wrong.
        """
        wallet = build_wallet()
        provider = build_payment_provider()
        service, factory = a_deposit_service(tmp_path, provider)
        seed(factory, wallet, payer)

        with pytest.raises(InvalidAmountError):
            service.execute(wallet.wallet_id, 5000, "invoice-7")

        assert provider.requests == []

    def test_an_amount_in_another_currency_is_refused(
        self, tmp_path, build_wallet, payer, build_payment_provider
    ):
        """The wallet decides the currency, never the request.

        The same rule ``PlanService.create_plan`` enforces against a plan and the
        same one ``translate.money_in`` applies at the wire - a deposit route
        reads a bare amount in the wallet's currency rather than accepting one the
        caller chose.
        """
        wallet = build_wallet()
        provider = build_payment_provider()
        service, factory = a_deposit_service(tmp_path, provider)
        seed(factory, wallet, payer)

        with pytest.raises(CurrencyMismatchError):
            service.execute(wallet.wallet_id, Money(5000, USD), "invoice-7")

        assert provider.requests == []


class TestACurrencyTheRailCannotCollect:
    """The guard, and the two orderings that decide what a caller is told.

    **This is the refusal that stops a payer's money being taken against a row
    that can never settle.** A wallet may be opened in any of five currencies and
    the rail is enabled for particular ones; without this check the collection
    goes out carrying the amount's number and NGN, the payer pays in naira, and
    ``SettlePayment`` - which compares what arrived against what the row asked
    for - refuses for ever. Nothing is credited and the reconciler reports the
    same disagreement on every run.

    It is refused here rather than at wallet creation, and the reason is that a
    wallet is a container rather than a collection: one holding dollars still
    holds pots, still runs plans, and can still be credited directly by
    ``WalletService`` without any rail being involved. What is impossible is a
    *collection* for it, and that is a fact about this door. Those three
    properties are asserted elsewhere in the suite - the USD wallets opened in
    ``test_plans.py`` and ``test_cli_plans.py`` - and their staying green is the
    assertion that creation was not restricted along with the rail.
    """

    def test_a_wallet_the_rail_cannot_collect_is_refused_before_calling_out(
        self, tmp_path, build_wallet, payer, build_payment_provider
    ):
        """**The empty ``requests`` list is the whole point, again.**

        The amount matches its wallet, so nothing above this check objects. What
        refuses it is the meeting of the wallet's currency and the rail's - and
        the payer is never sent to a payment page to find out.
        """
        wallet = build_wallet(currency=USD)
        provider = build_payment_provider()
        service, factory = a_deposit_service(tmp_path, provider)
        seed(factory, wallet, payer)

        with pytest.raises(CurrencyNotCollectableError):
            service.execute(wallet.wallet_id, Money(5000, USD), "invoice-7")

        assert provider.requests == []
        assert rows(factory, wallet) == []

    def test_the_rail_is_asked_rather_than_assumed(
        self, tmp_path, build_wallet, payer, build_payment_provider
    ):
        """**The test that separates asking the rail from hard-coding NGN.**

        Against a provider that only ever collects naira, "the door asked" and
        "the door assumed" are the same behaviour and no assertion can tell them
        apart - so the provider is built enabled for dollars, and the same wallet
        that was refused a moment ago now gets through. That is the difference
        the whole port method exists for: the set has one home, and this is what
        proves the door is reading it.

        **The amount and the opening balance are both read off the limits table,
        and that is not fussiness.** A dollar wallet's ceiling is a tenth of a
        naira one - 3,000 against 50,000 for an unverified account - so the 5,000
        the test above deposits would be refused one check *later* by the ceiling,
        and this test would fail for a reason that has nothing to do with the
        rail. The wallet below is what a passing guard looks like: small enough
        to be within its limits, and admitted only because the provider was asked.
        """
        amount = limits_for(Tier.UNVERIFIED, USD).max_balance.amount / 2
        wallet = build_wallet(currency=USD, available="0")
        provider = build_payment_provider(currencies=frozenset({USD}))
        service, factory = a_deposit_service(tmp_path, provider)
        seed(factory, wallet, payer)

        service.execute(wallet.wallet_id, Money(amount, USD), "invoice-7")

        assert [r["amount"] for r in provider.requests] == [Money(amount, USD)]
        assert len(rows(factory, wallet)) == 1

    def test_the_amount_is_weighed_against_the_wallet_before_the_rail(
        self, tmp_path, build_wallet, payer, build_payment_provider
    ):
        """The ordering, and the wrong sentence it prevents.

        A naira wallet sent a dollar amount is a *mismatch* - the caller's typo,
        and something they can correct. The rail can collect naira perfectly
        well, so the currency guard has nothing to say about this request; if it
        were asked first it would answer with a sentence about what this
        installation supports, which is true and beside the point. Two refusals
        that read alike are the reason ``errors`` keeps a separate name for each,
        and this is where the order that keeps them apart is pinned.
        """
        wallet = build_wallet(currency=NGN)
        provider = build_payment_provider()
        service, factory = a_deposit_service(tmp_path, provider)
        seed(factory, wallet, payer)

        with pytest.raises(CurrencyMismatchError):
            service.execute(wallet.wallet_id, Money(5000, USD), "invoice-7")

    def test_a_closed_wallet_answers_with_its_closure_and_not_its_currency(
        self, tmp_path, build_wallet, payer, build_payment_provider
    ):
        """The other edge of the same ordering, and the more actionable refusal.

        A wallet can be both closed and in a currency the rail cannot collect. A
        closed wallet is the one with a remedy - give up on it and open another -
        while a currency the rail cannot collect has none at all, so the caller
        is told the fact they can act on. This is the same reasoning that puts
        401 above 404 in ``errors``: identity first, because everything after it
        is answered about somebody who has been established.
        """
        wallet = build_wallet(currency=USD, status=WalletStatus.CLOSED)
        provider = build_payment_provider()
        service, factory = a_deposit_service(tmp_path, provider)
        seed(factory, wallet, payer)

        with pytest.raises(WalletClosedError):
            service.execute(wallet.wallet_id, Money(5000, USD), "invoice-7")

    def test_the_refusal_names_the_currency_and_what_can_be_collected(
        self, tmp_path, build_wallet, payer, build_payment_provider
    ):
        """A remedy, not just a refusal.

        "This wallet holds USD" tells a caller what they already know. Naming the
        currencies the installation *does* collect is what turns the sentence
        into one somebody can act on - open a wallet in one of those - and it is
        built from the set the refusal was decided against, so the two cannot
        disagree. Sorted, because the set it comes from has no order and a
        message that reorders itself between runs is one a reader stops trusting.
        """
        wallet = build_wallet(currency=USD)
        provider = build_payment_provider()
        service, factory = a_deposit_service(tmp_path, provider)
        seed(factory, wallet, payer)

        with pytest.raises(CurrencyNotCollectableError) as raised:
            service.execute(wallet.wallet_id, Money(5000, USD), "invoice-7")

        assert "USD" in str(raised.value)
        assert "NGN" in str(raised.value)

    def test_a_naira_wallet_is_unaffected(
        self, tmp_path, build_wallet, payer, build_payment_provider
    ):
        """The control, without which every test above passes on a door that
        refuses everything.

        Worth its four lines: the change this guards is one that adds a refusal
        to a path that mostly works, and the failure mode of getting it wrong is
        not a missed refusal but a deposit route that no longer opens a
        collection at all.
        """
        wallet = build_wallet(currency=NGN)
        provider = build_payment_provider()
        service, factory = a_deposit_service(tmp_path, provider)
        seed(factory, wallet, payer)

        service.execute(wallet.wallet_id, Money(5000, NGN), "invoice-7")

        assert len(provider.requests) == 1
        assert len(rows(factory, wallet)) == 1


class TestTheSameKeyTwice:
    def test_a_repeated_key_is_refused_and_refused_before_calling_out(
        self, tmp_path, build_wallet, payer, build_payment_provider
    ):
        """**The dedupe is before the call rather than after, and that is the design.**

        A retry that reached the provider would open a second collection under a
        reference it has already seen. The provider would refuse it - that is what
        the reference is for - but refusing it here means the request never leaves
        this process at all.
        """
        wallet = build_wallet()
        provider = build_payment_provider()
        service, factory = a_deposit_service(tmp_path, provider)
        seed(factory, wallet, payer)
        service.execute(wallet.wallet_id, Money(5000, NGN), "invoice-7")

        with pytest.raises(DepositAlreadyInitiatedError):
            service.execute(wallet.wallet_id, Money(5000, NGN), "invoice-7")

        assert len(provider.requests) == 1

    def test_the_second_attempt_leaves_no_second_row(
        self, tmp_path, build_wallet, payer, build_payment_provider
    ):
        """The refusal happens in a unit that writes nothing.

        The read-only unit is rolled back rather than committed, so there is
        nothing to make durable and nothing left holding a read lock the next
        writer would wait on.
        """
        wallet = build_wallet()
        service, factory = a_deposit_service(tmp_path, build_payment_provider())
        seed(factory, wallet, payer)
        service.execute(wallet.wallet_id, Money(5000, NGN), "invoice-7")

        with pytest.raises(DepositAlreadyInitiatedError):
            service.execute(wallet.wallet_id, Money(5000, NGN), "invoice-7")

        assert len(rows(factory, wallet)) == 1

    def test_two_different_keys_are_two_deposits(
        self, tmp_path, build_wallet, payer, build_payment_provider
    ):
        """The other side of the rule, so that the dedupe is pinned as a dedupe.

        A check that refused every second deposit would pass both tests above and
        make the endpoint useless.
        """
        wallet = build_wallet()
        service, factory = a_deposit_service(tmp_path, build_payment_provider())
        seed(factory, wallet, payer)

        service.execute(wallet.wallet_id, Money(5000, NGN), "invoice-7")
        service.execute(wallet.wallet_id, Money(7500, NGN), "invoice-8")

        assert len(rows(factory, wallet)) == 2


class TestAKeyTheProviderWouldRefuse:
    """The character rule, at the layer that violated it and could not see it.

    The reference this use case builds is the one a payment provider is handed as
    its idempotency key, and it used to join the wallet and the caller's key with
    a ``:`` - a character Paystack refuses outright. Every deposit this route ever
    attempted against the real provider came back
    ``400 invalid_character_in_reference``, and nothing in this file noticed,
    because nothing in this file checked: the fake provider accepted whatever it
    was given, so the suite agreed with the bug for as long as it existed.
    """

    def test_a_key_the_provider_cannot_take_is_refused_before_the_call(
        self, tmp_path, build_wallet, payer, build_payment_provider
    ):
        """Which is the same *ordering* claim the closed-wallet refusal makes.

        "It raised" and "it never called out" are different claims, and only the
        second one is worth anything here: a collection opened under a reference
        the provider would reject is a collection that does not exist, so the
        refusal has to land before the request leaves this process. Nothing is
        left at the far end to clean up, and no row is written here to explain.
        """
        wallet = build_wallet()
        provider = build_payment_provider()
        service, factory = a_deposit_service(tmp_path, provider)
        seed(factory, wallet, payer)

        with pytest.raises(InvalidIdempotencyKeyError):
            service.execute(wallet.wallet_id, Money(5000, NGN), "order:42")

        assert provider.requests == []
        assert rows(factory, wallet) == []

    def test_the_refusal_names_the_character_and_what_is_allowed(
        self, tmp_path, build_wallet, payer, build_payment_provider
    ):
        """Because the provider's own answer is a sentence about nothing.

        Paystack replies to this with a 400 whose body reads as though *this*
        system is broken - which is precisely how it read for the whole life of
        the deposit route, and why nobody looked at the reference. The refusal
        made here has to be the opposite of that: it names the offending
        character and the set that would have been accepted, because the person
        reading it is holding a key they chose.
        """
        wallet = build_wallet()
        service, factory = a_deposit_service(tmp_path, build_payment_provider())
        seed(factory, wallet, payer)

        with pytest.raises(InvalidIdempotencyKeyError) as refused:
            service.execute(wallet.wallet_id, Money(5000, NGN), "order:42")

        assert ":" in str(refused.value)
        assert "- . , =" in str(refused.value)

    def test_a_key_of_legal_characters_still_reaches_the_provider(
        self, tmp_path, build_wallet, payer, build_payment_provider
    ):
        """The other side of the rule, so the check is pinned as a check.

        A validator that refused everything would pass both tests above and make
        the route useless - and the point of this one is narrower than it looks:
        the separator is in the same alphabet as the key, so the whole reference
        is one a provider accepts, which is exactly what the fake now enforces.
        """
        wallet = build_wallet()
        provider = build_payment_provider()
        service, factory = a_deposit_service(tmp_path, provider)
        seed(factory, wallet, payer)

        service.execute(wallet.wallet_id, Money(5000, NGN), "order-42.retry")

        assert provider.requests[0]["reference"] == (
            f"{wallet.wallet_id}.order-42.retry"
        )


class TestThePayerAddressTheProviderWouldRefuse:
    """The second thing a real provider taught this use case, and the cheaper one.

    ``TestAKeyTheProviderWouldRefuse`` above is about the *key* this use case
    builds; this class is about the *address* it passes along, and the address is
    not built here at all - it is read off the actor's own row. That difference is
    why the refusal is a courtesy rather than this layer's judgement: what a
    provider will bill is the provider's rule, and this layer knows only the one
    case that is certain.

    ``app.domain.payments.payerEmail`` carries the argument for the narrowness.
    What is asserted *here* is the ordering, which is the same claim the
    closed-wallet test makes and the same one the key test makes: a collection
    opened under an address no provider will bill does not exist, so the refusal
    has to land before the request leaves this process.
    """

    def test_an_address_the_provider_would_refuse_never_reaches_it(
        self, tmp_path, build_wallet, build_user, build_payment_provider
    ):
        """``live@localhost``, which is the address a live run was refused for.

        Not ``localhost`` in particular - the rule is the missing dot, because one
        refused address and one accepted one differing by exactly that is the
        whole of the evidence anybody has. It is the same address the register
        endpoint used to create, which was the uncomfortable half: the account
        existed, it could hold a wallet, and no provider would ever bill it.

        **That half is closed, and it is why this test seeds rather than
        registers.** ``SignUp`` refuses the address now, so the account this
        refusal catches can only be one written before the rule - which is what
        ``build_user`` produces here, a row constructed in memory and stored
        directly. The refusal did not become unreachable when the entry rule
        landed; it became what it always should have been, a second line for the
        accounts already stranded rather than the only line for new ones.
        """
        wallet = build_wallet()
        provider = build_payment_provider()
        service, factory = a_deposit_service(tmp_path, provider)
        seed(factory, wallet, build_user(email="payer@localhost"))

        with pytest.raises(PayerEmailRefusedError):
            service.execute(wallet.wallet_id, Money(5000, NGN), "order-42")

        assert provider.requests == []
        assert rows(factory, wallet) == []

    def test_the_refusal_names_the_address_and_the_reason(
        self, tmp_path, build_wallet, build_user, build_payment_provider
    ):
        """Because the provider's own answer sends the reader to the wrong system.

        Paystack's reply to this is ``Invalid Email Address Passed`` with a
        ``nextStep`` about passing the ``email`` parameter - which reads as though
        this codebase forgot to send one, when in fact it sent exactly the address
        the account was registered under. A refusal that names the address and
        says what is wrong with it is something the person holding the account can
        act on; the provider's sentence is not.
        """
        wallet = build_wallet()
        service, factory = a_deposit_service(tmp_path, build_payment_provider())
        seed(factory, wallet, build_user(email="payer@localhost"))

        with pytest.raises(PayerEmailRefusedError) as refused:
            service.execute(wallet.wallet_id, Money(5000, NGN), "order-42")

        assert "payer@localhost" in str(refused.value)
        assert "real domain" in str(refused.value)

    def test_an_ordinary_address_still_reaches_the_provider(
        self, tmp_path, build_wallet, payer, build_payment_provider
    ):
        """The other side of the courtesy, so it is pinned as a check.

        A check that refused everything would pass both tests above and close the
        deposit route entirely - so the common case, the one every account a
        person actually creates looks like, has to be shown to cost nothing. This
        is also the half ``payerEmail``'s own test file argues hardest for: the
        two ways of being wrong here are not symmetrical, and refusing a good
        address is the one this system cannot undo on somebody's behalf.
        """
        wallet = build_wallet()
        provider = build_payment_provider()
        service, factory = a_deposit_service(tmp_path, provider)
        seed(factory, wallet, payer)

        service.execute(wallet.wallet_id, Money(5000, NGN), "order-42")

        assert provider.requests[0]["email"] == PAYER


class TestAnAccountWithNoAddress:
    """A phone-only account, and the refusal that has to exist before the 500.

    An address became optional on an account so that somebody can sign up with a
    number alone, and such an account reaches this use case complete: a wallet, a
    password, a session, money it can receive. What it cannot do is be *billed*,
    because a provider is handed the payer's address.

    **The order of the two payer checks is the whole of what this class pins.**
    The courtesy below asks a question about an address - ``has_real_domain``,
    which calls ``rpartition`` on what it is given - so ``None`` does not fail that
    check, it raises ``AttributeError`` inside it and reports as a 500. A refusal a
    person can act on and a crash are very different answers to the same request.
    """

    def test_a_deposit_from_an_account_with_no_address_is_refused(
        self, tmp_path, build_wallet, build_user, build_payment_provider
    ):
        wallet = build_wallet()
        provider = build_payment_provider()
        service, factory = a_deposit_service(tmp_path, provider)
        seed(factory, wallet, build_user(email=None, phone="08012345678"))

        with pytest.raises(PayerEmailMissingError):
            service.execute(wallet.wallet_id, Money(5000, NGN), "order-42")

        assert provider.requests == []
        assert rows(factory, wallet) == []

    def test_the_refusal_names_the_remedy(
        self, tmp_path, build_wallet, build_user, build_payment_provider
    ):
        """The sentence has to be actionable, because the person can act on it.

        Nothing is wrong with this account and nothing is wrong with the request;
        there is one field missing, and the operation that supplies it is the email
        change request. Telling somebody "no address" without telling them what to
        do about it leaves them holding an account that works for everything except
        the one thing they came to do.
        """
        wallet = build_wallet()
        service, factory = a_deposit_service(tmp_path, build_payment_provider())
        seed(factory, wallet, build_user(email=None, phone="08012345678"))

        with pytest.raises(PayerEmailMissingError) as refused:
            service.execute(wallet.wallet_id, Money(5000, NGN), "order-42")

        assert "add an email" in str(refused.value)

    def test_the_missing_address_is_refused_rather_than_the_stranger(
        self, tmp_path, build_wallet, build_user, build_payment_provider
    ):
        """Ownership is checked first, so this refusal cannot be an oracle.

        The wallet exists and belongs to somebody else; the actor is a bystander
        who has their own phone-only account. A check that reported the missing
        address before proving the wallet was theirs would answer "does this wallet
        exist" for a stranger - the same ordering claim the closed-wallet test
        makes, one branch over, and the reason the branch below it is reachable at
        all only for the wallet's actual owner.
        """
        wallet = build_wallet()
        provider = build_payment_provider()
        service, factory = a_deposit_service(tmp_path, provider, actor=uuid4())
        seed(factory, wallet, build_user(email=None, phone="08012345678"))

        with pytest.raises(WalletNotFoundError):
            service.execute(wallet.wallet_id, Money(5000, NGN), "order-42")


class TestWhenTheProviderFails:
    def test_a_refused_call_leaves_no_row(
        self, tmp_path, build_wallet, payer, build_payment_provider
    ):
        """**The provider is called between two units, and this is why.**

        The first unit reads and validates; the provider is called; the second
        writes. Holding one SQLite write transaction open across a third party's
        response time would block every other writer for as long as that party
        felt like taking, which is a self-inflicted outage proportional to an
        outage elsewhere.

        The cost of splitting is that the call can fail after the read and before
        the write - and the honest outcome of that is *no row*, because a deposit
        nothing was asked for is not a deposit. A row written optimistically would
        be a PENDING deposit that no webhook will ever settle.
        """
        wallet = build_wallet()
        provider = build_payment_provider(
            failures=[PaymentProviderUnavailableError("the provider is down")]
        )
        service, factory = a_deposit_service(tmp_path, provider)
        seed(factory, wallet, payer)

        with pytest.raises(PaymentProviderUnavailableError):
            service.execute(wallet.wallet_id, Money(5000, NGN), "invoice-7")

        assert rows(factory, wallet) == []

    def test_a_retry_under_the_same_key_succeeds(
        self, tmp_path, build_wallet, payer, build_payment_provider
    ):
        """The failure consumed nothing, so the client's key is still fresh.

        The fake's ``failures`` is a queue that empties, so the second attempt
        gets through - which is the shape a caller needs: a provider outage is
        worth retrying, and a retry must not be refused as a duplicate by a
        dedupe that counted an attempt which never happened.

        **The error is the unavailable one, and that is what makes the retry a
        coherent thing to do.** A refusal would be refused again with the same
        request; an outage is the case the caller is entitled to try again, which
        is why the grade for it is a 503 with a "come back later" meaning rather
        than a 400 telling them to edit the form.
        """
        wallet = build_wallet()
        provider = build_payment_provider(
            failures=[PaymentProviderUnavailableError("the provider is down")]
        )
        service, factory = a_deposit_service(tmp_path, provider)
        seed(factory, wallet, payer)

        with pytest.raises(PaymentProviderUnavailableError):
            service.execute(wallet.wallet_id, Money(5000, NGN), "invoice-7")

        result = service.execute(wallet.wallet_id, Money(5000, NGN), "invoice-7")

        assert result.status is TransactionStatus.PENDING
        assert len(rows(factory, wallet)) == 1


class TestWhereTheMoneyIsNot:
    def test_the_deposit_never_lands_in_a_pot(
        self, tmp_path, build_wallet, payer, build_payment_provider
    ):
        """A pot deposit is a different feature, and it is still held.

        ``POST /wallets/{id}/funds/{name}/deposits`` is on the boundary test's
        held list, and the reason is written there: money arriving into a wallet
        has its destination decided when the collection is opened, whereas potting
        it would mean deciding *after* the money landed which pot it was for - by
        which time the payer has chosen nothing.

        So the row carries no ``fund_id``, and the pot is as untouched as the
        available balance is. Both are asserted, because a use case that routed
        the money into "Locked" would leave the first number right and the second
        wrong.
        """
        wallet = build_wallet(available="1000", locked="4000")
        service, factory = a_deposit_service(tmp_path, build_payment_provider())
        seed(factory, wallet, payer)

        service.execute(wallet.wallet_id, Money(5000, NGN), "invoice-7")

        assert rows(factory, wallet)[0].fund_id is None
        after = stored(factory, wallet)
        assert after.available_balance == Money(1000, NGN)
        assert after.fund_by_name("Locked").balance == Money(Decimal("4000"), NGN)
