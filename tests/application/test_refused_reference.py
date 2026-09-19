"""A reference that names a refusal is spent, and replaying it is refused.

``WalletOperation`` deduplicates on ``internal_reference``, and the lookup it
uses returns a row *whatever its status*. So the case worth a file is the one
where the row it finds is ``FAILED``: the first attempt was refused, the refusal
is on the ledger, and a client retrying the same key is asking "did my movement
happen?" - to which "here is the row" is the wrong answer, because the row says
it did not. On an *announced* operation it is worse than wrong: a receipt is
composed from that row, and every sentence a receipt writes is past tense.

**This file sits at the top of ``tests/application`` rather than inside one
operation's directory, and the placement is the argument.** The rule is
``WalletOperation``'s, not any operation's, so a copy of it beside each operation
would be four tests that could drift from one implementation. What differs per
operation is only *how to make it refuse*, and that is the table at the bottom -
which is also what makes it visible that the four are otherwise one case.

**The four, and why exactly four.** A withdrawal, both payouts and a close are
behind a second-level confirmation, and a refusal *spends* that confirmation -
so a retry there meets ``ConfirmationAlreadyUsedError`` first, and
``test_a_refused_confirm_spends_the_request`` in ``test_wallet_service.py``
already covers it. These four never build a confirmation, so the reference is
the only thing that can refuse the retry and the only thing that has to.

That paragraph is about the *service* door, and this file drives operations
directly - so the one test below that wants a ``PENDING`` row uses
``WithdrawMoney`` itself, which is a hold by nature. The confirmation sits in
``WalletService``, above the operation, and nothing here needs to be past it to
reach the rule this file is about.

**The refusals are made at the wallet, not at the door.** An operation that
refuses in its *constructor* - an unknown pot name, say - leaves no ledger row
at all, so there is no reference to replay and nothing this file could assert.
Every builder below therefore arranges a refusal that happens inside ``_apply``,
which is the only kind that is recorded. See ``ReleaseFromFund``'s test file for
that distinction stated as a rule.
"""

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.deposit.deposit_money import DepositMoney
from app.application.fund.deposit_into_fund import DepositIntoFund
from app.application.fund.lock_into_fund import LockIntoFund
from app.application.fund.release_from_fund import ReleaseFromFund
from app.application.withdraw.withdraw_money import WithdrawMoney
from app.domain.money.currency import Currency
from app.domain.money.exception import (
    MoneyError,
    ReferenceAlreadyRefusedError,
)
from app.domain.money.fundKind import FundKind
from app.domain.money.money import Money
from app.domain.money.transactionStatus import TransactionStatus
from app.domain.money.walletStatus import WalletStatus
from app.infrastructure.repositories.in_memory_transaction_repository import (
    InMemoryTransactionRepository,
)

NGN = Currency.NGN

#: The moment every operation in this file is judged at. Fixed rather than "now",
#: like the rest of the suite: nothing here is about a date, and a test that read
#: the clock would have an expectation it could only write by knowing when it ran.
MOMENT = datetime(2026, 1, 1)

#: The key every test here replays. A literal rather than a ``uuid4()``, because
#: the whole point of the file is that the *same* key arrives twice and the test
#: should read that way.
KEY = "attempt-1"


def ngn(amount: str) -> Money:
    return Money(Decimal(amount), NGN)


# --- One builder per operation, each arranging a recorded refusal -------------


def _a_lock_that_is_refused(build_wallet, repository):
    """Locking more than the wallet holds - refused by the wallet, so recorded."""
    wallet = build_wallet(available="1000")
    wallet.open_fund("Vacation", FundKind.PERSONAL, as_of=MOMENT)
    service = LockIntoFund(wallet, repository, "Vacation", MOMENT)
    return lambda reference: service.execute(ngn("2000"), reference)


def _a_release_that_is_refused(build_wallet, repository):
    """Releasing more than the pot holds, for the same reason and the same shape."""
    wallet = build_wallet(available="0")
    pot = wallet.open_fund("Vacation", FundKind.PERSONAL, as_of=MOMENT)
    wallet.deposit_into_fund(pot.fund_id, ngn("1000"), MOMENT)
    service = ReleaseFromFund(wallet, repository, "Vacation", MOMENT)
    return lambda reference: service.execute(ngn("5000"), reference)


