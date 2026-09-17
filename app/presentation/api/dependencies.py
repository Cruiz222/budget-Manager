"""Who is asking, and the services built for them.

**One service per request, built with the actor that request resolved.** This is
the payoff of Phase 1a's decision to bind the acting user at construction: a
service is a factory and a UUID, so building one per request costs a tuple
allocation, and the actor cannot leak between requests because it is never stored
anywhere shared. The alternative - one long-lived service whose actor is set per
request - is exactly the bypass that phase spent its effort removing, and it
would be a worse bypass here than it ever was on the command line, because a
server serves concurrent requests and a mutable actor on a shared object is a
race whose loser reads somebody else's wallet.

The dependencies below are the whole of the arrangement. None of them holds state
between requests, and none could: FastAPI calls them per request and throws the
result away.

**This module is the only place a token is read off a request.** Every
authenticated endpoint reaches its actor through ``current_actor``, so the
question "how does this API decide who is asking?" has one answer, in one
function, and the answer is a credential rather than an assertion. The header
that used to be here - ``X-User-Email``, which named a user and proved nothing -
is gone, and its absence is the phase's whole point: **there is no code path in
this API by which a request can become a user without presenting a token.** That
is not a rule anybody has to keep; it is what is left when the only function that
can produce a ``User`` requires one.

**A missing header is this layer's business; a bad one is the domain's.** The
``Authorization`` header's *presence* and *shape* are transport concerns, and the
domain has never heard of a header - so ``bearer_token`` owns the split between
``Bearer`` and the token, and refuses an absent or unrecognisable header with
``MissingCredentialsError``. What the token then *means* goes straight to
``ResolveActorFromSession``, and a token that is unknown, expired or orphaned
comes back as ``InvalidSessionError`` through the same handler as every other
domain refusal. Both are 401s, and the ``error`` field tells the two apart: "you
sent nothing" against "what you sent was not good enough".

**Three of the dependencies below are not built for an actor at all**, and they
are the most recently added, so they are worth flagging on the way in.
``payment_provider`` answers a question about the *installation* - can this
deployment take money - and ``settler_service`` builds the one money use case that
has no actor to be given, because a provider reporting a movement is not a person.
``confirm_email_change_service`` is the third and the only one whose absence is
about *proof* rather than about there being nobody to name: an address change is
answered by a token mailed to the address being moved to, which is a better
warrant than the session that asked for it. Everything else here is "the services
built for them"; those three are what is left when "them" is the wrong question.
Their docstrings carry the arguments.

**The two password-reset services make that four**, and they are the first pair
added together rather than one at a time. Neither has an actor, and the asymmetry
between them is the interesting part: the confirm joins the group above, since its
warrant is a mailed code, while the *request* has no warrant at all and cannot have
one - the caller is here precisely because they cannot log in. It is the only
service in this module built for somebody the request has not identified, and the
only one whose honest answer to "who is asking?" is "a stranger, about an address".
See ``request_password_reset_service``, which is where that stops being alarming
and becomes a design.

**The two phone-verification services make that six**, added as a pair for the same
reason and with the same asymmetry one step further out. ``request_phone_verification_service``
is built for a caller who may not be anybody in this system at all - the account is
what answering creates - which makes it the first service here whose subject does
not yet exist rather than one whose subject cannot be proved. Its confirm joins the
group whose warrant is a code. Read as a group, the six are one question asked four
ways: *who is asking, and what proves it?* The answer for the newest pair is
"nobody yet, and a text message".
"""

from datetime import datetime

from fastapi import Depends, Request

