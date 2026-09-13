"""``ReconcilePayments``: the job that notices the webhook that never came.

The slice this file tests exists for one sentence in the README's open list - *a
PENDING row whose event never arrives is currently indistinguishable from one
whose event is in flight* - and every test here is a way of asking whether that
is still true. The central one is the first: a deposit whose webhook was lost,
recovered by this job, ending in exactly the state a delivered webhook would have
left.

**Arranged by what the run does with an answer**, because the answers are the
feature. A settled answer is handed to ``SettlePayment``; the other three move
nothing and are reported; and the rows that are never asked about are as much a
part of the design as the ones that are - which is why two tests here assert
against ``provider.lookups`` being *empty* rather than against an outcome.

**Nothing in this file replaces the settler.** The reconciler is given a real one
built on the same database, because "recovered and delivered deposits are
indistinguishable" is a claim about wiring and a fake settler would make it
untestable. Where a test wants to know that the settler ran, it asks the
*store* - the balance, the row status, the queued receipt - the same way the
settle tests do.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from app.application.payments.reconcile_payments import (
    DEFAULT_GRACE,
    DEFAULT_LIMIT,
    ReconcilePayments,
)
from app.application.payments.reconciledPayment import ReconciliationOutcome
from app.application.payments.settle_payment import SettlePayment
from app.application.payments.settledPayment import SettlementOutcome
from app.domain.money.currency import Currency
from app.domain.money.money import Money
from app.domain.money.transaction import Transaction
from app.domain.money.transactionStatus import TransactionStatus
from app.domain.money.transactionType import TransactionType
from app.domain.notifications.notificationKind import NotificationKind
from app.domain.payments.exception import PaymentProviderError
from app.domain.payments.providerAnswer import ProviderAnswer
from app.domain.payments.providerAnswerStatus import ProviderAnswerStatus
from app.domain.payments.providerEvent import ProviderEvent
from app.domain.payments.providerOutcome import ProviderOutcome
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)

NGN = Currency.NGN

#: The moment every run in this file is run *at*, chosen well after the deposits
#: below so that all of them are past the grace window unless a test says
#: otherwise. Passed as ``as_of`` rather than read from a clock, which is the
#: whole reason ``execute`` takes one: a test can put a row inside or outside the
#: window without waiting fifteen minutes, and every test in this file can run at
#: the same instant.
AS_OF = datetime(2026, 1, 2, 12, 0)

#: Where a receipt is addressed, for the one test that is about the receipt. The
#: same address ``test_settle_payment.py`` uses, and passed for the same reason:
#: a use case takes its inputs.
RECIPIENT = "chinedu@example.com"

#: A moment comfortably before ``AS_OF``, and the one every deposit here is made
#: at unless a test is about the window.
EARLIER = datetime(2026, 1, 2, 11, 0)


def a_reconciler(
    tmp_path,
    provider,
    *,
    recipient=None,
    grace=DEFAULT_GRACE,
    limit=DEFAULT_LIMIT,
    name="reconcile.db",
):
    """A real reconciler over a real store, with a real settler.

    Both use cases are built from the *same* factory, which is what
    ``build_reconciler`` does and what makes this file's central claim testable -
    the reconciler asks one database about a row and the settler credits a wallet
    in it. A test that handed in an unrelated settler would be testing a job that
    cannot exist in production.
    """
    factory = SqliteUnitOfWorkFactory(str(tmp_path / name))
    settler = SettlePayment(factory, recipient=recipient)
    return (
        ReconcilePayments(factory, settler, provider, grace=grace, limit=limit),
        factory,
    )


def seed(factory, wallet, *rows):
    """Put a wallet and its ledger rows in the store, in one unit.

    One unit rather than a commit per row, for ``test_settle_payment.py``'s
    reason: ``transactions.wallet_id`` is a real foreign key, so a row cannot
    exist without its wallet and the two saves are one fact.
    """
    uow = factory.start()
    uow.wallets.save(wallet)
    for row in rows:
        uow.transactions.save(row)
    uow.commit()


def available(factory, wallet):
    """The balance the *store* holds for this wallet, read as its owner.

    Through ``get_owned`` with the wallet's own owner, so an assertion about a
    recovered deposit is really checking that the money landed in the wallet its
    row names - rather than in whichever one the test happens to hold.
    """
    uow = factory.start()
    try:
        return uow.wallets.get_owned(wallet.wallet_id, wallet.user_id).available_balance
    finally:
        uow.rollback()


def stored_row(factory, provider_reference):
    uow = factory.start()
    try:
        return uow.transactions.get_by_provider_reference(provider_reference)
    finally:
        uow.rollback()


def pending_receipts(factory):
    uow = factory.start()
    try:
        return uow.notifications.pending()
    finally:
        uow.rollback()


def a_deposit(wallet, amount="5000", reference="dep-1", created_at=EARLIER):
    """A deposit as ``InitiateDeposit`` leaves it: PENDING, provider-named.

    The wallet is deliberately *not* credited, because that is the state this
    whole slice is about - a row saying money was asked for, and a balance saying
    none has arrived. ``provider_reference`` is set because it is the filter's
    other half: a row without one is a payment no provider was ever told about,
    and there is a test below that asserts such a row is never asked about.
    """
    return Transaction(
        wallet_id=wallet.wallet_id,
        type=TransactionType.DEPOSIT,
        amount=Money(Decimal(amount), NGN),
        internal_reference=f"{wallet.wallet_id}.{reference}",
        provider_reference=reference,
        created_at=created_at,
    )


def an_unsponsored_row(wallet, reference="pay-1"):
    """A PENDING row no provider has ever heard of - a CLI withdrawal.

    ``app/presentation/cli.py`` never writes ``provider_reference``, so every
    withdrawal and every plan-run payout leaves a row shaped exactly like this
    one. Asking Paystack about it would be asking about a reference that was
    never sent to anybody, which is why the discovery read carries the
    ``IS NOT NULL`` half; this helper is what makes that half testable.
    """
    return Transaction(
        wallet_id=wallet.wallet_id,
        type=TransactionType.WITHDRAWAL,
        amount=Money(Decimal("1000"), NGN),
        internal_reference=f"{wallet.wallet_id}.{reference}",
        created_at=EARLIER,
    )


def settled_answer(reference="dep-1", amount="5000"):
    """What the provider says when the money is there, in our vocabulary."""
    return ProviderAnswer(
        ProviderAnswerStatus.SETTLED,
        ProviderOutcome(
            event=ProviderEvent.CHARGE_SUCCEEDED,
            reference=reference,
            amount=Money(Decimal(amount), NGN),
        ),
    )


def not_settled_answer():
    return ProviderAnswer(ProviderAnswerStatus.NOT_SETTLED)


def no_such_reference_answer():
    return ProviderAnswer(ProviderAnswerStatus.NO_SUCH_REFERENCE)


# --- the reason this job exists ---------------------------------------------


class TestALostWebhookIsRecovered:
    def test_a_pending_deposit_the_provider_settled_is_credited(
        self, tmp_path, build_wallet, build_payment_provider
    ):
        """**The whole feature, in one test.**

        A deposit whose webhook never arrived: the payer's money has left their
        account, the row says PENDING, and the wallet is at zero. The reconciler
        asks the provider, is told the charge succeeded, and hands that answer to
        the settler - which is the same settler the webhook route uses, so the
        result is the same state the webhook would have produced.
        """
        wallet = build_wallet(available="1000")
        row = a_deposit(wallet)
        provider = build_payment_provider(answers={"dep-1": settled_answer()})
        reconciler, factory = a_reconciler(tmp_path, provider)
        seed(factory, wallet, row)

        report = reconciler.execute(AS_OF)

        assert [one.outcome for one in report.reconciled] == [
            ReconciliationOutcome.SETTLED
        ]
        assert report.moved
        assert available(factory, wallet) == Money(6000, NGN)
        assert stored_row(factory, "dep-1").status is TransactionStatus.SUCCESSFUL

    def test_a_recovered_deposit_queues_the_receipt_a_delivered_one_would(
        self, tmp_path, build_wallet, build_payment_provider
    ):
        """**The strongest assertion this slice can make, and it is free.**

        ``SettlePayment._announce`` queues the receipt, so a payment recovered
        here and one delivered by webhook leave *identical* state - and the way
        that is guaranteed rather than hoped for is that there is one settler.
        The reconciler adds a caller, not a settlement path; a version of this
        file that built its own would pass every balance assertion above and fail
        this one.
        """
        wallet = build_wallet(available="0")
        row = a_deposit(wallet)
        provider = build_payment_provider(answers={"dep-1": settled_answer()})
        reconciler, factory = a_reconciler(tmp_path, provider, recipient=RECIPIENT)
        seed(factory, wallet, row)

        reconciler.execute(AS_OF)

        receipts = pending_receipts(factory)
        assert [one.kind for one in receipts] == [NotificationKind.WALLET_DEPOSIT]

    def test_a_second_run_moves_nothing(
        self, tmp_path, build_wallet, build_payment_provider
    ):
        """**The idempotence that comes free from asking the ledger.**

        The first run leaves the row SUCCESSFUL, and a SUCCESSFUL row is not
        PENDING - so the second run does not even discover it, and the provider
        is not asked a second time. That is what makes this job safe to run on a
        cron with no locking and no claim table: the retry is the next run, and
        the next run asks only about what is still in flight.

        The balance is asserted as well, because "asked nothing" and "moved
        nothing" are different claims and the second is the one that matters.
        """
        wallet = build_wallet(available="0")
        row = a_deposit(wallet)
        provider = build_payment_provider(answers={"dep-1": settled_answer()})
        reconciler, factory = a_reconciler(tmp_path, provider)
        seed(factory, wallet, row)

        reconciler.execute(AS_OF)
        second = reconciler.execute(AS_OF)

        assert second.reconciled == ()
        assert provider.lookups == ["dep-1"]
        assert available(factory, wallet) == Money(5000, NGN)

    def test_it_recovers_a_deposit_for_a_wallet_this_job_never_named(
        self, tmp_path, build_wallet, build_payment_provider
    ):
        """**The actorless test: two owners, one run, each credited to its own.**

        ``TestTheSchedulerIsNotAPrivilegedActor``'s shape one job over, and the
        claim is stronger here. The scheduler mints an executor per plan so the
        wallet can be read as its owner; this job reads *no wallet at all* - it
        takes a reference off a ledger row and hands an outcome to the settler,
        and the settler finds the owner through ``owner_of`` and then
        ``get_owned``. So there is nothing here that could act for nobody even if
        somebody tried to make it: the owner is a fact the row already carries.

        Two wallets, two owners, one run - and each balance moves by its own
        deposit and no more. A design that resolved an actor once and reused it
        would credit both deposits to one wallet, which is the failure this test
        is written to catch.
        """
        first = build_wallet(available="0", user_id=uuid4())
        second = build_wallet(available="0", user_id=uuid4())
        provider = build_payment_provider(
            answers={
                "dep-first": settled_answer("dep-first", "1000"),
                "dep-second": settled_answer("dep-second", "2000"),
            }
        )
        reconciler, factory = a_reconciler(tmp_path, provider)
        seed(factory, first, a_deposit(first, "1000", "dep-first"))
        seed(factory, second, a_deposit(second, "2000", "dep-second"))

        report = reconciler.execute(AS_OF)

        assert len(report.moved) == 2
        assert available(factory, first) == Money(1000, NGN)
        assert available(factory, second) == Money(2000, NGN)


# --- what the provider can say ----------------------------------------------


class TestWhatTheProviderAnswers:
    """Three answers and one non-answer, and only one of them moves anything."""

    def test_a_charge_that_has_not_arrived_leaves_the_row_pending(
        self, tmp_path, build_wallet, build_payment_provider
    ):
        """**The ordinary answer, and the one that must stay quiet.**

        A payer still on the checkout page comes back ``NOT_SETTLED``, on this
        run and on every run until they either pay or give up. Nothing moves and
        the row stays PENDING - which is the correct state, because the money
        genuinely has not arrived.
        """
        wallet = build_wallet(available="1000")
        row = a_deposit(wallet)
        provider = build_payment_provider(answers={"dep-1": not_settled_answer()})
        reconciler, factory = a_reconciler(tmp_path, provider)
        seed(factory, wallet, row)

        report = reconciler.execute(AS_OF)

        assert [one.outcome for one in report.reconciled] == [
            ReconciliationOutcome.NOT_SETTLED
        ]
        assert not report.moved
        assert available(factory, wallet) == Money(1000, NGN)
        assert stored_row(factory, "dep-1").status is TransactionStatus.PENDING

    def test_an_abandoned_checkout_is_reported_and_not_failed(
        self, tmp_path, build_wallet, build_payment_provider
    ):
        """**The report-only policy, asserted where somebody would change it.**

        A checkout Paystack reports as ``abandoned`` will very probably never be
        paid, and marking the row FAILED would be honest about the deposit and
        dangerous about the money: ``SettlePayment`` refuses a
        ``CHARGE_SUCCEEDED`` on a row that is not PENDING, so a row failed a
        minute before its payment arrived would take the payer's money and never
        credit it, with nothing left in the system that could.

        So the row stays PENDING and the run keeps reporting it. That costs a
        lookup every fifteen minutes for ever, which is a small, visible,
        recoverable cost - and the sweep that would resolve it needs a fifth
        ``ProviderEvent`` and an answer to "what does it mean to fail a charge
        that might still arrive". Neither exists yet, and this test is where the
        absence is enforced rather than merely intended.
        """
        wallet = build_wallet(available="1000")
        row = a_deposit(wallet)
        provider = build_payment_provider(answers={"dep-1": not_settled_answer()})
        reconciler, factory = a_reconciler(tmp_path, provider)
        seed(factory, wallet, row)

        reconciler.execute(AS_OF)

        assert stored_row(factory, "dep-1").status is TransactionStatus.PENDING

    def test_a_reference_the_provider_denies_is_the_loudest_line(
        self, tmp_path, build_wallet, build_payment_provider
    ):
        """**Unreachable in a healthy install, which is what makes it an alarm.**

        ``InitiateDeposit`` writes the row only after the provider accepted the
        collection, so a provider that now denies knowing the reference means
        this deployment's rows and its secret key belong to different accounts -
        a staging key against production data, or the reverse. Nothing is
        settled, failed or changed when it arrives; what the report carries is a
        sentence naming the two things to compare.
        """
        wallet = build_wallet(available="1000")
        row = a_deposit(wallet)
        provider = build_payment_provider(
            answers={"dep-1": no_such_reference_answer()}
        )
        reconciler, factory = a_reconciler(tmp_path, provider)
        seed(factory, wallet, row)

        report = reconciler.execute(AS_OF)

        (one,) = report.reconciled
        assert one.outcome is ReconciliationOutcome.NO_SUCH_REFERENCE
        assert "secret key" in one.detail
        assert not one.moved_money
        assert stored_row(factory, "dep-1").status is TransactionStatus.PENDING

    def test_a_provider_that_cannot_be_asked_is_recorded_and_the_run_goes_on(
        self, tmp_path, build_wallet, build_payment_provider
    ):
        """**A third-party failure is per-row; our own data being wrong is not.**

        A provider being briefly unavailable about one reference says nothing
        about the next one, so the failure is recorded against that row and the
        run continues - a job that stopped at the first hiccup would leave every
        row behind it unreconciled, and a cron job that mailed the operator every
        fifteen minutes is a cron job somebody turns off. Decision 24's rule,
        applied to the one job whose whole purpose is noticing things.

        The other two rows settle in the same run, which is the assertion that
        makes this a test about *continuing* rather than about catching.
        """
        wallet = build_wallet(available="0")
        rows = [
            a_deposit(wallet, "1000", "dep-1", EARLIER),
            a_deposit(wallet, "2000", "dep-2", EARLIER + timedelta(minutes=1)),
            a_deposit(wallet, "3000", "dep-3", EARLIER + timedelta(minutes=2)),
        ]
        provider = build_payment_provider(
            answers={
                "dep-1": settled_answer("dep-1", "1000"),
                "dep-2": PaymentProviderError("the provider is unreachable"),
                "dep-3": settled_answer("dep-3", "3000"),
            }
        )
        reconciler, factory = a_reconciler(tmp_path, provider)
        seed(factory, wallet, *rows)

        report = reconciler.execute(AS_OF)

        assert [one.outcome for one in report.reconciled] == [
            ReconciliationOutcome.SETTLED,
            ReconciliationOutcome.FAILED,
            ReconciliationOutcome.SETTLED,
        ]
        assert report.unanswered == (report.reconciled[1],)
        assert "unreachable" in report.reconciled[1].detail
        assert available(factory, wallet) == Money(4000, NGN)
        assert stored_row(factory, "dep-2").status is TransactionStatus.PENDING

    def test_an_amount_that_disagrees_is_settled_by_the_provider_and_refused_by_us(
        self, tmp_path, build_wallet, build_payment_provider
    ):
        """**The row a report exists to surface, and the reason it carries a value.**

        The provider has taken the payer's money and this system has deliberately
        not credited it, because the amount that arrived is not the amount the
        row asked for. Two vocabularies disagree here - ``SETTLED`` describes the
        provider and ``AMOUNT_DISAGREES`` describes us - and a report that
        flattened either into the other would be the one line an operator most
        needs and cannot get.

        ``moved_money`` reads the ``SettledPayment`` rather than the outcome, so
        "the provider settled it" cannot be mistaken for "the wallet was
        credited" by anything downstream. The row stays PENDING, which is what
        leaves it a candidate for every subsequent run.
        """
        wallet = build_wallet(available="0")
        row = a_deposit(wallet, "5000", "dep-1")
        provider = build_payment_provider(
            answers={"dep-1": settled_answer("dep-1", "9999")}
        )
        reconciler, factory = a_reconciler(tmp_path, provider)
        seed(factory, wallet, row)

        report = reconciler.execute(AS_OF)

        (one,) = report.reconciled
        assert one.outcome is ReconciliationOutcome.SETTLED
        assert one.settlement.outcome is SettlementOutcome.AMOUNT_DISAGREES
        assert not one.moved_money
        assert not report.moved
        assert available(factory, wallet) == Money(0, NGN)
        assert stored_row(factory, "dep-1").status is TransactionStatus.PENDING


# --- who is asked, and who is not -------------------------------------------


class TestWhichRowsAreAskedAbout:
    """The half of the design that is about *not* spending a provider call."""

    def test_a_row_with_no_provider_reference_is_never_asked_about(
        self, tmp_path, build_wallet, build_payment_provider
    ):
        """**The filter's second half, and it is load-bearing rather than tidy.**

        The CLI never writes ``provider_reference``, so every withdrawal and
        every plan-run payout leaves a PENDING row that no provider has ever
        heard of. Without the ``IS NOT NULL`` half of the read, this job would
        ask Paystack about references that were never sent to it - and get back
        ``NO_SUCH_REFERENCE``, the alarm this system reserves for a
        misconfigured key, on every withdrawal in the database.

        Asserted against ``lookups`` rather than against an outcome, because the
        claim is that the call was never made.
        """
        wallet = build_wallet(available="5000")
        provider = build_payment_provider()
        reconciler, factory = a_reconciler(tmp_path, provider)
        seed(factory, wallet, an_unsponsored_row(wallet))

        report = reconciler.execute(AS_OF)

        assert report.reconciled == ()
        assert provider.lookups == []

    def test_a_settled_row_is_not_a_candidate(
        self, tmp_path, build_wallet, build_payment_provider
    ):
        """The filter's first half: a reference alone would hand back a finished
        deposit, and asking about one that has already been credited would spend
        a call to be told what the ledger already knows."""
        wallet = build_wallet(available="5000")
        row = a_deposit(wallet)
        row.mark_successful()
        provider = build_payment_provider()
        reconciler, factory = a_reconciler(tmp_path, provider)
        seed(factory, wallet, row)

        reconciler.execute(AS_OF)

        assert provider.lookups == []

    def test_a_row_inside_the_grace_window_is_not_asked_about(
        self, tmp_path, build_wallet, build_payment_provider
    ):
        """**The courtesy, asserted as zero calls rather than as a quiet answer.**

        A payment started a minute ago is almost certainly fine - its webhook is
        in flight or its payer is still on the checkout page - so asking about it
        would spend a provider call to learn nothing. The window is *not* what
        makes the run safe: a settled answer is the only thing that moves
        anything, so a payer mid-checkout is protected by their answer rather
        than by this delay. What the delay buys is the call.

        The count is reported rather than the rows, because the rows are not
        interesting individually and an operator only needs to know that the run
        found something it chose not to ask about yet.
        """
        wallet = build_wallet(available="0")
        row = a_deposit(wallet, created_at=AS_OF - timedelta(minutes=1))
        provider = build_payment_provider(answers={"dep-1": settled_answer()})
        reconciler, factory = a_reconciler(tmp_path, provider)
        seed(factory, wallet, row)

        report = reconciler.execute(AS_OF)

        assert provider.lookups == []
        assert report.reconciled == ()
        assert report.too_young == 1
        assert report.remaining == 0

    def test_a_row_exactly_on_the_edge_of_the_window_is_asked_about(
        self, tmp_path, build_wallet, build_payment_provider
    ):
        """The boundary, stated rather than left to ``<`` versus ``<=``.

        The window is inclusive at its edge - a row exactly ``grace`` old is
        asked about - which mirrors the way ``Confirmation.is_expired`` treats
        its own boundary: the instant a window closes is the instant it stops
        being open. The two must agree with each other rather than each being
        individually defensible, and this is where that is written down.
        """
        wallet = build_wallet(available="0")
        row = a_deposit(wallet, created_at=AS_OF - DEFAULT_GRACE)
        provider = build_payment_provider(answers={"dep-1": settled_answer()})
        reconciler, factory = a_reconciler(tmp_path, provider)
        seed(factory, wallet, row)

        reconciler.execute(AS_OF)

        assert provider.lookups == ["dep-1"]

    def test_the_batch_asks_the_oldest_and_reports_the_rest(
        self, tmp_path, build_wallet, build_payment_provider
    ):
        """**A backlog drains from its oldest end, and a run stays bounded.**

        The natural bound for this job is a count, because its cost is one
        network call per row - where ``RunDuePlans`` bounds a tick by running one
        occurrence per plan. A provider outage that leaves five thousand rows
        PENDING must not make the next run five thousand calls long: it clears a
        batch, reports what it left, and the next run continues from there.

        Oldest first is what stops a row being starved. A backlog that is always
        longer than the batch still drains from the front, so the payment that
        has been stuck longest is always the one asked about next.
        """
        wallet = build_wallet(available="0")
        rows = [
            a_deposit(wallet, "1000", f"dep-{n}", EARLIER + timedelta(minutes=n))
            for n in range(5)
        ]
        provider = build_payment_provider(
            answers={
                "dep-0": settled_answer("dep-0", "1000"),
                "dep-1": settled_answer("dep-1", "1000"),
            }
        )
        reconciler, factory = a_reconciler(tmp_path, provider, limit=2)
        seed(factory, wallet, *rows)

        report = reconciler.execute(AS_OF)

        assert provider.lookups == ["dep-0", "dep-1"]
        assert report.remaining == 3
        assert available(factory, wallet) == Money(2000, NGN)

    def test_a_quiet_run_says_so_without_asking_anything(
        self, tmp_path, build_wallet, build_payment_provider
    ):
        """**Quiet is the good case, and it is the opposite of the scheduler's.**

        An empty report means nothing is in flight, which is the ordinary state
        of a healthy installation - a webhook normally settles a deposit within
        seconds. The scheduler's quiet tick means every plan has already run;
        this job's quiet run means nothing is wrong *and* nothing was missed,
        and a run that always finds work is a run reporting a broken webhook
        endpoint.
        """
        provider = build_payment_provider()
        reconciler, factory = a_reconciler(tmp_path, provider)

        report = reconciler.execute(AS_OF)

        assert report.reconciled == ()
        assert (report.too_young, report.remaining) == (0, 0)
        assert provider.lookups == []
