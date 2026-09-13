from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from app import composition_root
from app.composition_root import (
    build_deliverer,
    build_initiate_deposit,
    build_notification_deliverer,
    build_notifier,
    build_reconciler,
    build_scheduler,
    build_settler,
    build_wallet_service,
    provider_for,
)
from app.domain.money.confirmationKind import ConfirmationKind
from app.domain.money.currency import Currency
from app.domain.money.money import Money
from app.domain.money.transaction import Transaction
from app.domain.money.transactionType import TransactionType
from app.domain.money.wallet import Wallet
from app.domain.money.walletStatus import WalletStatus
from app.domain.payments.providerAnswer import ProviderAnswer
from app.domain.payments.providerAnswerStatus import ProviderAnswerStatus
from app.domain.payments.providerEvent import ProviderEvent
from app.domain.payments.providerOutcome import ProviderOutcome
from app.domain.planning.instruction import Instruction
from app.domain.planning.plannedAction import PlannedAction
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)
from app.infrastructure.settings import EmailSettings, PaystackSettings
from tests.conftest import TEST_USER_ID

NGN = Currency.NGN

#: The user every service built in this file acts as.
#:
#: A file about *wiring* still has to answer "who?", because every builder now
#: requires it. That is the point of the parameter being required rather than
#: defaulted: the composition root is where a wrong answer would be most
#: expensive and least visible, so it is the place least able to inherit silence.
ACTOR = TEST_USER_ID


def test_two_service_instances_share_state_over_one_database(tmp_path, build_wallet):
    factory = SqliteUnitOfWorkFactory(str(tmp_path / "compose.db"))

    # Seed a wallet through a unit of work, exactly like the service would.
    seed = factory.start()
    wallet = build_wallet()
    seed.wallets.save(wallet)
    seed.commit()

    service_one = build_wallet_service(unit_of_work_factory=factory, actor=ACTOR)
    service_one.deposit(
        wallet.wallet_id,
        Money(Decimal("5000"), NGN),
        internal_reference=str(uuid4()),
    )

    # A second, independently-constructed service reads the committed deposit
    # and takes 3000 out of it - state persisted across instances.
    #
    # It is two calls since the confirmation, and here they are split *between*
    # the two instances on purpose: one records the request and the other answers
    # it. That is a stronger version of what this test has always said. The
    # request had to reach a table for a service that never saw it to be able to
    # carry it out, which is exactly the claim the feature makes - the record
    # lives in the database, not in the terminal that made it.
    service_two = build_wallet_service(unit_of_work_factory=factory, actor=ACTOR)
    requested = service_one.request_confirmation(
        wallet.wallet_id,
        ConfirmationKind.WITHDRAWAL,
        datetime.now(),
        internal_reference=str(uuid4()),
        amount=Money(Decimal("3000"), NGN),
    )
    service_two.confirm(requested.confirmation.confirmation_id, datetime.now())

    read = factory.start()
    try:
        stored = read.wallets.get_owned(wallet.wallet_id, wallet.user_id)
    finally:
        read.rollback()
    assert stored.available_balance == Money(Decimal("12000"), NGN)
    assert stored.locked_balance == Money(Decimal("0"), NGN)


def test_build_notifier_warns_over_the_database_it_was_given(
    tmp_path, build_wallet, build_plan
):
    """The composition root's one job: turn a database path into a working use case.

    The service built here is the one ``plan tick`` uses, so a mistake in the
    wiring - the wrong repository, the wrong factory - surfaces as a warning that
    never arrives rather than as an exception somewhere.
    """
    factory = SqliteUnitOfWorkFactory(str(tmp_path / "compose.db"))

    seed = factory.start()
    try:
        wallet = build_wallet(locked="10000")
        plan = build_plan(wallet_id=wallet.wallet_id, anchor=datetime(2026, 3, 2, 12, 0))
        seed.wallets.save(wallet)
        seed.plans.save(plan)
        seed.commit()
    except BaseException:
        seed.rollback()
        raise

    raised = build_notifier(unit_of_work_factory=factory).execute(
        datetime(2026, 3, 2, 11, 30)
    )

    assert len(raised) == 1
    assert raised[0].plan_id == plan.plan_id