from app.application.identity.confirm_email_change import ConfirmEmailChange
from app.application.identity.confirm_password_reset import ConfirmPasswordReset
from app.application.identity.confirm_phone_sign_up import ConfirmPhoneSignUp
from app.application.identity.log_in import LogIn
from app.application.identity.log_out import LogOut
from app.application.identity.request_email_change import RequestEmailChange
from app.application.identity.request_password_reset import RequestPasswordReset
from app.application.identity.request_phone_verification import (
    RequestPhoneVerification,
)
from app.application.identity.resolve_actor import ResolveActorFromSession
from app.application.identity.sign_up import SignUp
from app.application.payments.initiate_deposit import InitiateDeposit
from app.application.payments.settle_payment import SettlePayment
from app.application.plan_service import PlanService
from app.application.profile_service import ProfileService
from app.application.wallet_service import WalletService
from app.composition_root import (
    build_confirm_email_change,
    build_confirm_password_reset,
    build_confirm_phone_sign_up,
    build_initiate_deposit,
    build_log_in,
    build_log_out,
    build_plan_service,
    build_profile_service,
    build_request_email_change,
    build_request_password_reset,
    build_request_phone_verification,
    build_resolve_actor,
    build_settler,
    build_sign_up,
    build_wallet_service,
)
from app.domain.identity.user import User
from app.domain.payments.paymentProvider import PaymentProvider
from app.presentation.api.errors import (
    MissingCredentialsError,
    PaymentsUnconfiguredError,
)

#: The scheme a token is presented with, spelled the way the header spells it.
#:
#: ``Bearer`` rather than a bespoke scheme name, because this is exactly what
#: bearer authentication is - whoever holds the token is treated as the person it
#: was issued to - and a client library that knows how to send an
#: ``Authorization`` header already knows how to send this one. Inventing a name
#: would buy nothing and cost every caller a line of documentation to read.
AUTH_SCHEME = "Bearer"


def bearer_token(request: Request) -> str:
    """The token this request presented, or a refusal.

    Returns the token exactly as the client sent it - not stripped of anything
    but surrounding whitespace, and **not hashed**. Hashing happens in the
    domain, in ``hash_session_token``, and the reason it happens there rather
    than here is the reason ``SessionRepository`` gives: the hash must always be
    *derived* from a presented token by the one function that defines the
    derivation. A boundary that hashed its own way would be a second definition,
    and a disagreement between the two would present as a token that works on
    the CLI and not over HTTP.

    The scheme is matched case-insensitively, which is what RFC 9110 requires of
    it, and the token is not otherwise interpreted: no base64, no prefix, no
    structure. It is an opaque string that only a hash table lookup understands.

    An unrecognised scheme is refused here rather than passed down. That is a
    deliberate choice between two 401s: ``Bearer basic-dXNlcjpwYXNz`` is a client
    that has misunderstood the API, and telling it so as "credentials were not
    presented" is more useful than sending "basic-dXNlcjpwYXNz" to a lookup that
    was never going to find it.
    """
    header = request.headers.get("Authorization")
    if header is None:
        raise MissingCredentialsError(
            "the Authorization header is required: send 'Bearer <token>'"
        )

    scheme, separator, token = header.partition(" ")
    token = token.strip()
    if not separator or scheme.lower() != AUTH_SCHEME.lower() or not token:
        raise MissingCredentialsError(
            f"the Authorization header must read '{AUTH_SCHEME} <token>'"
        )
    return token


def current_actor(request: Request) -> User:
    """The user this request acts as, proved by the token it presented.

    FastAPI caches a dependency's result for the duration of one request, so this
    runs once even when several services depend on it. That mattered more before
    than it does now - a second call used to be a second find-or-create, which is
    a write - but it is still worth having, because a second call is a second
    session lookup and a second clock reading, and the actor of one request should
    be one answer rather than several that happen to agree.

    **The clock is read here and nowhere below.** ``datetime.now()`` at this line
    is the only place a wall clock enters the authenticated path, which is what
    lets every expiry rule underneath be a pure comparison a test can drive. It
    is also why the parameter is not on the dependency: FastAPI has no way to
    supply it, so making it an argument would mean a default argument that reads
    the clock anyway - the same call, in a place that looks configurable and is
    not.
    """
    return build_resolve_actor(
        unit_of_work_factory=request.app.state.unit_of_work_factory
    ).execute(bearer_token(request), datetime.now())


