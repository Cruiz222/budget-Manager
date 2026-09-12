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
"""

from datetime import datetime

from fastapi import Depends, Request

from app.application.identity.log_in import LogIn
from app.application.identity.log_out import LogOut
from app.application.identity.resolve_actor import ResolveActorFromSession
from app.application.identity.sign_up import SignUp
from app.application.plan_service import PlanService
from app.application.wallet_service import WalletService
from app.composition_root import (
    build_log_in,
    build_log_out,
    build_plan_service,
    build_resolve_actor,
    build_sign_up,
    build_wallet_service,
)
from app.domain.identity.user import User
from app.presentation.api.errors import MissingCredentialsError

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


def sign_up_service(request: Request) -> SignUp:
    """Registration, which is one of the two things reachable without a token.

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
    """Login, the other thing reachable without a token, for the same reason.

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