def test_build_notifier_over_an_empty_database_warns_about_nothing(tmp_path):
    """An empty database is the ordinary state of a fresh install, not an error."""
    factory = SqliteUnitOfWorkFactory(str(tmp_path / "empty.db"))

    assert build_notifier(unit_of_work_factory=factory).execute(
        datetime(2026, 3, 2, 11, 30)
    ) == []


def test_build_deliverer_without_settings_has_nowhere_to_send(tmp_path):
    """The unconfigured install, and the reason the channel is ``None`` and not a stub.

    A deliverer handed a channel that always fails would fill the log with
    failures on a machine that has simply never been set up for email. ``None``
    is the honest answer: there is no channel, and the use case reports that
    differently from a delivery that was tried and failed.
    """
    factory = SqliteUnitOfWorkFactory(str(tmp_path / "compose.db"))

    deliverer = build_deliverer(unit_of_work_factory=factory, settings=None)

    assert deliverer.execute(datetime(2026, 3, 2, 11, 30)).is_quiet is True


def test_build_deliverer_turns_settings_into_an_smtp_channel(tmp_path, monkeypatch):
    """The port meets the adapter here and nowhere else.

    Asserting on the constructor arguments rather than on the channel's class:
    what a mistake in this wiring would get wrong is a *value* - the port, the
    sender, the timeout - and a test that only checked the type would pass with
    every one of them scrambled.
    """
    captured = {}

    class RecordingChannel:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def send(self, message):  # pragma: no cover - never called here
            raise AssertionError("the composition root must not send anything")

    monkeypatch.setattr(
        composition_root, "SmtpNotificationChannel", RecordingChannel
    )
    settings = EmailSettings(
        host="smtp.example.com",
        port=2525,
        sender="alerts@example.com",
        recipient="chinedu@example.com",
        username="me@example.com",
        password="hunter2",
        starttls=False,
    )

    build_deliverer(
        unit_of_work_factory=SqliteUnitOfWorkFactory(str(tmp_path / "compose.db")),
        settings=settings,
    )

    assert captured == {
        "host": "smtp.example.com",
        "port": 2525,
        "sender": "alerts@example.com",
        "username": "me@example.com",
        "password": "hunter2",
        "starttls": False,
    }


def test_an_injected_channel_beats_the_settings(tmp_path, monkeypatch, build_channel):
    """The seam the whole test suite rests on: a fake is never second-guessed.

    The settings here are *complete*, so a build that ignored the injected
    channel would quietly construct a real SMTP adapter and the suite would go
    back to depending on a network. The exploding stand-in is what makes that
    difference visible rather than silent.
    """

    class NeverBuilt:
        def __init__(self, **kwargs):  # pragma: no cover - must not be reached
            raise AssertionError("settings must not be used when a channel is given")

    monkeypatch.setattr(composition_root, "SmtpNotificationChannel", NeverBuilt)
    channel = build_channel()

    deliverer = build_deliverer(
        unit_of_work_factory=SqliteUnitOfWorkFactory(str(tmp_path / "compose.db")),
        settings=EmailSettings(
            host="smtp.example.com",
            port=587,
            sender="alerts@example.com",
            recipient="chinedu@example.com",
        ),
        channel=channel,
    )

    assert deliverer.execute(datetime(2026, 3, 2, 11, 30)).is_quiet is True
    assert channel.attempts == []


# --- the receipt queue ------------------------------------------------------