def wallet_service(
    request: Request, actor: User = Depends(current_actor)
) -> WalletService:
    """The wallet use cases, acting as this request's actor.

    ``settings`` is passed even though nothing reachable in this phase sends
    mail, and passing it is deliberate: the builder's signature is the one the
    CLI uses, so when Phase 2 exposes a deposit the receipt is already wired and
    nobody has to notice that it was not.
    """
    return build_wallet_service(
        unit_of_work_factory=request.app.state.unit_of_work_factory,
        settings=request.app.state.settings,
        actor=actor.user_id,
    )


def plan_service(
    request: Request, actor: User = Depends(current_actor)
) -> PlanService:
    """The plan use cases, acting as this request's actor.

    Scoped by the same actor as the wallet service above, and it scopes plans as
    well as wallets - so another user's plan is not refused here, it is a plan
    that was never created.
    """
    return build_plan_service(
        unit_of_work_factory=request.app.state.unit_of_work_factory,
        actor=actor.user_id,
    )


def profile_service(
    request: Request, actor: User = Depends(current_actor)
) -> ProfileService:
    """The profile use cases, acting as this request's actor.

    **The only dependency here whose actor is the thing being read *and* the
    thing being written**, which is why it is worth a sentence even though it
    looks like ``plan_service`` above. Every other scoped dependency takes an id
    that narrows a search - a wallet id, a plan id - and the actor is a separate
    fact used to check ownership. Here there is no id at all: the request asks
    for "my profile" and the actor *is* the address. So the route below takes no
    path parameter, no query parameter and no id in the body, and there is
    deliberately nothing for it to take: a caller cannot name somebody else's
    profile because the interface has no way to spell one.

    That is a stronger version of the rule the API keeps everywhere else, and it
    is worth noticing where it comes from. For a wallet, "not yours" and "does
    not exist" have to be collapsed into one 404 by hand, because a wallet id can
    be substituted. Here the substitution is unrepresentable, so there is no
    branch to write and none to get wrong.

    **It takes no ``settings``**, unlike ``request_email_change_service`` and the
    password-reset pair, and the absence is the honest one: nothing on this path
    sends mail. A profile write changes a tier and the person finds out from this
    response, which is the same moment they asked.
    """
    return build_profile_service(
        unit_of_work_factory=request.app.state.unit_of_work_factory,
        actor=actor.user_id,
    )


def sign_up_service(request: Request) -> SignUp:
    """Registration, which is one of the five things reachable without a token.

    No ``current_actor`` parameter, and it cannot have one: signing up is how a
    person comes to be able to authenticate, so demanding a token would make the
    endpoint reachable only by those who no longer need it. That leaves it as an
    unauthenticated write, which is inherent rather than a hole - and it is the
    reason rate limiting is a real item in 2c, since this is the one endpoint
    that will do tens of milliseconds of work for anybody who asks.

    The hasher comes off ``app.state``, where ``create_app`` resolved it once.
    That is the same treatment the unit of work factory and the mail settings
    get, and for the same reason: it is configuration, and configuration is
    resolved at startup rather than per request. The adapter holds no connection
    and no mutable state - it wraps a set of cost parameters - so sharing one
    across the threadpool is safe, and building a fresh one per request would
    only be a second, identical object.
    """
    return build_sign_up(
        unit_of_work_factory=request.app.state.unit_of_work_factory,
        password_hasher=request.app.state.password_hasher,
    )


