from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from app import composition_root
from app.composition_root import build_deliverer, build_notifier, build_wallet_service
from app.domain.money.currency import Currency
from app.domain.money.money import Money
from app.domain.money.wallet import Wallet
from app.domain.money.walletStatus import WalletStatus
from app.infrastructure.notifications.email_settings import EmailSettings
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)

NGN = Currency.NGN


def test_two_service_instances_share_state_over_one_database(tmp_path, build_wallet):
    factory = SqliteUnitOfWorkFactory(str(tmp_path / "compose.db"))

    # Seed a wallet through a unit of work, exactly like the service would.
    seed = factory.start()
    wallet = build_wallet()
    seed.wallets.save(wallet)
    seed.commit()

    service_one = build_wallet_service(unit_of_work_factory=factory)
    service_one.deposit(
        wallet.wallet_id,
        Money(Decimal("5000"), NGN),
        internal_reference=str(uuid4()),
    )

    # A second, independently-constructed service reads the committed deposit
    # and withdraws from it - state persisted across instances.
    service_two = build_wallet_service(unit_of_work_factory=factory)
    service_two.withdraw(
        wallet.wallet_id,
        Money(Decimal("3000"), NGN),
        internal_reference=str(uuid4()),
    )

    read = factory.start()
    try:
        stored = read.wallets.get_by_id(wallet.wallet_id)
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