def test_build_notification_deliverer_without_settings_has_nowhere_to_send(tmp_path):
    """The same unconfigured state as the warning queue, one queue over.

    ``None`` rather than a channel that always fails: an install that has never
    been set up for email should accumulate receipts, not fill a log with
    connection errors. And nothing ages out while it waits - see
    ``DeliverNotifications``.
    """
    factory = SqliteUnitOfWorkFactory(str(tmp_path / "compose.db"))

    deliverer = build_notification_deliverer(
        unit_of_work_factory=factory, settings=None
    )

    assert deliverer.execute(datetime(2026, 3, 2, 11, 30)).is_quiet is True


def test_both_deliverers_build_the_same_smtp_channel(tmp_path, monkeypatch):
    """One SMTP configuration for both queues, asserted rather than assumed.

    The two builders share ``_channel_for``, and that sharing is the design: a
    user who can receive warnings can receive receipts, and a user who cannot
    has one thing to fix. Asserting both call *sites* - not just the helper -
    is what catches the receipt drain quietly gaining its own adapter with a
    field scrambled.
    """
    built = []

    class RecordingChannel:
        def __init__(self, **kwargs):
            built.append(kwargs)

        def send(self, message):  # pragma: no cover - never called here
            raise AssertionError("the composition root must not send anything")

    monkeypatch.setattr(
        composition_root, "SmtpNotificationChannel", RecordingChannel
    )
    settings = EmailSettings(
        host="smtp.example.com",
        port=2525,
        sender="alerts@example.com",
        recipient="chinedu@example.com",
        username="me@example.com",
        password="hunter2",
        starttls=False,
    )
    factory = SqliteUnitOfWorkFactory(str(tmp_path / "compose.db"))

    build_deliverer(unit_of_work_factory=factory, settings=settings)
    build_notification_deliverer(unit_of_work_factory=factory, settings=settings)

    assert len(built) == 2
    assert built[0] == built[1]
    assert built[1]["host"] == "smtp.example.com"
    assert built[1]["port"] == 2525
    assert built[1]["starttls"] is False


def test_an_injected_channel_beats_the_settings_for_receipts(
    tmp_path, monkeypatch, build_channel
):
    """The receipt queue's half of the seam that keeps the suite off the network.

    ``deliver_notifications`` is drained by ``plan tick`` and by every wallet
    command, so a build here that ignored the injected channel would open sockets
    from the CLI tests as well as from these.
    """

    class NeverBuilt:
        def __init__(self, **kwargs):  # pragma: no cover - must not be reached
            raise AssertionError("settings must not be used when a channel is given")

    monkeypatch.setattr(composition_root, "SmtpNotificationChannel", NeverBuilt)
    channel = build_channel()

    deliverer = build_notification_deliverer(
        unit_of_work_factory=SqliteUnitOfWorkFactory(str(tmp_path / "compose.db")),
        settings=EmailSettings(
            host="smtp.example.com",
            port=587,
            sender="alerts@example.com",
            recipient="chinedu@example.com",
        ),
        channel=channel,
    )

    assert deliverer.execute(datetime(2026, 3, 2, 11, 30)).is_quiet is True
    assert channel.attempts == []


def test_build_wallet_service_addresses_receipts_to_the_configured_recipient(
    tmp_path, build_wallet
):
    """The address is an installation's, fixed when the service is built.

    It is deliberately not a parameter of ``deposit``: adding "and where to send
    the mail" to a money move's signature would put a delivery concern inside a
    balance change. So the wiring is worth asserting here - a mistake would show
    up as receipts addressed to nobody, or to the sender.
    """
    factory = SqliteUnitOfWorkFactory(str(tmp_path / "compose.db"))
    wallet = build_wallet()
    seed = factory.start()
    seed.wallets.save(wallet)
    seed.commit()
    service = build_wallet_service(
        unit_of_work_factory=factory,
        actor=ACTOR,
        settings=EmailSettings(
            host="smtp.example.com",
            port=587,
            sender="alerts@example.com",
            recipient="chinedu@example.com",
        ),
    )

    service.deposit(
        wallet.wallet_id,
        Money(Decimal("5000"), NGN),
        internal_reference=str(uuid4()),
    )

    read = factory.start()
    try:
        queued = read.notifications.pending()
    finally:
        read.rollback()
    assert len(queued) == 1
    assert queued[0].recipient == "chinedu@example.com"


