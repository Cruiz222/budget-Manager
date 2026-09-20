from uuid import UUID

from app.application.identity.confirm_email_change import ConfirmEmailChange
from app.application.identity.confirm_password_reset import ConfirmPasswordReset
from app.application.identity.confirm_phone_sign_up import ConfirmPhoneSignUp
from app.application.identity.log_in import LogIn
from app.application.identity.log_in_with_google import LogInWithGoogle
from app.application.identity.log_out import LogOut
from app.application.identity.request_email_change import RequestEmailChange
from app.application.identity.request_password_reset import RequestPasswordReset
from app.application.identity.request_phone_verification import (
    RequestPhoneVerification,
)
from app.application.identity.resolve_actor import ResolveActorFromSession
from app.application.identity.sign_up import SignUp
from app.application.identity.sign_up_with_google import SignUpWithGoogle
from app.application.notifications.deliver_notifications import DeliverNotifications
from app.application.notifications.deliver_pending_messages import (
    DeliverPendingMessages,
)
from app.application.payments.initiate_deposit import InitiateDeposit
from app.application.payments.reconcile_payments import ReconcilePayments
from app.application.payments.settle_payment import SettlePayment
from app.application.plan_service import PlanService
from app.application.profile_service import ProfileService
from app.application.planning.execute_plan_run import ExecutePlanRun
from app.application.planning.notify_upcoming_runs import NotifyUpcomingRuns
from app.application.planning.run_due_plans import RunDuePlans
from app.application.unit_of_work import UnitOfWorkFactory
from app.application.wallet_service import WalletService
from app.domain.identity.googleIdentityVerifier import GoogleIdentityVerifier
from app.domain.identity.password_hasher import PasswordHasher
from app.infrastructure.security.argon2_password_hasher import Argon2PasswordHasher
from app.infrastructure.identity.pyjwt_google_identity_verifier import (
    PyJwtGoogleIdentityVerifier,
)
from app.infrastructure.settings import (
    EmailSettings,
    GoogleSettings,
    PaystackSettings,
    TermiiSettings,
    describe_configuration,
    describe_google_configuration,
    describe_termii_configuration,
)
from app.infrastructure.notifications.smtp_notification_channel import (
    SmtpNotificationChannel,
)
from app.infrastructure.notifications.termii_sms_channel import TermiiSmsChannel
from app.infrastructure.payments.paystack_payment_provider import (
    PaystackPaymentProvider,
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


def provider_for(
    settings: PaystackSettings | None, provider=None, *, callback_url: str | None = None
):
    """The payment provider to call: the injected one, Paystack, or none at all.

    The exact counterpart of ``_channel_for`` above, one direction of money over.
    That one picks the thing words leave through; this one picks the thing a
    collection is opened with. Both return ``None`` when the installation is not
    configured, and both take an injectable override that wins when given - which
    is what keeps the suite off the network in both cases.

    ``None`` is a value here, not a failure, in the same sense: it is how "this
    installation cannot take payments" reaches a caller as a state it can report.
    What that caller *does* with it differs from mail's, and the difference is
    the point: a missing mail account means say nothing, and a missing payment
    key means accept nothing. See ``presentation.api.dependencies`` for where
    that becomes a status code.

    **Public, where ``_channel_for`` is private, and the difference is a caller.**
    That one is reached only from builders in this file, so its underscore is
    accurate. This one has a second caller outside it: ``create_app`` puts the
    provider on ``app.state`` so the API can verify a signature with it, and a
    private name imported across modules is a promise about encapsulation that
    the import itself breaks. Naming it honestly is cheaper than a second
    construction site, and a second construction site is what this function
    exists to prevent - the two would drift over which settings they read, and
    the drift would present as a webhook verified with a key nothing else uses.

    **``callback_url`` is a parameter rather than a fourth settings object, and
    that is a deliberate refusal to teach this function about a URL.** The value
    Paystack needs is the installation's public origin *joined to the path a payer
    should land on*, and only two frames in this system know the second half: the
    web layer, which owns the page, and ``create_app``, which is where both facts
    meet. A reader that composed the join itself would have to know a
    presentation's route table, which is exactly the coupling the settings module
    exists to avoid - see ``build_request_password_reset`` for the same rule
    stated about a sentence instead of an address. So this function's job stops at
    handing the assembled string to the adapter, and the assembly happens one
    layer up.

    ``None`` is the ordinary case and not an error: it means nobody told this
    installation where it is publicly reachable, and a deposit then opens with no
    return address at all. Deposits still work; the payer simply is not sent back.
    See ``PaystackPaymentProvider.initialize_deposit``.
    """
    if provider is not None:
        return provider
    if settings is None:
        return None
    return PaystackPaymentProvider(
        secret_key=settings.secret_key, callback_url=callback_url
    )


def google_verifier_for(
    settings: GoogleSettings | None, verifier=None
) -> GoogleIdentityVerifier | None:
    """The Google verifier to trust: the injected one, PyJWT, or none at all.

    ``provider_for``'s shape one integration along, and every part of that
    docstring's argument transfers: both return ``None`` for an unconfigured
    installation, both take an override that wins, and both are public because
    ``create_app`` is a second caller outside this file.

    **It is public here for a stronger version of the reason, and that reason is
    worth stating because it is a performance decision rather than a tidy one.**
    The adapter wraps a ``jwt.PyJWKClient``, which fetches Google's signing keys
    once and then serves them from memory, rotating them when a token's ``kid`` is
    not in the set. Building one per request would throw that cache away and make
    every Google sign-in a network call to Google before it could even look at the
    token - which is both slow and a way to get rate-limited by a provider whose
    keys have not changed. So ``create_app`` resolves this once, at startup, and
    the same object serves every request exactly as the payment provider does.

    ``None`` is a value rather than a failure, and here it is stronger than it is
    for payments: an installation with no client id cannot judge a token *at all*.
    There is no degraded sign-in to fall back to, which is what
    ``google_from_environment`` argues - the only answers are "verify" and
    "refuse", and the refusal names the variable through
    ``_no_google_account_reason`` below.
    """
    if verifier is not None:
        return verifier
    if settings is None:
        return None
    return PyJwtGoogleIdentityVerifier(settings)


def _sms_channel_for(settings: TermiiSettings | None, channel=None):
    """The channel a text leaves through: the injected one, Termii, or none at all.

    ``_channel_for``'s exact counterpart one wire over - words leave by mail
    through that one, and a code leaves by text through this one - and it is
    private for the same reason: its callers are the builders in this file and
    nothing else. Note that ``provider_for`` beside it is public, and the
    difference is a caller rather than a conviction: ``create_app`` resolves a
    provider once and puts it on ``app.state``, so it needs the name, whereas both
    channel builders construct their adapter per call, exactly as the mail path
    always has. The three functions differ in one thing only - which settings they
    read and which adapter they build - and that is deliberate rather than
    regrettable: the day one of them grows a caller outside this module it is
    renamed, and until then the underscore is accurate.

    ``None`` is a value here, not a failure, exactly as it is for the other two:
    this suite runs with no ``TERMII_*`` set, so the ordinary test app would be an
    install that cannot text, and the fake seam is the same one the mail tests use
    - the builder is handed a channel, or the channel builder is replaced. What
    ``None`` *means* to a caller differs from mail's, and the difference is why this
    flow refuses rather than carrying on silently - see
    ``RequestPhoneVerification``.
    """
    if channel is not None:
        return channel
    if settings is None:
        return None
    return TermiiSmsChannel(
        api_key=settings.api_key,
        sender_id=settings.sender_id,
    )


def build_initiate_deposit(
    unit_of_work_factory: UnitOfWorkFactory | None = None,
    settings: PaystackSettings | None = None,
    *,
    actor: UUID,
    provider=None,
) -> InitiateDeposit:
    """Wire up the deposit-initiation use case over the provider.

    ``actor`` is required and keyword-only for the reason every other actor
    parameter in this file is: the use case reads a wallet, so it has to be told
    whose, and a default would be silence rather than an answer.

    Note what is *not* here: the use case takes a provider that must exist, and
    ``provider_for`` can return ``None``. The two are kept apart deliberately.
    Deciding that an unconfigured installation cannot open a collection is a
    decision about a *request* - it becomes a 503 with a body - and this function
    is not the layer that knows about either. Its caller checks, and the check
    lives in ``dependencies`` beside the check for a missing token.

    No shared factory is needed. The use case opens two units of its own and
    shares no fact between them beyond the reference, which is a string.
    """
    return InitiateDeposit(
        unit_of_work_factory=unit_of_work_factory or SqliteUnitOfWorkFactory(),
        provider=provider_for(settings, provider),
        actor=actor,
    )


def build_settler(
    unit_of_work_factory: UnitOfWorkFactory | None = None,
    settings: EmailSettings | None = None,
) -> SettlePayment:
    """Wire up settlement, which is the one money use case with no actor.

    **No actor, and no provider either, and both absences are the same fact seen
    twice.** A payment provider reporting what happened is not a person: it
    arrives holding a signature rather than a token, so there is nobody this
    builder could be told to act as - and rather than invent one, the use case
    derives the owner from the ledger row it is settling, through
    ``WalletRepository.owner_of``. That is the same shape as ``build_scheduler``,
    which answers "who is acting?" per plan instead of at this level; the
    difference is that a plan carries its owner and a transaction carries only a
    wallet, so this one asks the store.

    No provider, because settling needs no call outward - the provider tells
    *us*, and what it says has already been parsed into a ``ProviderOutcome`` by
    the time this is reached. That is why this builder takes no
    ``PaystackSettings``: the webhook that drives it verifies a signature with
    the secret key, and verification is the route's business rather than this
    use case's.

    ``settings`` is the mail settings and only that, for the receipt a settled
    movement earns - see ``SettlePayment._announce``. It is ``EmailSettings`` and
    not ``PaystackSettings``, which is worth noticing: the money comes in through
    Paystack and the receipt goes out through SMTP, and one builder naming both
    would blur two providers that share nothing but a wallet. (``build_reconciler``
    below is that builder, and it earns the exception by being the one job that
    genuinely does both - see its docstring.)
    """
    return SettlePayment(
        unit_of_work_factory=unit_of_work_factory or SqliteUnitOfWorkFactory(),
        recipient=settings.recipient if settings is not None else None,
    )


def build_reconciler(
    unit_of_work_factory: UnitOfWorkFactory | None = None,
    settings: EmailSettings | None = None,
    paystack: PaystackSettings | None = None,
    *,
    provider=None,
) -> ReconcilePayments:
    """Wire up reconciliation, which needs both providers and neither actor.

    **The first builder here that names both settings objects**, and the
    exception is worth its paragraph because ``build_settler`` above states the
    rule it breaks. That builder's rule - mail and payments in one signature
    "would blur two providers that share nothing but a wallet" - holds for every
    other use case in this file, and it holds because none of them does both.
    This one does: it asks Paystack a question and it queues the SMTP receipt
    that the answer earns. A builder with a different signature would not be
    narrower, it would be a lie - the receipts this job produces would have to be
    composed with a recipient some other function decided.

    **The settler is built from the *same* factory, and that is a correctness
    requirement in ``build_scheduler``'s sense.** Each row settles in its own
    transaction, so the two use cases do not share one - but they must share a
    *database*, and the failure mode of getting that wrong is silent: the
    reconciler would ask the provider about rows in one file and credit wallets in
    another, reporting recovered payments that never happened. One factory for
    both is how that becomes impossible rather than merely unlikely.

    **Both use cases are built here rather than passed in, and that is the whole
    point of the function.** ``ReconcilePayments`` takes a settler, so the only
    thing standing between this system and two settlement paths is where the
    settler came from - and it comes from here, from the same builder the webhook
    route uses. A recovered payment and a delivered one are indistinguishable
    because there is one settler, and this is the line that makes it so.

    ``paystack`` may be ``None``, exactly as it may be for ``build_initiate_deposit``,
    and for the same reason: deciding that an unconfigured installation cannot
    reconcile is a decision about a *run*, and the caller makes it. The CLI
    reports it and exits 0 - a job with nothing to ask with says so, the same way
    a tick with no mail account does.
    """
    factory = unit_of_work_factory or SqliteUnitOfWorkFactory()
    return ReconcilePayments(
        unit_of_work_factory=factory,
        settler=build_settler(unit_of_work_factory=factory, settings=settings),
        provider=provider_for(paystack, provider),
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


def build_profile_service(
    unit_of_work_factory: UnitOfWorkFactory | None = None,
    *,
    actor: UUID,
) -> ProfileService:
    """Wire up the profile use cases over the same storage as everything else.

    **The simplest builder here, and the one worth reading as the baseline.** It
    takes no ``settings``, no hasher and no provider, because reading and writing
    somebody's name needs none of them - and the contrast with
    ``build_wallet_service`` above is the whole content of this docstring. That
    builder takes a mail installation so a movement can produce a receipt; this
    one cannot send anything to anybody, which is a property of the feature rather
    than a gap in it. A profile write is a write and nothing else.

    ``actor`` is required and keyword-only, matching ``build_plan_service`` - the
    service binds it at construction, so every method it has is scoped to one
    person and there is no method that takes a user id. See ``ProfileService``.

    A separate factory from the wallet service's, and that is correct here for
    the reason ``build_plan_service`` gives: every method opens and closes its own
    unit, so a second factory pointed at the same database file is a second
    transaction by design rather than a second database.

    **The wallet service does not go through this one**, and it is worth saying
    where the two builders sit side by side and look interchangeable. The tier
    that decides how much money may move is read *inside the unit that moves the
    money*, through ``uow.profiles``, so the balance and the tier were read at one
    moment. A wallet service that asked this service for a tier would be reading
    it in a transaction of its own and acting on it in another - which is the race
    ``BEGIN IMMEDIATE`` exists to close, reintroduced at a higher layer where no
    isolation level can help.
    """
    return ProfileService(
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
# supply.
#
# The builders above with no actor are exactly the ones that can answer "whose
# wallet?" without being told, and each answers it differently: ``build_scheduler``
# per plan, from the owner the plan carries; ``build_settler`` from the ledger row
# it is settling, through ``owner_of``; and ``build_notifier`` and
# ``build_reconciler`` by not touching a wallet at all. None of them invents an
# actor, which is the property the whole group exists to make visible.
#
# The four builders that made up this group before the address change - sign-up,
# log-in, log-out and the actor lookup - take the same optional factory the rest
# do and nothing else, and each absence is argued in its own docstring. A sign-up
# sends nothing, because there is no address to send from until the account exists
# and no reason to tell anybody it did.
#
# ``password_hasher`` is a parameter on the three that need one rather than being
# constructed inside them, for the reason ``channel`` is a parameter on the
# deliverers: it is the seam where a test injects a fake, and a fake is what keeps
# the suite from paying tens of milliseconds per hash. It is the *only* such seam
# on this side, which is the honest measure of how little there is here.
#
# ``settings`` reaches the last two builders only, and that is a change worth
# marking because it is the one thing the paragraph above used to deny. Mail
# settings were the *so-far-built-a-service* side's business: a sign-up sends
# nothing, because there is no address to send from until the account exists, and
# a login tells nobody. An address change is the first identity operation that
# speaks to the outside world, and the two mails it sends are the reason - one to
# prove the new address, one to warn the old one. They are still the only two.
#
# The two password-reset builders at the end are now the fourth and fifth members
# of the no-actor group - after ``build_settler``, ``build_notifier`` and
# ``build_confirm_email_change`` - and the first pair whose absence of an actor is
# not a story about authorisation at all. A reset is authorised by a mailed code,
# exactly as a change is, but the *request* half has nothing standing behind it:
# the whole premise is that the caller has lost the password and holds no session,
# so there is no actor to be told and no proof to demand. It is the loosest entry
# point in the file and it is argued on ``RequestPasswordReset``.
#
# ``settings`` reaches all four of the mail-sending builders now, and this is
# where the count stops being a curiosity and becomes a rule: an installation has
# one SMTP configuration and every message in the system leaves through it. What
# is new with the reset pair is that ``None`` stops meaning "do without mail" and
# starts meaning "refuse", because there is nothing a forgotten password can be
# proved by when there is no mailbox to prove it with. That difference is not
# visible in these signatures - both builders take the same optional settings and
# the same optional channel - so it is implemented once, through the reason
# composed below.


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

    A session token exists because this was called, **or because
    ``build_log_in_with_google`` was** - and that second half was added when Google
    sign-in landed, so the sentence above read "the only" for most of this file's
    life and no longer does. What is left of the claim is the part that was doing
    the work: there is no *other kind* of path. Nothing mints a token as a side
    effect of reading something, no route returns one for a session that already
    exists, and ``build_sign_up`` still does not write a ``sessions`` row. A token
    comes from a login, and there are now two logins because there are two kinds of
    proof - which is ``log_in``'s module docstring's subject, not this one's.

    The contrast with ``build_sign_up`` above is worth holding: that one needs the
    *same* hasher for a different verb. Hash-once and verify-many are two
    directions through one adapter, and both builders take it as an argument so
    that a test can substitute an adapter that recognises a password without
    doing the work of one. ``build_log_in_with_google`` takes none, which is the
    mechanical difference between the two logins: one compares a stored secret and
    one never had a secret to compare.
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


def build_request_email_change(
    unit_of_work_factory: UnitOfWorkFactory | None = None,
    password_hasher: PasswordHasher | None = None,
    settings: EmailSettings | None = None,
    actor: UUID | None = None,
    channel=None,
) -> RequestEmailChange:
    """Wire up asking to move an account's address, as one account.

    The first identity builder with an ``actor``, and the reason it has one is the
    same reason the money builders do: it acts *as somebody*, reading that
    account's credential and comparing a password against it. It is also the first
    one that needs the hasher for a **verification rather than a hash**, which is
    worth noticing because it is the opposite direction through the same port from
    ``build_sign_up`` - and the reason the two builders take the identical
    argument.

    ``settings`` and ``channel`` are the pair ``build_deliverer`` already has, and
    the arrangement is copied rather than reinvented: ``_channel_for`` picks the
    SMTP adapter, an injected ``channel`` wins over it, and ``None`` means this
    installation has no mail account. That last case is not an error here - it is
    the fallback the whole feature turns on, and it is why the *use case* can apply
    a change immediately. See ``RequestEmailChange`` for why refusing instead would
    move the trap one layer down.

    **No shared factory with the confirm builder below, and there is nothing to
    share.** The two halves of a change never run in the same transaction: one
    writes a request, the other spends it minutes later on another device, and a
    shared factory would only be a way to make a reader wonder whether they were
    supposed to. The pairing this feature needs is *within* the confirm, between
    ``email_changes`` and ``users`` - and that one is stated on the unit.
    """
    return RequestEmailChange(
        unit_of_work_factory=unit_of_work_factory or SqliteUnitOfWorkFactory(),
        password_hasher=password_hasher or Argon2PasswordHasher(),
        actor=actor,
        channel=_channel_for(settings, channel),
    )


def build_confirm_email_change(
    unit_of_work_factory: UnitOfWorkFactory | None = None,
    settings: EmailSettings | None = None,
    channel=None,
) -> ConfirmEmailChange:
    """Wire up answering a change, which has no actor to be given.

    **This is the third builder in the file that takes no ``actor``, and it is the
    first whose absence is about *authorisation* rather than about there being
    nobody to name.** ``build_settler`` has none because a provider reporting a
    movement is not a person, and ``build_notifier`` has none because it reads no
    wallet. This one has none because the token *is* the authorisation: it was
    mailed to the address being moved to, and it exists only because somebody
    already proved the account's password to mint it. See ``ConfirmEmailChange``
    for why a live session on top would refuse the person who asked on a laptop
    and read the mail on a phone, and why that is a hole closed rather than a hole
    opened.

    The channel is here for the other mail - the notice to the address being left
    behind - and it is the one send in this system whose failure is *reported
    rather than raised*. The mail account is the same one ``build_deliverer``
    delivers warnings and receipts through, deliberately: an installation has one
    SMTP configuration, not one per kind of message.
    """
    return ConfirmEmailChange(
        unit_of_work_factory=unit_of_work_factory or SqliteUnitOfWorkFactory(),
        channel=_channel_for(settings, channel),
    )


def build_request_password_reset(
    unit_of_work_factory: UnitOfWorkFactory | None = None,
    settings: EmailSettings | None = None,
    channel=None,
) -> RequestPasswordReset:
    """Wire up asking for a reset, which is the one mail-sending builder with no proof.

    No hasher and no actor, and both absences are the same fact: there is no
    password here to verify and nobody to verify one *as*. ``build_log_out`` and
    ``build_confirm_email_change`` have no actor because the token is the
    authorisation; this one has none because the caller is a stranger by
    construction - they cannot log in, which is why they are here. Demanding a
    session would refuse exactly the person the endpoint exists for.

    **This builder is where "no mail account" stops meaning "do without it".**
    ``build_request_email_change`` takes the same two arguments and treats a
    missing channel as an instruction to apply the change immediately, which works
    there because the account's own password was just proved. Nothing here can be
    proved, so ``_channel_for`` returning ``None`` is a refusal - and the refusal
    has to name the setting that is missing, which means asking
    ``describe_configuration`` what it is.

    That question is asked *here* rather than in the use case or in a presentation,
    and the placement is the point. ``settings.py`` is the only module in the
    system that reads the real environment, and the CLI and the API must both
    refuse with the same sentence - so the sentence is composed once, where the
    configuration is read, and handed down as an argument. A dependency in
    ``presentation.api`` could not serve the CLI, and a use case that read
    ``os.environ`` would break the rule the settings module exists to keep. The
    injected ``channel`` is checked *after* ``_channel_for`` rather than before it,
    so a test that passes a fake channel never causes the environment to be looked
    at at all - which is the same reason the fake wins over the settings one level
    up.
    """
    resolved = _channel_for(settings, channel)
    return RequestPasswordReset(
        unit_of_work_factory=unit_of_work_factory or SqliteUnitOfWorkFactory(),
        channel=resolved,
        unconfigured_reason=_no_mail_account_reason(resolved),
    )


def _no_mail_account_reason(resolved) -> str | None:
    """Why a password cannot be reset here, in the one sentence both presentations say.

    **Two halves, and neither is decoration.** ``describe_configuration`` names the
    variable that would fix this, which is the actionable fact; the prefix says what
    is *impossible* until it is set, which is the fact a person who just typed
    ``reset-password`` needs and cannot infer from a bare variable name - "SMTP_HOST is
    not set" is true of every mail-sending feature in this system, and the reason it
    matters *here* is the third ruling behind this feature: an address change falls
    back to the password proof when there is no mail, and a forgotten password has no
    proof to fall back on.

    ``None`` when there is a channel, because there is nothing to explain. When there
    is not, the prefix stands alone if ``describe_configuration`` has nothing to add -
    which happens only when the environment is complete and yet no settings and no
    channel reached this function, i.e. a caller that bypassed ``from_environment``.
    That is the case the old ``or`` here was written for, and it is kept: refusing with
    "so a password cannot be reset: None" would be worse than refusing with the prefix
    on its own.
    """
    if resolved is not None:
        return None
    missing = describe_configuration()
    reason = "this installation has no mail account, so a password cannot be reset"
    return f"{reason}: {missing}" if missing else reason


def build_confirm_password_reset(
    unit_of_work_factory: UnitOfWorkFactory | None = None,
    password_hasher: PasswordHasher | None = None,
    settings: EmailSettings | None = None,
    channel=None,
) -> ConfirmPasswordReset:
    """Wire up answering a reset, which needs the hasher and no actor.

    The hasher is here for the **hash** direction, which makes this builder the
    fourth to take it and the second to take it for writing rather than checking -
    ``build_sign_up`` is the other. That is worth naming because the argument is
    identical and the stakes are not: a sign-up hashes a password the person chose
    while looking at a form, and this hashes one typed into a terminal after a
    lockout, where a silent transcription error recreates the lockout the whole
    command exists to end. The CLI asks twice for that reason; nothing at this
    layer can.

    No actor, and the argument is ``build_confirm_email_change``'s unchanged: the
    mailed code is the authorisation, and the account it acts on is the one the
    claimed row names rather than one a request supplied. What this one does that
    that one does not is *end sessions* - see ``SessionRepository.delete_by_user_id``
    for why a reset revokes where an address change deliberately does not.

    ``settings`` and ``channel`` are the same pair every mail-sending builder takes,
    and ``None`` means something different here again: the notice is simply not
    attempted, and the result says so in ``notice_sent``/``notice_error``. It is
    deliberately *not* a refusal, because by the time this builder is reached a
    channel already existed - a confirm is only reachable through a request that
    required one - so a missing channel can only mean the installation was
    reconfigured in the window. Refusing then would leave a spent code and an
    unchangeable password, which is the worst state this feature can produce.
    """
    return ConfirmPasswordReset(
        unit_of_work_factory=unit_of_work_factory or SqliteUnitOfWorkFactory(),
        password_hasher=password_hasher or Argon2PasswordHasher(),
        channel=_channel_for(settings, channel),
    )


def build_request_phone_verification(
    unit_of_work_factory: UnitOfWorkFactory | None = None,
    settings: TermiiSettings | None = None,
    channel=None,
) -> RequestPhoneVerification:
    """Wire up asking to prove a number, the second builder for a caller with nothing.

    No hasher because there is no password to hash yet - the password is chosen at
    the confirm, by whoever read the text - and no actor for
    ``build_request_password_reset``'s reason exactly: the person using this cannot
    be identified, and here it is stronger, because they may not have an account to
    be identified *as*. Demanding anything would refuse exactly the person the
    endpoint exists for.

    **A missing channel is a refusal here, where mail's is a refusal only
    sometimes.** ``build_request_email_change`` treats no mail account as an
    instruction to do without it, and it can, because the account's own password was
    just proved. Nothing here can be proved: the reason to believe somebody holds a
    handset *is* a message arriving on it, so there is no fallback to fall back to
    and the refusal is the only true answer. The sentence naming the missing
    variable is composed here rather than in the use case, by
    ``_no_sms_account_reason`` below - see ``build_request_password_reset`` for the
    whole of that argument, which is one channel over and identical.

    The injected ``channel`` is resolved *first*, so a test that passes a fake never
    causes the real environment to be read at all.
    """
    resolved = _sms_channel_for(settings, channel)
    return RequestPhoneVerification(
        unit_of_work_factory=unit_of_work_factory or SqliteUnitOfWorkFactory(),
        channel=resolved,
        unconfigured_reason=_no_sms_account_reason(resolved),
    )


def _no_sms_account_reason(resolved) -> str | None:
    """Why a number cannot be verified here, in the one sentence both presentations say.

    ``_no_mail_account_reason``'s shape and its argument - a prefix saying what is
    impossible, then ``describe_termii_configuration`` naming the variable that would
    fix it - and the two differ only in which reader supplies the second half. That
    is deliberate rather than a copy that drifted: the pair of functions is what
    keeps each channel's reason composed from *its own* configuration, so an install
    with mail and no SMS says which variable is missing instead of reporting an
    outage, and an install with neither still gets a sentence about the flow the
    person actually tried to use.

    ``None`` when there is a channel, because there is nothing to explain; and the
    prefix alone when ``describe_termii_configuration`` has nothing to add, which
    happens only when the environment is complete and yet no settings and no channel
    reached this function - a caller that bypassed ``from_environment``.
    """
    if resolved is not None:
        return None
    missing = describe_termii_configuration()
    reason = (
        "this installation has no SMS account, so a number cannot be verified"
    )
    return f"{reason}: {missing}" if missing else reason


def build_confirm_phone_sign_up(
    unit_of_work_factory: UnitOfWorkFactory | None = None,
    password_hasher: PasswordHasher | None = None,
) -> ConfirmPhoneSignUp:
    """Wire up answering a phone signup, which needs the hasher and nothing else.

    **Two arguments, and the shortest signature of any builder that creates an
    account** - which is worth naming, because what is missing is more instructive
    than what is there. No channel: nothing is sent from this half, and there is no
    second message to send (see ``phoneVerificationMessage`` for why a "your number
    is verified" text would tell somebody a fact they learned by answering the code).
    No settings and no ``unconfigured_reason`` either, for
    ``build_confirm_password_reset``'s reason: by the time this is reached a channel
    already existed, because a confirm is only reachable through a request that
    required one, so there is nothing here that a missing channel could refuse.

    The hasher is not optional and not defaulted, and it is the third builder to
    take it for *writing* after ``build_sign_up`` and ``build_confirm_password_reset``.
    The argument is identical to those two: a hash is the only thing this half
    stores, so an install that could not hash could not confirm at all.
    """
    return ConfirmPhoneSignUp(
        unit_of_work_factory=unit_of_work_factory or SqliteUnitOfWorkFactory(),
        password_hasher=password_hasher or Argon2PasswordHasher(),
    )


def build_sign_up_with_google(
    unit_of_work_factory: UnitOfWorkFactory | None = None,
    settings: GoogleSettings | None = None,
    verifier=None,
) -> SignUpWithGoogle:
    """Wire up registering the account a Google identity belongs to.

    **No hasher, and here the absence is the feature rather than an omission.**
    Four builders in this file take one, three of them for writing; this one and
    its sibling below take none because nothing on this path is ever hashed - an
    account arriving through Google has no password to store, and a hash invented
    for it would be a password nobody chose. The hasher's absence is the
    mechanical statement of what ``record_new_google_account`` does instead of
    ``record_new_account``: one row rather than two.

    No actor either, for ``build_request_password_reset``'s reason: the caller
    cannot authenticate, and the token they present is the proof instead. A
    dependency that demanded a session would refuse exactly the person the
    endpoint exists for.

    **This builder is where "no Google client id" becomes a refusal**, following
    ``build_request_password_reset`` and ``build_request_phone_verification``
    exactly: ``google_verifier_for`` returns ``None`` for an installation with no
    client id, and the sentence naming the missing variable is composed *here*
    because ``app.infrastructure.settings`` is the only module in the system that
    reads the environment. A use case may not import it and an API dependency
    cannot be seen by the CLI, so the sentence is made once where the
    configuration is read and handed down as an argument.

    The injected ``verifier`` is resolved first, so a test that passes a fake
    never causes the real environment to be read at all - the same ordering the
    two channel builders above use, and here it also means a suite can test this
    whole feature without PyJWT ever being pointed at Google.
    """
    resolved = google_verifier_for(settings, verifier)
    return SignUpWithGoogle(
        unit_of_work_factory=unit_of_work_factory or SqliteUnitOfWorkFactory(),
        verifier=resolved,
        unconfigured_reason=_no_google_account_reason(resolved),
    )


def build_log_in_with_google(
    unit_of_work_factory: UnitOfWorkFactory | None = None,
    settings: GoogleSettings | None = None,
    verifier=None,
) -> LogInWithGoogle:
    """Wire up exchanging a Google identity for a session.

    ``build_sign_up_with_google``'s signature word for word, and the sameness is
    worth noticing rather than skimming: the two halves of this feature take
    exactly the same three arguments in exactly the same order, which is what
    makes them two spellings of one wiring rather than two wirings that happen to
    agree. The difference between them is entirely in what they *write* - a user
    row against a session row - and that is stated where it can be read, in the two
    use cases' own docstrings.

    **It is the second builder in this file that writes a ``sessions`` row**, and
    that is worth naming because ``build_log_in``'s docstring claims to be the only
    source of tokens. It was, until this line existed; see that docstring, which
    says so now. What is *not* shared is the hasher: ``build_log_in`` takes one to
    compare a stored hash against, and there is no hash on this path at all, so a
    password is never read - which is why the two builders that issue tokens do not
    have the same signature.
    """
    resolved = google_verifier_for(settings, verifier)
    return LogInWithGoogle(
        unit_of_work_factory=unit_of_work_factory or SqliteUnitOfWorkFactory(),
        verifier=resolved,
        unconfigured_reason=_no_google_account_reason(resolved),
    )


def _no_google_account_reason(resolved) -> str | None:
    """Why no Google sign-in can be judged here, in the one sentence both say.

    ``_no_mail_account_reason``'s shape and ``_no_sms_account_reason``'s other
    half - a prefix saying what is impossible, then ``describe_google_configuration``
    naming the variable that would fix it - and the third of these functions is
    what turns the pattern from a repeated decision into the house shape for a
    channel that can be missing. Each one composes from *its own* reader, so an
    install with mail and no Google says which variable is missing rather than
    reporting an outage.

    ``None`` when there is a verifier, because there is nothing to explain; and
    the prefix alone when ``describe_google_configuration`` has nothing to add,
    which happens only when the environment is complete and yet no settings and no
    verifier reached this function - a caller that bypassed
    ``google_from_environment``.

    **The prefix is the part a person could not infer**, and here it says something
    slightly different from the other two: a mail-less install cannot reset a
    password, an SMS-less one cannot verify a number, and a Google-less one cannot
    sign anybody in *at all*. There is no fallback flow for a client that arrived
    holding an id_token - it either lets them in or it does not - which is why this
    sentence is worth reading as the strongest of the three rather than the third
    of a kind.
    """
    if resolved is not None:
        return None
    missing = describe_google_configuration()
    reason = (
        "this installation has no Google client id, so no Google sign-in can be "
        "verified"
    )
    return f"{reason}: {missing}" if missing else reason