def _a_deposit_that_is_refused(build_wallet, repository):
    """A wallet that cannot be credited refuses the deposit it was asked for.

    ``DepositMoney`` is the one operation here with no pot and no date - it is the
    direct-credit door - so the only refusal it has is the wallet's own status.
    """
    wallet = build_wallet(status=WalletStatus.CLOSED)
    service = DepositMoney(wallet, repository)
    return lambda reference: service.execute(ngn("1000"), reference)


def _a_pot_deposit_that_is_refused(build_wallet, repository):
    """The same wallet-status refusal through the pot-scoped door."""
    wallet = build_wallet(status=WalletStatus.CLOSED)
    wallet.status = WalletStatus.ACTIVE
    wallet.open_fund("Vacation", FundKind.PERSONAL, as_of=MOMENT)
    wallet.status = WalletStatus.CLOSED
    service = DepositIntoFund(wallet, repository, "Vacation", MOMENT)
    return lambda reference: service.execute(ngn("1000"), reference)


#: The four unconfirmed operations, each behind a way of making it refuse that
#: the ledger records. The keys are the ids pytest prints, so they are the
#: operation's own name rather than a description of the refusal.
REFUSED = {
    "lock_into_fund": _a_lock_that_is_refused,
    "release_from_fund": _a_release_that_is_refused,
    "deposit": _a_deposit_that_is_refused,
    "deposit_into_fund": _a_pot_deposit_that_is_refused,
}


# --- The rule ----------------------------------------------------------------


@pytest.mark.parametrize("arrange", REFUSED.values(), ids=REFUSED.keys())
def test_a_refused_reference_cannot_be_replayed(build_wallet, arrange):
    """The same key, twice, and the second call is refused rather than answered.

    Three claims, and the middle one is the defect this file exists for. The
    first call refuses - that is the control, and without it the second assertion
    would pass for an operation that refused *everything*. The second call raises
    ``ReferenceAlreadyRefusedError`` rather than returning the FAILED row. And the
    ledger did not grow: the replay wrote nothing, because the refusal happens
    before a ``Transaction`` is ever constructed.

    **The FAILED row is asserted positively on purpose.** A regression that went
    back to returning it would fail the second assertion, and this line is the
    sentence saying why that return was wrong: a FAILED row is a refusal rather
    than an outcome, so handing it back reports a movement that never happened as
    one that did - and on an announced operation, composes a receipt for it.
    Asserting the status rather than the count alone is also what stops the test
    passing if the repository ever stopped recording refusals.
    """
    repository = InMemoryTransactionRepository()
    call = arrange(build_wallet, repository)

    # The refusal itself is not this file's subject - only that it *is* one, and
    # that it is a ``MoneyError``, which is the type ``WalletOperation`` records.
    with pytest.raises(MoneyError):
        call(KEY)

    with pytest.raises(ReferenceAlreadyRefusedError) as refused:
        call(KEY)

    # The message names the key, because the caller's next question is "which
    # reference do I change" - and the answer has to be in the refusal.
    assert KEY in str(refused.value)

    assert len(repository.transactions) == 1
    assert list(repository.transactions.values())[0].status is TransactionStatus.FAILED


def test_a_successful_reference_is_still_answered_with_its_row(build_wallet):
    """The other half, and the reason the fix could not simply refuse every replay.

    Idempotency is a promise this door makes: a client that retries after a lost
    response gets the answer it would have got the first time. That is a
    *successful* replay, and it must keep working - so the fix had to be a
    branch on the row's status rather than a refusal of replays in general. Each
    operation's own file has one of these (``test_replaying_the_same_internal_
    reference_*``); this one is here to sit beside the refusal, so a future
    reader sees the two outcomes of one lookup in one place.
    """
    wallet = build_wallet()
    repository = InMemoryTransactionRepository()
    wallet.open_fund("Vacation", FundKind.PERSONAL, as_of=MOMENT)
    service = LockIntoFund(wallet, repository, "Vacation", MOMENT)

    first = service.execute(ngn("3000"), KEY)
    second = service.execute(ngn("3000"), KEY)

    assert second.transaction_id == first.transaction_id
    # Once, not twice - which is the promise the row is being handed back for.
    assert wallet.fund_by_name("Vacation").balance == ngn("3000")
    assert len(repository.transactions) == 1


