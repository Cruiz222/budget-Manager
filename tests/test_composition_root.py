from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from app import composition_root
from app.composition_root import (
    build_deliverer,
    build_notification_deliverer,
    build_notifier,
    build_scheduler,
    build_wallet_service,
)
from app.domain.money.currency import Currency
from app.domain.money.money import Money
from app.domain.money.wallet import Wallet
from app.domain.money.walletStatus import WalletStatus
from app.infrastructure.settings import EmailSettings
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)
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
    # and withdraws from it - state persisted across instances.
    service_two = build_wallet_service(unit_of_work_factory=factory, actor=ACTOR)
    service_two.withdraw(
        wallet.wallet_id,
        Money(Decimal("3000"), NGN),
        internal_reference=str(uuid4()),
    )

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
    """
    factory = SqliteUnitOfWorkFactory(str(tmp_path / "compose.db"))
    wallet = build_wallet(locked="10000")
    plan = build_plan(wallet_id=wallet.wallet_id)
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

