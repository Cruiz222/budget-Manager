from app.application.notifications.deliver_notifications import DeliverNotifications
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


def _channel_for(settings: EmailSettings | None, channel=None):
    """The channel to deliver through: the injected one, SMTP, or none at all.

    Shared by both deliverers, and sharing it is the point. A second copy of
    this construction would be a second place to get the adapter's arguments
    wrong - to forget ``starttls``, or to swap the username and the sender - and
    the two queues would then deliver identically-composed messages over
    differently-configured connections, which is the kind of difference nobody
    thinks to look for.

    ``None`` is a value here, not a failure: it is how "this installation has no
    email" reaches a drain as a state it can report. See ``build_deliverer``.
    """
    if channel is not None:
        return channel
    if settings is None:
        return None
    return SmtpNotificationChannel(
        host=settings.host,
        port=settings.port,
        sender=settings.sender,
        username=settings.username,
        password=settings.password,
        starttls=settings.starttls,
    )


def build_wallet_service(
    unit_of_work_factory: UnitOfWorkFactory | None = None,
    settings: EmailSettings | None = None,
) -> WalletService:
    """Composition root: the one place concrete persistence is chosen.

    The application layer only knows the abstract UnitOfWorkFactory; this
    factory is where the SQLite-backed implementation is wired in, writing to
    "budget.db" in the working directory. Tests inject a factory pointed at a
    temp file. Swapping storage happens here and nowhere else.

    ``settings`` is the recipient a receipt is addressed to, and passing it here
    rather than into each operation is deliberate: an operation takes an amount
    and a reference, and adding "and where to send the mail" to ``deposit`` would
    put a delivery concern in the signature of a money move. The address is a
    property of the *installation*, so it is fixed when the service is built.
    ``None`` means no email is configured, which is a normal state and not an
    error - see ``WalletService.ANNOUNCED``.
    """
    return WalletService(
        unit_of_work_factory=unit_of_work_factory or SqliteUnitOfWorkFactory(),
        recipient=settings.recipient if settings is not None else None,
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
    settings: EmailSettings | None = None,
) -> RunDuePlans:
    """Wire up one scheduler tick over the same storage as the wallet service.

    Both use cases must be built from the *same* factory, and that is a
    correctness requirement rather than tidiness. The executor moves money
    through the wallet repositories and records the run through the plan
    repositories, and it relies on all of that landing in one transaction. Give
    it a different factory and it would be writing to a different database
    entirely - the money would move in one place while the plan advanced in
    another.

    It is also the same factory the run's *receipt* is queued through, and that
    matters for the same reason: the receipt is written in the run's own
    transaction, so a factory that did not see the run could not see the receipt
    either. The recipient is the only thing this builder adds to what the
    executor already needed - it changes what the run *says*, never what it does.
    """
    factory = unit_of_work_factory or SqliteUnitOfWorkFactory()
    return RunDuePlans(
        unit_of_work_factory=factory,
        execute_plan_run=ExecutePlanRun(
            unit_of_work_factory=factory,
            recipient=settings.recipient if settings is not None else None,
        ),
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
    return DeliverPendingMessages(
        unit_of_work_factory=unit_of_work_factory or SqliteUnitOfWorkFactory(),
        channel=_channel_for(settings, channel),
    )


def build_notification_deliverer(
    unit_of_work_factory: UnitOfWorkFactory | None = None,
    settings: EmailSettings | None = None,
    channel=None,
) -> DeliverNotifications:
    """Wire up receipt delivery, choosing SMTP when email is configured.

    The mirror of ``build_deliverer`` one queue over, and it is a separate
    function rather than a parameter on that one because the two drains are
    separate classes with separate rules - a receipt has no deadline, so this
    drain never expires anything. Sharing a builder would mean sharing a
    signature that could not describe the difference.

    What *is* shared is the channel, deliberately: both queues hand their
    messages to the same adapter through the same three fields, so an install has
    one SMTP configuration and not two. A user who can receive warnings can
    receive receipts, and a user who cannot receive either has one thing to fix.

    No shared factory is required here either. A receipt is written in its run's
    transaction by whoever caused the run, and this drain only reads and updates
    rows that already exist - so it can safely be built on its own factory
    pointing at the same database, exactly as ``build_notifier`` is.
    """
    return DeliverNotifications(
        unit_of_work_factory=unit_of_work_factory or SqliteUnitOfWorkFactory(),
        channel=_channel_for(settings, channel),
    )
