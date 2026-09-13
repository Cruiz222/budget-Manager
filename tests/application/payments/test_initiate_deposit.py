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

**One refusal is about the characters in the key itself**, and it is the newest
one in this file - a real provider is what taught it. The reference built here is
also the idempotency key Paystack is handed, and their alphabet is narrower than
a Python string's; see ``TestAKeyTheProviderWouldRefuse`` for the bug that cost.

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

from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.payments.initiate_deposit import InitiateDeposit
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
    DepositAlreadyInitiatedError,
    PayerEmailRefusedError,
    PaymentProviderError,
)
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)
from tests.conftest import TEST_USER_ID

NGN = Currency.NGN
USD = Currency.USD

ACTOR = TEST_USER_ID

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
            failures=[PaymentProviderError("the provider is down")]
        )
        service, factory = a_deposit_service(tmp_path, provider)
        seed(factory, wallet, payer)

        with pytest.raises(PaymentProviderError):
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
        """
        wallet = build_wallet()
        provider = build_payment_provider(
            failures=[PaymentProviderError("the provider is down")]
        )
        service, factory = a_deposit_service(tmp_path, provider)
        seed(factory, wallet, payer)

        with pytest.raises(PaymentProviderError):
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
