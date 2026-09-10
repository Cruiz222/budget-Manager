from app.application.notifications.deliver_pending_messages import (
    DeliverPendingMessages,
)
from app.application.plan_service import PlanService
from app.application.planning.execute_plan_run import ExecutePlanRun
from app.application.planning.notify_upcoming_runs import NotifyUpcomingRuns
from app.application.planning.run_due_plans import RunDuePlans
from app.application.unit_of_work import UnitOfWorkFactory
from app.application.wallet_service import WalletService
from app.infrastructure.notifications.email_settings import EmailSettings
from app.infrastructure.notifications.smtp_notification_channel import (
    SmtpNotificationChannel,
)
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)


def build_wallet_service(
    unit_of_work_factory: UnitOfWorkFactory | None = None,
) -> WalletService:
    """Composition root: the one place concrete persistence is chosen.

    The application layer only knows the abstract UnitOfWorkFactory; this
    factory is where the SQLite-backed implementation is wired in, writing to
    "budget.db" in the working directory. Tests inject a factory pointed at a
    temp file. Swapping storage happens here and nowhere else.
    """
    return WalletService(
        unit_of_work_factory=unit_of_work_factory or SqliteUnitOfWorkFactory()
    )


def build_plan_service(
    unit_of_work_factory: UnitOfWorkFactory | None = None,
) -> PlanService:
    """Wire up the plan use cases over the same storage as everything else.

    Note the contrast with ``build_scheduler`` below. That one *requires* one
    factory to be shared across two use cases, because a single run must be one
    transaction. This one does not: every ``PlanService`` method opens and closes
    its own unit, so a separate factory pointing at the same database file is
    perfectly correct - it is a different transaction by design.

    The distinction is worth holding onto: sharing a factory is only load-bearing
    when two use cases must land in the *same* transaction. Everywhere else, one
    factory per service is the clearer wiring, because it makes the transaction
    boundaries read off the code instead of having to be remembered.
    """
    return PlanService(
        unit_of_work_factory=unit_of_work_factory or SqliteUnitOfWorkFactory()
    )


def build_scheduler(
    unit_of_work_factory: UnitOfWorkFactory | None = None,
) -> RunDuePlans:
    """Wire up one scheduler tick over the same storage as the wallet service.

    Both use cases must be built from the *same* factory, and that is a
    correctness requirement rather than tidiness. The executor moves money
    through the wallet repositories and records the run through the plan
    repositories, and it relies on all of that landing in one transaction. Give
    it a different factory and it would be writing to a different database
    entirely - the money would move in one place while the plan advanced in
    another.
    """
    factory = unit_of_work_factory or SqliteUnitOfWorkFactory()
    return RunDuePlans(
        unit_of_work_factory=factory,
        execute_plan_run=ExecutePlanRun(unit_of_work_factory=factory),
    )


def build_notifier(
    unit_of_work_factory: UnitOfWorkFactory | None = None,
    settings: EmailSettings | None = None,
) -> NotifyUpcomingRuns:
    """Wire up the pre-payout warning over the same storage as everything else.

    No shared factory is required here, unlike ``build_scheduler`` above. That
    one needs its two use cases on the *same* factory because one plan run must
    be one transaction; a warning shares no fact with a run, so there is nothing
    for it to commit alongside. One factory per service is therefore the clearer
    wiring - the same reasoning ``build_plan_service`` gives, and the reason the
    distinction is worth stating twice: sharing a factory is load-bearing in
    exactly one place, and pretending otherwise everywhere would make it hard to
    see where it actually matters.

    ``settings`` is passed in rather than read here, because the composition root
    takes its inputs as arguments like everything else and only
    ``email_settings`` reads the environment. ``None`` means this install has no
    notification address: the warning is still raised and still recorded, and
    simply has nowhere to be sent.
    """
    return NotifyUpcomingRuns(
        unit_of_work_factory=unit_of_work_factory or SqliteUnitOfWorkFactory(),
        recipient=settings.recipient if settings is not None else None,
    )


def build_deliverer(
    unit_of_work_factory: UnitOfWorkFactory | None = None,
    settings: EmailSettings | None = None,
    channel=None,
) -> DeliverPendingMessages:
    """Wire up outbox delivery, choosing SMTP when email is configured.

    This is where the abstract ``NotificationChannel`` port meets the concrete
    SMTP adapter, and it is the only place in the application that names one. An
    unconfigured install gets ``None`` rather than a channel that cannot work, so
    that "there is nowhere to send this" is a state the use case can see and
    report, instead of a connection error it has to interpret.

    ``channel`` lets a test inject a fake and is why no test in this suite opens
    a socket. It takes precedence when given.
    """
    if channel is None and settings is not None:
        channel = SmtpNotificationChannel(
            host=settings.host,
            port=settings.port,
            sender=settings.sender,
            username=settings.username,
            password=settings.password,
            starttls=settings.starttls,
        )
    return DeliverPendingMessages(
        unit_of_work_factory=unit_of_work_factory or SqliteUnitOfWorkFactory(),
        channel=channel,
    )
