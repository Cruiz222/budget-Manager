from uuid import UUID

from app.application.identity.log_in import LogIn
from app.application.identity.log_out import LogOut
from app.application.identity.resolve_actor import ResolveActorFromSession
from app.application.identity.sign_up import SignUp
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
from app.domain.identity.password_hasher import PasswordHasher
from app.infrastructure.security.argon2_password_hasher import Argon2PasswordHasher
from app.infrastructure.settings import EmailSettings
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
    *,
    actor: UUID,
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

    ``actor`` is who the service acts for, and it is required and keyword-only
    for the same class of reason as ``settings`` - fixed at construction because
    it is a property of the caller, not of the call. A CLI command, an HTTP
    request or a background job each build one service and use it for one
    person. Note this is the builder where the two kinds of fixity differ in
    weight: a wrong ``settings`` sends mail to the wrong address, whereas a wrong
    ``actor`` is not a mistake that can be made quietly at all - a service built
    for the wrong user reads that user's wallets and *fails* to read anyone
    else's, so the error shows up as a missing wallet rather than as a leak.
    """
    return WalletService(
        unit_of_work_factory=unit_of_work_factory or SqliteUnitOfWorkFactory(),
        recipient=settings.recipient if settings is not None else None,
        actor=actor,
    )


def build_plan_service(
    unit_of_work_factory: UnitOfWorkFactory | None = None,
    *,
    actor: UUID,
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

    ``actor`` scopes plans as well as wallets here, so this is the builder that
    decides whose plans exist as far as the caller is concerned - another user's
    plan is not refused, it is a plan that was never created.
    """
    return PlanService(
        unit_of_work_factory=unit_of_work_factory or SqliteUnitOfWorkFactory(),
        actor=actor,
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

    **There is no actor parameter, and its absence is the design.** Every other
    builder here takes one because the thing it builds serves exactly one
    person. A tick serves everyone with a plan and belongs to no one, so the
    question "who is acting?" has no answer at this level - and rather than
    invent an answer (a system user, a skipped check, an admin flag) this
    builder passes a *closure* that answers it per plan, with the owner the plan
    itself carries:

        lambda user_id: ExecutePlanRun(factory, actor=user_id, ...)

    The executor is therefore still built once per user, one line down, by code
    that knows nothing about scheduling. That is what keeps the scheduler free
    of privilege instead of merely free of the word "bypass": there is no
    executor here to grant authority to.
    """
    factory = unit_of_work_factory or SqliteUnitOfWorkFactory()
    recipient = settings.recipient if settings is not None else None
    return RunDuePlans(
        unit_of_work_factory=factory,
        build_execute_plan_run=lambda user_id: ExecutePlanRun(
            unit_of_work_factory=factory,
            actor=user_id,
            recipient=recipient,
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
    ``settings`` reads the environment. ``None`` means this install has no
    notification address: the warning is still raised and still recorded, and
    simply has nowhere to be sent.

    **This is the one builder with no actor, and the difference is real rather
    than an oversight.** Every other builder here constructs something that
    reads a wallet, and so has to be told whose. ``NotifyUpcomingRuns`` reads
    plans by status and writes notices - it touches no wallet at all, and its
    docstring records that as a structural guarantee rather than a habit. Taking
    an actor it could not use would be a parameter that only ever says "trust
    me", and the value of every *real* actor parameter in this file comes from
    the fact that it is load-bearing. The day a warning wants a balance to quote
    in its words, that is a signature change here and in its constructor - and
    that is the right price, because it is also the day the class stops being
    able to make the guarantee it currently makes.
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


# --- Identity: who is asking, before anything else can be asked --------------
#
# Read last in this file and needed first everywhere else, which is why the group
# is marked rather than interleaved. Nothing above can be called until one of
# these has produced an actor: ``build_wallet_service`` and ``build_plan_service``
# both demand a ``UUID`` that only ``LogIn`` and ``build_resolve_actor`` can
# supply, and the two builders with no actor (``build_scheduler``,
# ``build_notifier``) are exactly the ones that never touch a wallet.
#
# All four take the same optional factory the rest do, and none of them takes
# ``settings``. Mail settings are what the *so-far-built-a-service* side adds to a
# receipt; a sign-up sends nothing, because there is no address to send from until
# the account exists and no reason to tell anybody it did.
#
# ``password_hasher`` is a parameter on the three that need one rather than being
# constructed inside them, for the reason ``channel`` is a parameter on the
# deliverers: it is the seam where a test injects a fake, and a fake is what keeps
# the suite from paying tens of milliseconds per hash. It is the *only* such seam
# on this side, which is the honest measure of how little there is here.


def build_sign_up(
    unit_of_work_factory: UnitOfWorkFactory | None = None,
    password_hasher: PasswordHasher | None = None,
) -> SignUp:
    """Wire up registration over the same storage as everything else.

    No shared factory is required, and there is no exception to note here: a
    sign-up writes a user and a credential, both of which are on the unit it
    opens, so the transaction that makes them one thing is its own. Nothing
    outside it has to be built on the same factory.

    The hasher is the concrete argon2 adapter by default and the abstract port in
    the signature, which is the arrangement ``_channel_for`` establishes one
    level up: the composition root is the only place that names a concrete
    implementation, and everything above it depends on the interface.
    """
    return SignUp(
        unit_of_work_factory=unit_of_work_factory or SqliteUnitOfWorkFactory(),
        password_hasher=password_hasher or Argon2PasswordHasher(),
    )


def build_log_in(
    unit_of_work_factory: UnitOfWorkFactory | None = None,
    password_hasher: PasswordHasher | None = None,
) -> LogIn:
    """Wire up login, and note what this builder is the *only* source of.

    A session token exists because this was called. There is no other path in the
    codebase that writes a ``sessions`` row - ``build_sign_up`` does not, and
    ``build_resolve_actor`` only reads - so "where do tokens come from" has a
    one-line answer, and the answer is a use case a person has to satisfy with a
    password.

    The contrast with ``build_sign_up`` above is worth holding: that one needs the
    *same* hasher for a different verb. Hash-once and verify-many are two
    directions through one adapter, and both builders take it as an argument so
    that a test can substitute an adapter that recognises a password without
    doing the work of one.
    """
    return LogIn(
        unit_of_work_factory=unit_of_work_factory or SqliteUnitOfWorkFactory(),
        password_hasher=password_hasher or Argon2PasswordHasher(),
    )


def build_log_out(unit_of_work_factory: UnitOfWorkFactory | None = None) -> LogOut:
    """Wire up sign-out, which needs no hasher and no actor.

    The one builder in this group that takes nothing but a factory, and both
    absences carry information. No hasher, because ending a session compares no
    secret - it deletes a row that the presented token already identifies. No
    actor, because the token *is* the authorisation; see ``LogOut`` for why
    resolving one first would break the operation rather than secure it.
    """
    return LogOut(
        unit_of_work_factory=unit_of_work_factory or SqliteUnitOfWorkFactory(),
    )


def build_resolve_actor(
    unit_of_work_factory: UnitOfWorkFactory | None = None,
) -> ResolveActorFromSession:
    """Wire up the token-to-actor lookup every authenticated surface calls.

    No hasher here either, and the reason is the mirror of ``build_log_in``'s: a
    session token is hashed with SHA-256, which is a plain function in the domain
    rather than a port, because there is nothing to configure about it and
    nothing to swap. See ``hash_session_token`` for why the expensive hash is the
    wrong tool for a 256-bit random value.

    **There is no ``actor`` parameter, and unlike every other builder here that
    is not a statement about privilege** - it is the reverse of one. This is the
    thing that *produces* actors; taking one would be asking it to confirm what it
    is about to say. It is the only builder whose output is a ``User`` rather than
    a service, because the answer to "who is asking" is not a service.
    """
    return ResolveActorFromSession(
        unit_of_work_factory=unit_of_work_factory or SqliteUnitOfWorkFactory(),
    )