def log_in_service(request: Request) -> LogIn:
    """Login, the second of the five things reachable without a token, and the other
    one that is a way in.

    **This endpoint is slow on purpose and must stay off the event loop.** It is
    a plain ``def``, so FastAPI runs it in its threadpool, which is what makes
    the tens of milliseconds argon2 spends invisible to other requests. Writing
    ``async def`` here would not make the hash concurrent; it would run it on the
    event loop and stall every other request for its duration. This is the first
    genuinely slow call in the API, so it is the first place that mistake would
    have mattered.
    """
    return build_log_in(
        unit_of_work_factory=request.app.state.unit_of_work_factory,
        password_hasher=request.app.state.password_hasher,
    )


def log_out_service(request: Request) -> LogOut:
    """Sign-out, which resolves no actor - see ``LogOut`` for why that is correct.

    The route passes ``bearer_token(request)`` straight in. There is deliberately
    no ``current_actor`` dependency on the logout endpoint: requiring a token to
    *still be valid* in order to discard it would refuse exactly the tokens a
    client most wants to get rid of, and possession of the token is already the
    whole of the authorisation for deleting it.
    """
    return build_log_out(
        unit_of_work_factory=request.app.state.unit_of_work_factory,
    )


def request_email_change_service(
    request: Request, actor: User = Depends(current_actor)
) -> RequestEmailChange:
    """Asking to move *this* account's address, which is why it acts as somebody.

    An actor here and none on the confirm below, and the pair is the whole of how
    this feature is authorised. Asking is a change to an account, so it is done
    *by* that account: the caller is resolved from a token, the password in the
    body is checked against that account's credential, and neither can stand in
    for the other. Answering is proved by something else entirely, so it has no
    actor at all - see ``confirm_email_change_service``.

    ``settings`` is the mail settings, and this is the first identity dependency
    that needs them: the builder turns them into a channel, or into ``None`` when
    the installation has no mail account. That ``None`` is not a failure here - it
    is what makes the change apply immediately, which is the fallback a fresh
    install depends on. Nothing in this module branches on it; the use case does,
    because it is the only code that knows what to do instead.
    """
    return build_request_email_change(
        unit_of_work_factory=request.app.state.unit_of_work_factory,
        password_hasher=request.app.state.password_hasher,
        settings=request.app.state.settings,
        actor=actor.user_id,
    )


def confirm_email_change_service(request: Request) -> ConfirmEmailChange:
    """Answering a change, which has no actor and therefore no ``current_actor``.

    **The second service in this module built without one**, after
    ``settler_service``, and the two absences are different in kind. That one
    cannot be told who is acting because a provider reporting a movement is not a
    person. This one *could* be told - there is a live session in the world that
    asked for the change - and is deliberately not, because the token is a better
    proof than the session is. It was mailed to the address being moved to, and it
    was minted only after somebody proved the account's password, so requiring a
    session on top would add a way for the person who asked to be refused without
    adding a check. See ``ConfirmEmailChange``, which argues this at length.

    That makes it the API's third unauthenticated write, and the reason it is not
    a hole is worth stating where the missing dependency is visible: what it can
    write is one account's address, and the only way to reach the write is to
    present a value this system posted to that address.

    ``settings`` for the notice to the address being left behind - the same mail
    settings everything else uses, so an installation has one SMTP account rather
    than one per kind of message.
    """
    return build_confirm_email_change(
        unit_of_work_factory=request.app.state.unit_of_work_factory,
        settings=request.app.state.settings,
    )


def request_password_reset_service(request: Request) -> RequestPasswordReset:
    """Asking for a reset, which is built for nobody and cannot be built for anybody.

    **No ``current_actor``, and unlike every other absence in this module this one
    is not a statement about warrant - it is the situation.** The caller cannot
    authenticate; that is what a forgotten password *is*. A dependency that
    demanded a token would refuse every request this endpoint exists to serve, and
    there is no substitute proof available: an address and nothing else is the
    entire input.

    So the honest description of what is built here is a service that will send one
    mail to one address if that address names an account, and will say nothing that
    distinguishes the two cases. The containment is not in this function - it is in
    the use case, which is where the argument is written down; what this function
    guarantees is that no token is consulted, so a caller cannot even *try* to make
    the request be about somebody.

    **``settings`` is passed even though the builder may refuse**, and the
    difference from ``request_email_change_service`` above is worth reading as a
    pair. That one hands over mail settings that may resolve to no channel, and the
    use case treats that as an instruction to apply the change immediately. Here a
    missing channel is a refusal, and the sentence it refuses with is composed by
    the builder from the environment - which is why this dependency hands over the
    settings and reads nothing itself. Nothing in this module ever calls
    ``describe_configuration``; if it did, the CLI would need a second copy.
    """
    return build_request_password_reset(
        unit_of_work_factory=request.app.state.unit_of_work_factory,
        settings=request.app.state.settings,
    )