def test_build_wallet_service_without_settings_is_silent_and_still_pays(
    tmp_path, build_wallet
):
    factory = SqliteUnitOfWorkFactory(str(tmp_path / "compose.db"))
    wallet = build_wallet()
    seed = factory.start()
    seed.wallets.save(wallet)
    seed.commit()
    service = build_wallet_service(
        unit_of_work_factory=factory, settings=None, actor=ACTOR
    )

    service.deposit(
        wallet.wallet_id,
        Money(Decimal("5000"), NGN),
        internal_reference=str(uuid4()),
    )

    read = factory.start()
    try:
        assert read.notifications.pending() == []
        stored = read.wallets.get_owned(wallet.wallet_id, wallet.user_id)
    finally:
        read.rollback()
    assert stored.available_balance == Money(Decimal("15000"), NGN)


def test_build_scheduler_addresses_run_receipts_to_the_configured_recipient(
    tmp_path, build_wallet, build_plan
):
    """The tick's receipt, wired the same way - and through the *same* factory.

    The shared factory is what makes the receipt land in the run's own
    transaction, so this is not just about the address: a scheduler built with a
    different factory would queue its receipts in a different database, and the
    money would move without them.

    **The plan is a release plan, and Phase 2b is why.** This used the fixture's
    default payout plan and counted one queued receipt. A payout's row is left
    PENDING now, because its money is bound for a bank account nothing here has
    contacted, and a run whose rows have not settled queues nothing - so the
    default plan would count zero and this test would be asserting the silence
    rather than the wiring it exists to check. A release settles where this
    system can see it, so the receipt still fires and the wiring is still on
    trial. The scheduler is not being told anything about settlement: this is a
    property of the plan handed to it, not of the builder.
    """
    factory = SqliteUnitOfWorkFactory(str(tmp_path / "compose.db"))
    wallet = build_wallet(locked="10000")
    plan = build_plan(
        wallet_id=wallet.wallet_id,
        instructions=(
            Instruction(
                action=PlannedAction.RELEASE,
                amount=Money(Decimal("2000"), NGN),
                label="emergency",
            ),
        ),
        # A release plan must name the date it ends - ``SavingsPlan`` refuses one
        # without, because a release is irreversible and "irreversible until the
        # set date" only means something if there is a set date. Note this is a
        # property of the *plan*, not of the builder being tested here: the
        # scheduler is told nothing about settlement, and this test's subject is
        # the wiring, not the plan.
        ends_on=date(2027, 1, 1),
    )
    seed = factory.start()
    seed.wallets.save(wallet)
    seed.plans.save(plan)
    seed.commit()
    scheduler = build_scheduler(
        unit_of_work_factory=factory,
        settings=EmailSettings(
            host="smtp.example.com",
            port=587,
            sender="alerts@example.com",
            recipient="chinedu@example.com",
        ),
    )

    runs = scheduler.execute(datetime(2026, 1, 1))

    assert [run.status.value for run in runs] == ["succeeded"]
    read = factory.start()
    try:
        queued = read.notifications.pending()
    finally:
        read.rollback()
    assert len(queued) == 1
    assert queued[0].recipient == "chinedu@example.com"
    assert queued[0].subject_id == plan.plan_id