def test_a_pending_reference_is_answered_with_its_row_too(build_wallet):
    """A hold is a real dedupe hit, and it is not a refusal.

    ``PENDING`` is the third status a row can carry and the one a replay is most
    likely to meet in practice - a payout whose provider has not reported back.
    The two tests above cover the two ends of the status column; this is the
    middle, and it belongs with them because "the row's status decides" is the
    rule and a rule stated over two of its three cases is a rule with a hole.

    The operation is a **withdrawal** because a withdrawal is a hold by nature -
    it debits and stops, leaving the row PENDING until a provider says otherwise.
    That is the state being asked about, reached by doing the thing that produces
    it rather than by reaching into the class and turning ``settles_immediately``
    off.
    """
    wallet = build_wallet()
    repository = InMemoryTransactionRepository()
    service = WithdrawMoney(wallet, repository)

    first = service.execute(ngn("5000"), KEY)
    second = service.execute(ngn("5000"), KEY)

    assert first.status is TransactionStatus.PENDING
    assert second.transaction_id == first.transaction_id
    # Debited once and held once: the replay was answered, not run. A second run
    # would leave nothing available at all, which is the assertion - the debit is
    # the only place a withdrawal shows up.
    #
    # There is deliberately no ``locked_balance`` line here, and the absence is
    # worth a sentence because it is the trap this test first fell into.
    # ``locked_balance`` is the sum of a wallet's *pots*, and a withdrawal makes
    # none: money leaving for a bank account is out of the owner's reach but it is
    # not reserved for anything, so it is not locked. The hold this test is about
    # is a ``PENDING`` row and a smaller balance, and nothing else.
    assert wallet.available_balance == ngn("5000")
    assert len(repository.transactions) == 1


def test_the_refusal_names_the_operation_it_refused(build_wallet):
    """The message says *what* was refused, not only which key refused it.

    A caller holding one key may have used it for a lock and a release on
    different days - nothing scopes a key to an operation - so "this reference is
    spent" would leave them looking for which attempt spent it. The type is what
    answers that, and it is on the row the replay was turned away from.
    """
    wallet = build_wallet(available="1000")
    repository = InMemoryTransactionRepository()
    wallet.open_fund("Vacation", FundKind.PERSONAL, as_of=MOMENT)
    service = LockIntoFund(wallet, repository, "Vacation", MOMENT)

    with pytest.raises(MoneyError):
        service.execute(ngn("2000"), KEY)

    with pytest.raises(ReferenceAlreadyRefusedError) as refused:
        service.execute(ngn("2000"), KEY)

    assert "lock_funds" in str(refused.value)


def test_a_fresh_reference_is_the_way_to_ask_again(build_wallet):
    """What the refusal tells the caller to do, and that it works.

    The refusal is only useful if there is somewhere to go next, and there is:
    the wallet was never mutated by the refused attempt, so the same call under a
    new key succeeds once the reason for the refusal is gone. Here it is gone
    because the wallet is given the money it was missing.
    """
    wallet = build_wallet(available="1000")
    repository = InMemoryTransactionRepository()
    wallet.open_fund("Vacation", FundKind.PERSONAL, as_of=MOMENT)
    service = LockIntoFund(wallet, repository, "Vacation", MOMENT)

    with pytest.raises(MoneyError):
        service.execute(ngn("2000"), KEY)

    with pytest.raises(ReferenceAlreadyRefusedError):
        service.execute(ngn("2000"), KEY)

    # The refusal changed nothing, which is what makes a retry safe at all.
    assert wallet.available_balance == ngn("1000")
    assert wallet.fund_by_name("Vacation").balance == ngn("0")

    wallet.apply_deposit(ngn("5000"))

    transaction = service.execute(ngn("2000"), str(uuid4()))

    assert transaction.status is TransactionStatus.SUCCESSFUL
    assert wallet.fund_by_name("Vacation").balance == ngn("2000")
    assert wallet.available_balance == ngn("4000")