def confirm_password_reset_service(request: Request) -> ConfirmPasswordReset:
    """Answering a reset, which has no actor for ``confirm_email_change_service``'s reason.

    **The fourth service here built without one**, and it belongs to the group
    whose absence is about *proof*: a reset is answered by a code mailed to the
    address the account already holds, so possession of that mailbox is a better
    warrant than a session would be. A session on top would not add a check - it
    would add a way for the person who asked on a laptop and read the mail on a
    phone to be refused by exactly the operation that exists to let them back in.

    It takes the hasher where the confirm above does not, because this is the one
    use case in the identity group that *writes* a credential rather than reading
    one - see ``build_confirm_password_reset``. The hasher comes off
    ``app.state``, exactly as ``sign_up_service``'s does, so the argon2 adapter is
    still constructed in exactly one place.

    ``settings`` for the notice that the password changed. A missing channel here
    is *not* a refusal, and the asymmetry with the request above is deliberate:
    this call has already spent the code, so refusing would leave the account with
    a dead code and an unchanged password. The result reports the notice instead.
    """
    return build_confirm_password_reset(
        unit_of_work_factory=request.app.state.unit_of_work_factory,
        password_hasher=request.app.state.password_hasher,
        settings=request.app.state.settings,
    )


def request_phone_verification_service(
    request: Request,
) -> RequestPhoneVerification:
    """Asking to prove a number, which is built for somebody who may not exist yet.

    **No ``current_actor``, and this absence is the strongest of the group.** The
    other unauthenticated services are built for a caller who cannot *prove* they
    are anybody - a person who forgot a password, a person answering a mailed code.
    This one is built for a caller who may not *be* anybody in this system: the
    account is what answering creates, so requiring a session would not refuse a
    stranger, it would refuse the entire flow.

    **``settings`` is handed over and nothing is read here, exactly as the reset
    dependency above does it.** ``composition_root`` composes the sentence that
    names the missing variable from the environment; a copy of that logic in this
    module would be a second sentence the CLI could disagree with, and the CLI is
    the surface where an operator is most likely to see it. Nothing in this module
    ever calls ``describe_termii_configuration``.

    ``settings`` is ``app.state.termii`` and not ``app.state.settings``, and the two
    names being adjacent is why it is worth a sentence: one is the mail installation
    and one is the SMS installation, they are configured separately, and this is one
    of only two places both are in scope. An install with SMTP and no Termii must
    refuse here while still resetting passwords by mail, and it will - the builder
    reads only what it was given.
    """
    return build_request_phone_verification(
        unit_of_work_factory=request.app.state.unit_of_work_factory,
        settings=request.app.state.termii,
    )


def confirm_phone_sign_up_service(request: Request) -> ConfirmPhoneSignUp:
    """Answering a phone signup, which has no actor because there is nobody yet.

    **The shortest dependency of the identity group** - no actor, no channel, no
    settings - and each absence means something slightly different. No actor, for
    the reason above. No channel because this half sends nothing: there is no
    second message to send, which ``phoneVerificationMessage`` argues at length. No
    settings because by the time a confirm is reachable a channel already existed
    on the request half, so there is nothing a missing one could refuse; the same
    shape ``confirm_password_reset_service`` has for the notice it does not send.

    It takes the hasher where the request above takes none, and the pair is worth
    reading together: the password is chosen at the answer, so this is the half that
    *writes* a credential. The hasher comes off ``app.state``, exactly as
    ``sign_up_service``'s does, so the argon2 adapter is still constructed in
    exactly one place.
    """
    return build_confirm_phone_sign_up(
        unit_of_work_factory=request.app.state.unit_of_work_factory,
        password_hasher=request.app.state.password_hasher,
    )