def test_build_scheduler_runs_a_plan_whose_owner_it_was_never_told(
    tmp_path, build_wallet, build_plan, stranger
):
    """The wiring claim behind "the scheduler is not a privileged actor".

    ``build_scheduler`` is the one builder here that takes no ``actor``, and this
    is what its absence has to mean in practice: the tick runs a plan belonging
    to a user nothing in this file ever named, because it learns the owner from
    the plan and builds an executor for that owner. If the builder instead held
    one executor - or passed a fixed actor - the run would fail to find the plan
    or the wallet, and it would fail here.

    Note this is a *wiring* test and not a repeat of the scheduler's own suite:
    what it pins is that the composition root passes a builder rather than a
    built object. That distinction is invisible in ``RunDuePlans`` itself once the
    lambda is in place, which is exactly why it is worth an assertion where the
    lambda is written.
    """
    factory = SqliteUnitOfWorkFactory(str(tmp_path / "compose.db"))
    wallet = build_wallet(locked="10000", user_id=stranger)
    plan = build_plan(wallet_id=wallet.wallet_id, user_id=stranger)
    seed = factory.start()
    seed.wallets.save(wallet)
    seed.plans.save(plan)
    seed.commit()
    scheduler = build_scheduler(unit_of_work_factory=factory)

    runs = scheduler.execute(datetime(2026, 1, 1))

    assert [run.status.value for run in runs] == ["succeeded"]
    read = factory.start()
    try:
        stored = read.wallets.get_owned(wallet.wallet_id, stranger)
    finally:
        read.rollback()
    assert stored.locked_balance == Money(Decimal("8000"), NGN)


# --- payments ---------------------------------------------------------------
#
# The same three shapes as the notification side, one direction of money over,
# and they are here rather than in the payments packages for the same reason the
# channel tests above are here: what is being checked is *wiring* - which adapter
# meets which port, and with which arguments - and wiring has exactly one home.


#: A key that is not a credential. Shaped like ``TEST_PAYSTACK_SECRET`` so that a
#: test value is recognisable as one, and deliberately not imported from the
#: suite's constants: this file is about what a *settings object* carries into an
#: adapter, and reusing the fake's key would make the two indistinguishable.
SECRET = "sk_test_" + "a" * 32


def a_pending_deposit(wallet, amount="5000", reference="dep-1") -> Transaction:
    """A deposit row as ``InitiateDeposit`` leaves it: asked for, not arrived.

    Built by hand rather than through the deposit route, because what is under
    test in this file is which *adapter* a builder chose - and reaching that
    through an HTTP client would make this file's failure modes indistinguishable
    from the deposit suite's.
    """
    return Transaction(
        wallet_id=wallet.wallet_id,
        type=TransactionType.DEPOSIT,
        amount=Money(Decimal(amount), NGN),
        internal_reference=f"{wallet.wallet_id}.{reference}",
        provider_reference=reference,
    )


def a_charge(row: Transaction) -> ProviderOutcome:
    """The event a provider sends when the payer's card was charged.

    For the row's own reference and the row's own amount, so that the two
    agreement rules ``SettlePayment`` applies are satisfied by construction and
    the test is about the wiring rather than about them.
    """
    return ProviderOutcome(
        event=ProviderEvent.CHARGE_SUCCEEDED,
        reference=row.provider_reference,
        amount=row.amount,
    )


def test_provider_for_without_settings_is_no_provider():
    """The unconfigured install, and it is ``None`` rather than a stub.

    A provider that always failed would make every deposit attempt look like a
    provider outage on a machine that has simply never been set up to take
    payments. ``None`` is the honest answer, and it is a *value*: the caller
    checks it and answers 503, which is a state rather than an error.
    """
    assert provider_for(None) is None


def test_provider_for_turns_settings_into_paystack(monkeypatch):
    """The port meets the adapter here and nowhere else.

    Asserting on the constructor arguments rather than on the class, for the
    reason the channel test above does: the mistake this wiring could make is a
    *value*, and one field is all there is to get wrong.
    """
    captured = {}

    class RecordingProvider:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(
        composition_root, "PaystackPaymentProvider", RecordingProvider
    )

    provider_for(PaystackSettings(secret_key=SECRET))

    assert captured == {"secret_key": SECRET}


def test_an_injected_provider_beats_the_settings(monkeypatch, build_payment_provider):
    """The seam that keeps this suite off the network, stated as a precedence.

    The settings here are complete, so a build that ignored the injected provider
    would quietly construct a real adapter - and the next test to open a
    collection would make an HTTP call. The exploding stand-in is what turns that
    from a silent change into a failure.
    """

    class NeverBuilt:
        def __init__(self, **kwargs):  # pragma: no cover - must not be reached
            raise AssertionError("settings must not be used when a provider is given")

    monkeypatch.setattr(composition_root, "PaystackPaymentProvider", NeverBuilt)
    provider = build_payment_provider()

    assert provider_for(PaystackSettings(secret_key=SECRET), provider) is provider


def test_build_initiate_deposit_acts_for_the_actor_it_was_given(
    tmp_path, build_user, build_wallet, build_payment_provider
):
    """Both halves of the wiring, asserted through one call that uses both.

    The provider is proven injected by the fake's own record of what it was
    asked, and the actor is proven by *whose address* that call carried: the
    email comes from ``uow.users.get_by_id(self._actor)``, so a builder that
    dropped the actor would not reach this line at all. That is the property
    worth a test here, because it is the one a wrong builder would break
    silently - a use case built for nobody would fail on a lookup, and a use case
    built for the wrong person would send a real payer's address to a third
    party.
    """
    factory = SqliteUnitOfWorkFactory(str(tmp_path / "compose.db"))
    wallet = build_wallet()
    user = build_user(email="chinedu@example.com")
    seed = factory.start()
    seed.users.save(user)
    seed.wallets.save(wallet)
    seed.commit()
    provider = build_payment_provider()

    deposits = build_initiate_deposit(
        unit_of_work_factory=factory,
        settings=PaystackSettings(secret_key=SECRET),
        actor=ACTOR,
        provider=provider,
    )
    deposits.execute(wallet.wallet_id, Money(Decimal("5000"), NGN), "invoice-7")

    assert provider.requests[0]["email"] == "chinedu@example.com"


def test_build_settler_addresses_the_receipt_to_the_configured_recipient(
    tmp_path, build_wallet
):
    """``settings`` here is mail, and this is what proves the builder reads it.

    Not ``PaystackSettings``, which is the one thing about this signature worth
    noticing: the money arrives through the payment provider and the receipt goes
    out through the mail one, so the settle builder is told the address receipts
    are addressed *to* and nothing about how payments are verified.

    Three of the four fields below are never read, and writing them anyway is the
    honest version: ``EmailSettings`` has no defaults for ``host``, ``port`` and
    ``sender`` **because a setting with a default is a setting somebody deploys
    without noticing**, so a test that wanted to name only the recipient would be
    asking for exactly the wrong convenience. What the builder does with the
    unread three is nothing - it takes a whole configuration or none.
    """
    factory = SqliteUnitOfWorkFactory(str(tmp_path / "compose.db"))
    wallet = build_wallet()
    row = a_pending_deposit(wallet)
    seed = factory.start()
    seed.wallets.save(wallet)
    seed.transactions.save(row)
    seed.commit()

    settler = build_settler(
        unit_of_work_factory=factory,
        settings=EmailSettings(
            host="smtp.example.com",
            port=587,
            sender="alerts@example.com",
            recipient="chinedu@example.com",
        ),
    )
    settled = settler.settle(a_charge(row))

    assert settled.outcome.value == "deposit_credited"
    read = factory.start()
    try:
        queued = read.notifications.pending()
    finally:
        read.rollback()
    assert [notification.recipient for notification in queued] == [
        "chinedu@example.com"
    ]