def payment_provider(request: Request) -> PaymentProvider:
    """The provider this installation collects through, or a 503.

    **The one dependency here that answers a question about the installation
    rather than about the caller**, and it is worth naming because every other
    function in this module produces a *service for somebody*. This produces the
    adapter itself, because two routes need it for two different reasons and
    neither reason is an actor: the deposit route needs something to open a
    collection with, and the webhook needs something to verify a signature
    against.

    **``None`` is refused here rather than passed on**, which is the entire
    content of the function. ``provider_for`` returns ``None`` for an install
    with no key, deliberately - it is a state, not a failure - and the two
    callers would each have to branch on it. Branching here means the refusal is
    written once, at the only door that can produce a provider, and no code
    below has to ask whether the thing it is holding exists. The same shape as
    ``bearer_token``: the check lives where the value is produced, so nothing
    downstream has to be careful.

    A **503**, not a 401, and ``errors.PaymentsUnconfiguredError`` argues why at
    length. Short version: the installation is not ready, which is a different
    sentence from the caller being unproven - and the difference decides whether
    Paystack retries or gives up.
    """
    provider = request.app.state.payment_provider
    if provider is None:
        raise PaymentsUnconfiguredError(
            "this installation has no payment provider configured"
        )
    return provider


def deposit_service(
    request: Request,
    actor: User = Depends(current_actor),
    provider: PaymentProvider = Depends(payment_provider),
) -> InitiateDeposit:
    """The deposit use case, acting as this request's actor, over this provider.

    Two dependencies rather than one, and the pair is the point: the actor
    scopes the wallet and supplies the payer's email, and the provider is what
    the money will arrive through. Neither can stand in for the other - a
    provider with no actor cannot say whose wallet to credit, and an actor with
    no provider cannot open a collection - so a missing payment key is a refusal
    *before* any wallet is read, which is the correct order for a request that
    can only fail.

    ``settings`` is ``app.state.paystack``, not ``app.state.settings``, and the
    two names being adjacent is exactly why it is worth saying: one is the mail
    installation and one is the payment installation, they are configured
    separately, and this is the one place both are in scope.
    """
    return build_initiate_deposit(
        unit_of_work_factory=request.app.state.unit_of_work_factory,
        settings=request.app.state.paystack,
        actor=actor.user_id,
        provider=provider,
    )


def settler_service(request: Request) -> SettlePayment:
    """Settlement, which has no actor and therefore no ``current_actor`` here.

    **This is the only service in the module built without one**, and the
    absence is not an oversight to be tidied later - it is the shape of the
    thing. ``SettlePayment`` cannot be told who is acting, because a provider
    reporting a movement is not a person and there is no honest value to give
    it. Ownership is established inside the use case, from the ledger row it is
    settling, through ``WalletRepository.owner_of`` - so the money is still only
    ever read as its owner's, and the guarantee holds by a different route
    rather than by an exemption. See ``SettlePayment``'s class docstring.

    ``settings`` is the *mail* settings, so the receipt a settled movement earns
    is addressed the way every other receipt in this system is. Note the
    contrast with ``deposit_service`` above: the money arrives through Paystack
    and the confirmation goes out through SMTP, and a builder taking one settings
    object for both would be the place the two quietly became one.
    """
    return build_settler(
        unit_of_work_factory=request.app.state.unit_of_work_factory,
        settings=request.app.state.settings,
    )