def test_build_settler_without_settings_still_settles_and_says_nothing(
    tmp_path, build_wallet
):
    """The mirror of ``build_wallet_service``'s silent install, and the same claim.

    A fresh installation has no mail account and still has to be able to take
    money. So the money moves, the row settles, and the only thing missing is the
    sentence telling the owner about it - which is why the address is composed
    into the notification rather than consulted before the credit.
    """
    factory = SqliteUnitOfWorkFactory(str(tmp_path / "compose.db"))
    wallet = build_wallet()
    row = a_pending_deposit(wallet)
    seed = factory.start()
    seed.wallets.save(wallet)
    seed.transactions.save(row)
    seed.commit()

    settler = build_settler(unit_of_work_factory=factory, settings=None)
    settled = settler.settle(a_charge(row))

    assert settled.outcome.value == "deposit_credited"
    read = factory.start()
    try:
        assert read.notifications.pending() == []
        stored = read.wallets.get_owned(wallet.wallet_id, wallet.user_id)
    finally:
        read.rollback()
    assert stored.available_balance == Money(Decimal("15000"), NGN)


# --- the reconciler ---------------------------------------------------------


def a_settled_answer_for(row: Transaction) -> ProviderAnswer:
    """The provider's answer to "what became of this?", saying the money is there."""
    return ProviderAnswer(ProviderAnswerStatus.SETTLED, a_charge(row))


def test_build_reconciler_recovers_a_lost_payment_over_the_database_it_was_given(
    tmp_path, build_wallet, build_payment_provider
):
    """**Both settings objects, and both proven by one call.**

    This is the only builder in the file that names mail *and* payments, and each
    half is asserted the way it would fail silently. The payment half is proven by
    the fake's record of what it was asked - the reference it looked up - and the
    mail half by *where the receipt went*, which is the one thing that can only
    have come from ``EmailSettings``.

    The deposit is recovered, which is the claim in miniature: a row nothing
    settled, credited because the reconciler asked and the settler - the same
    settler the webhook route uses - applied the answer.
    """
    factory = SqliteUnitOfWorkFactory(str(tmp_path / "compose.db"))
    wallet = build_wallet(available="0")
    row = a_pending_deposit(wallet)
    seed = factory.start()
    seed.wallets.save(wallet)
    seed.transactions.save(row)
    seed.commit()
    provider = build_payment_provider(
        answers={"dep-1": a_settled_answer_for(row)}
    )

    reconciler = build_reconciler(
        unit_of_work_factory=factory,
        settings=EmailSettings(
            host="smtp.example.com",
            port=587,
            sender="alerts@example.com",
            recipient="chinedu@example.com",
        ),
        paystack=PaystackSettings(secret_key=SECRET),
        provider=provider,
    )
    report = reconciler.execute(datetime.now() + timedelta(hours=1))

    assert provider.lookups == ["dep-1"]
    assert report.moved
    read = factory.start()
    try:
        stored = read.wallets.get_owned(wallet.wallet_id, wallet.user_id)
        queued = read.notifications.pending()
    finally:
        read.rollback()
    assert stored.available_balance == Money(Decimal("5000"), NGN)
    assert [notification.recipient for notification in queued] == [
        "chinedu@example.com"
    ]


def test_build_reconciler_without_settings_still_recovers_and_says_nothing(
    tmp_path, build_wallet, build_payment_provider
):
    """The silent install, one job over: the money moves and only the sentence
    about it is missing. A fresh deployment has no mail account and must still be
    able to notice a payment that arrived."""
    factory = SqliteUnitOfWorkFactory(str(tmp_path / "compose.db"))
    wallet = build_wallet(available="0")
    row = a_pending_deposit(wallet)
    seed = factory.start()
    seed.wallets.save(wallet)
    seed.transactions.save(row)
    seed.commit()
    provider = build_payment_provider(
        answers={"dep-1": a_settled_answer_for(row)}
    )

    reconciler = build_reconciler(
        unit_of_work_factory=factory, settings=None, provider=provider
    )
    reconciler.execute(datetime.now() + timedelta(hours=1))

    read = factory.start()
    try:
        assert read.notifications.pending() == []
        stored = read.wallets.get_owned(wallet.wallet_id, wallet.user_id)
    finally:
        read.rollback()
    assert stored.available_balance == Money(Decimal("5000"), NGN)

