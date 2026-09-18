"""The application factory, and the one place this presentation is configured.

**Injection works the way ``app.composition_root`` already works**: every input
is an optional argument, ``None`` means "the real thing", and a test passes its
own. That is not a coincidence - it is the same seam, deliberately, because two
ways to point the system at a database would be one too many and the second one
is always the one that is wrong.

What ``create_app`` puts on ``app.state`` is the whole of the configuration: a
unit of work factory, the mail settings, the payment settings and the provider
built from them, the SMS settings, the Google settings and verifier, and the rate
limiter. All are resolved once, when the app is built, and never per request -
which is what makes a running server act on one configuration rather than on
whatever the environment happened to be when a request arrived.

**The rate limiter is the one of those that is not inert**, and the difference is
worth stating up front because it is the reason this module now has a lifespan and
a thread where it had neither. Everything else on ``app.state`` is a value that is
read; the limiter is an object that owns a background flusher, and something has
to start it and something has to stop it. That is what ``lifespan`` below is, and
it is the only lifecycle this application has.

**Nothing here is async, and nothing is decorated to look it.** Every service
below is synchronous and SQLite is synchronous, so FastAPI runs the ``def``
endpoints in its threadpool - which is the correct execution model for blocking
work. Writing ``async def`` would not make them concurrent; it would run them on
the event loop and block every other request while one query ran. The
distinction is easy to get backwards, and getting it backwards is invisible until
there is load.

**This module logs, for exactly one reason, and it is not diagnostics.** A
deployment configured with a *live* Paystack key takes real money from real
cards, and there is nothing about running it that says so - both modes use the
same host, the same endpoints and the same code, and are told apart only by which
secret key was put in the environment. A live key where somebody meant to put a
test one is the one configuration mistake in this system that moves money, and it
is silent in both directions. So the mode is printed once at boot. Nothing
branches on it; see ``_report_payment_mode``.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.composition_root import google_verifier_for, provider_for
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)
from app.infrastructure.rate_limiting import (
    BackgroundFlusher,
    InMemoryRateCounter,
    TwoLayerRateLimiter,
)
from app.infrastructure.security.argon2_password_hasher import Argon2PasswordHasher
from app.infrastructure.settings import database_path as configured_database_path
from app.infrastructure.settings import (
    from_environment,
    google_from_environment,
    paystack_from_environment,
    rate_limit_flush_seconds as configured_flush_interval,
    termii_from_environment,
)
from app.presentation.api import errors
from app.presentation.api.rate_limits import longest_window
from app.presentation.api.routes import (
    confirmations,
    email_changes,
    funds,
    health,
    password_resets,
    phone_verifications,
    plans,
    profiles,
    sessions,
    users,
    wallets,
    webhooks,
)


logger = logging.getLogger(__name__)


def _report_payment_mode(application: FastAPI) -> None:
    """Say, once at boot, whether this process can take real money.

    **The only configuration fact this system prints**, and the reason it is this
    one is that it is the only mistake here that is both silent and expensive.
    Paystack's sandbox and its live service are the same host and the same
    endpoints; a deployment is in one mode or the other solely by which secret
    key was exported, and nothing else about a running server distinguishes them.
    A live key exported into somebody's staging shell takes real money from real
    cards while every log line, every test and the dashboard all look like
    testing - and the operator finds out from a card statement.

    **Nothing branches on the value.** This function does not refuse to start on
    a live key, because a live key is the correct state of a production
    deployment and refusing would take the money down to protect it. It does not
    refuse an unrecognised key either, for the reason ``PaystackSettings.mode``
    gives: this is a courtesy read of a format this code does not own, and a
    deployment whose key Paystack has changed the shape of must keep serving.
    What it does is put one line where an operator already looks.

    ``info`` for a mode that is known, because both are states the deployment is
    supposed to be in - and ``warning`` for a key whose mode cannot be read off
    it, because that is the one case where the answer to "are we taking real
    money" is *nobody can tell from here*, which is a thing somebody should fix.
    A deployment with no key at all says so at ``info``: payments being off is
    the ordinary state of a fresh clone, not a fault.
    """
    settings = application.state.paystack
    if settings is None:
        logger.info("payments are off: no PAYSTACK_SECRET_KEY is set")
        return

    if settings.mode == "unrecognised":
        logger.warning(
            "PAYSTACK_SECRET_KEY does not begin with 'sk_test_' or 'sk_live_', so "
            "whether this deployment takes real money cannot be read off it - "
            "check the key against the Paystack dashboard"
        )
        return

    logger.info(
        "payments are configured against Paystack's %s service%s",
        settings.mode,
        " - this deployment takes real money" if settings.mode == "live" else "",
    )


def create_app(
    unit_of_work_factory=None,
    settings=None,
    database_path=None,
    password_hasher=None,
    paystack_settings=None,
    payment_provider=None,
    termii_settings=None,
    google_settings=None,
    google_verifier=None,
    rate_limit_flush_seconds=None,
) -> FastAPI:
    """Build the application.

    ``unit_of_work_factory`` - where data lives. ``None`` builds a real
    ``SqliteUnitOfWorkFactory`` over ``database_path``; a test passes a factory
    pointed at a temporary file.

    ``database_path`` - the SQLite file, used only when no factory was injected.
    ``None`` resolves through ``app.infrastructure.settings``, so an installation
    that sets nothing gets the ``budget.db`` the CLI has always used and the two
    presentations are looking at one set of money rather than two. Read once, at
    startup.

    ``settings`` - the mail settings, an ``EmailSettings``. ``None`` means "read
    this installation's configuration", which is the same thing the CLI's
    ``main`` does at the top of an invocation. It is passed to the wallet service
    builder even though nothing reachable in this phase sends mail, so that when
    Phase 2 exposes a deposit the receipt is already wired and nobody has to
    notice it was not.

    ``password_hasher`` - the ``PasswordHasher`` port. ``None`` builds the argon2
    adapter, which is what any real installation runs. It is a parameter for the
    same reason ``unit_of_work_factory`` is - it is the seam a test substitutes -
    and the substitute matters more here than usual. The real adapter is
    *designed* to be slow: tens of milliseconds per hash, at default parameters,
    deliberately. A suite that registers and logs in in order to test something
    about wallets would spend most of its wall clock hashing passwords it does
    not care about.

    Note what is *not* parameterised: nothing chooses a weaker algorithm because
    of an environment. A configurable hash is a hash that can be configured wrong
    in production, and all a test needs is to skip the work, not to change what
    the work is.

    ``paystack_settings`` - the payment settings, a ``PaystackSettings``. ``None``
    means "read this installation's configuration", and an installation without a
    key resolves to ``None`` - which is a *supported state* rather than a broken
    one. It is not the same kind of absence the mail settings' ``None`` is,
    though, and the difference is the reason the next paragraph exists: no mail
    address means this installation says nothing, and no payment key means it
    takes nothing. See ``routes/webhooks.py`` for what "takes nothing" looks like
    from outside - a 503, so the provider retries rather than giving up.

    ``payment_provider`` - the ``PaymentProvider`` port, for the same reason
    ``unit_of_work_factory`` and ``password_hasher`` are parameters: it is the
    seam a test substitutes. ``None`` builds the Paystack adapter from
    ``paystack_settings``, or resolves to ``None`` when there are no settings to
    build it from. **The substitute matters more here than anywhere else in this
    file**, because the real adapter opens sockets - and it is not only the
    deposit route that is kept off the network by it: the webhook's signature
    check is *also* the adapter's, so this one seam is what lets a test drive the
    real verification path without a provider account.

    Both are resolved together rather than independently, and the pair is
    deliberately not collapsed into one parameter. Settings without a provider is
    an installation that *can* take payments; a provider without settings is a
    test. One parameter could not say which of those it meant.

    ``termii_settings`` - the SMS settings, a ``TermiiSettings``. ``None`` means
    "read this installation's configuration", and an installation without both
    ``TERMII_API_KEY`` and ``TERMII_SENDER_ID`` resolves to ``None`` - a supported
    state, and the state a fresh clone is in. It is the mail settings' ``None``
    rather than the payment one's: this installation simply cannot text, and the
    flows that need a text refuse in words naming the missing variable.

    **There is no matching ``sms_channel`` parameter, and the asymmetry with
    ``payment_provider`` above is worth a sentence.** A provider has to exist as an
    object on ``app.state`` because two routes need it directly - one to open a
    collection with, one to verify a signature against. Nothing here holds a
    channel: both halves that could send a text go through a builder, which
    constructs the adapter per request exactly as the mail builders always have.
    So the seam for a test is the same one the mail tests use - the channel builder
    inside ``composition_root`` - rather than a fourth thing on ``app.state`` that
    no route would ever read.

    ``google_settings`` - the Google configuration, a ``GoogleSettings``. ``None``
    means "read this installation's configuration", and an installation without
    ``GOOGLE_CLIENT_ID`` resolves to ``None`` - a supported state, and the state a
    fresh clone is in. It is Termii's ``None`` rather than mail's: this
    installation cannot judge a Google token, and there is no "do without it"
    available for a flow whose entire input is that token.

    ``google_verifier`` - the ``GoogleIdentityVerifier`` port, and it is a
    parameter for the reason ``payment_provider`` is: it is the seam a test
    substitutes. The contrast with ``termii_settings`` above is the one worth
    drawing out, because the two look like the same kind of thing and are not.
    Nothing on ``app.state`` holds an SMS channel; a channel is built per request
    inside a builder, because each send is a fresh conversation and there is
    nothing to keep. **A verifier is kept, and it is kept on purpose**, because the
    adapter holds Google's signing keys in memory and rotates them itself -
    building one per request would re-fetch the JWKS on every sign-in, which is
    slower and is a way to be rate-limited by a provider whose keys have not
    changed. So this is the payment provider's shape rather than the SMS channel's,
    and ``google_verifier_for`` carries the argument.

    ``None`` for both is a supported state rather than an error, and the two are
    resolved together rather than independently - settings without a verifier is an
    installation that *could* verify, a verifier without settings is a test. The
    same pair-wise reasoning ``paystack_settings``/``payment_provider`` gets above.

    ``rate_limit_flush_seconds`` - how often the rate limiter writes its counters
    down, and ``0`` for no flusher at all. ``None`` resolves through
    ``app.infrastructure.settings``, which defaults to thirty seconds, so **an
    application built with no arguments - which is how both production call sites
    build one - persists its counters.** That default is deliberate and the
    alternative was rejected: making the flusher opt-in would mean the documented
    ``uvicorn app.presentation.api.app:create_app --factory`` invocation, which
    cannot pass a keyword, silently ran the one configuration that loses a budget
    on restart.

    So the test suite is the side that opts *out*, in ``tests/conftest.py``, where
    it sets the variable to zero for every test. A thread per test application
    would be hundreds of threads, and each would perform a real write at shutdown -
    a cost paid to demonstrate something the cold layer's own tests demonstrate
    directly by calling ``flush``. This parameter is the way back in for the test
    that wants the real lifecycle.
    """
    application = FastAPI(
        title="Budget Manager",
        description=(
            "A personal savings wallet with named locked pots and scheduled "
            "plans.\n\n"
            "**Who is asking is carried in an `Authorization: Bearer <token>` "
            "header**, and the token comes from `POST /sessions`. Register with "
            "`POST /users`, exchange those credentials for a token, and send it "
            "with every other request; `DELETE /sessions/current` ends it. "
            "`GET /users/me` and `GET /health` are the two endpoints that answer "
            "without any of that - the first only to tell you your token is no "
            "longer good.\n\n"
            "There is no way to act as somebody else. A wallet belonging to "
            "another account is not refused, it is *not there* - the same 404 a "
            "wallet that never existed gets, with the same body."
        ),
    )

    if unit_of_work_factory is None:
        unit_of_work_factory = SqliteUnitOfWorkFactory(
            database_path or configured_database_path()
        )

    flush_seconds = (
        configured_flush_interval()
        if rate_limit_flush_seconds is None
        else rate_limit_flush_seconds
    )
    rate_limiter = TwoLayerRateLimiter(
        InMemoryRateCounter(),
        unit_of_work_factory,
        # The longest window any policy uses, read off the policy table rather
        # than chosen - see ``rate_limits.longest_window``. A shorter retention
        # would sweep a row whose window was still live, which would hand a
        # caller a fresh budget in the middle of their window.
        retention=longest_window(),
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        """Warm the limiter, run its flusher, and stop it cleanly.

        **This is the first lifespan in this application and the first thread in
        ``app/``**, so it is worth being explicit about what it does and does not
        guarantee. It does three things in order: adopt the counts the last
        process flushed, start a daemon thread that flushes every
        ``flush_seconds``, and - on the way out - stop that thread and flush once
        more so a clean shutdown loses nothing. ``BackgroundFlusher`` carries the
        honest limits of the arrangement, including what a crash costs.

        **Failing to warm does not stop the application.** A database that cannot
        be read is a database whose wallets cannot be read either, and this
        application's answer to that state is to serve and report it - ``/health``
        is a route precisely because an installation with an unreachable store is
        a supported state rather than a dead one. Refusing to boot over a counter
        table would take the money down to protect the rate limiter, which is the
        wrong way round. The failure is kept on
        ``application.state.rate_limit_warm_error`` instead, because the honest
        cost of carrying on is that a restart may hand back part of a budget - a
        thing an operator should be able to find out, and the attribute is the
        only place it is recorded. Like ``BackgroundFlusher.last_error``, this is
        what the missing logging would otherwise report.

        ``warm`` reads the database synchronously on the event loop. That is a
        deliberate exception to the rule the module docstring gives about blocking
        work, and it is safe for the one reason that makes any blocking call safe
        in an async context: it happens once, before the server accepts anything,
        so there is no other request for it to block. Moving it to the threadpool
        would add machinery to make a single ``SELECT`` concurrent with nothing.
        """
        try:
            # Read before the thread starts, so the ordering is unambiguous: the
            # flusher cannot write this process's empty view of the world on top
            # of what the last one stored.
            rate_limiter.warm()
            application.state.rate_limit_warm_error = None
        except Exception as exc:  # see the docstring: boot must not depend on it
            application.state.rate_limit_warm_error = exc

        _report_payment_mode(application)

        flusher = None
        if flush_seconds > 0:
            flusher = BackgroundFlusher(rate_limiter, flush_seconds)
            flusher.start()
        try:
            yield
        finally:
            if flusher is not None:
                flusher.stop()

    application = FastAPI(
        title="Budget Manager",
        lifespan=lifespan,
        description=(
            "A personal savings wallet with named locked pots and scheduled "
            "plans.\n\n"
            "**Who is asking is carried in an `Authorization: Bearer <token>` "
            "header**, and the token comes from `POST /sessions`. Register with "
            "`POST /users`, exchange those credentials for a token, and send it "
            "with every other request; `DELETE /sessions/current` ends it. "
            "`GET /users/me` and `GET /health` are the two endpoints that answer "
            "without any of that - the first only to tell you your token is no "
            "longer good.\n\n"
            "There is no way to act as somebody else. A wallet belonging to "
            "another account is not refused, it is *not there* - the same 404 a "
            "wallet that never existed gets, with the same body."
        ),
    )

    application.state.unit_of_work_factory = unit_of_work_factory
    application.state.rate_limiter = rate_limiter
    # Always set, so that "was the warm start clean?" is a question with an
    # answer rather than one with a missing attribute. ``None`` means it was.
    application.state.rate_limit_warm_error = None
    application.state.settings = (
        from_environment() if settings is None else settings
    )
    application.state.password_hasher = (
        Argon2PasswordHasher() if password_hasher is None else password_hasher
    )
    application.state.paystack = (
        paystack_from_environment() if paystack_settings is None else paystack_settings
    )
    application.state.payment_provider = (
        provider_for(application.state.paystack)
        if payment_provider is None
        else payment_provider
    )
    application.state.termii = (
        termii_from_environment() if termii_settings is None else termii_settings
    )
    application.state.google = (
        google_from_environment() if google_settings is None else google_settings
    )
    application.state.google_verifier = (
        google_verifier_for(application.state.google)
        if google_verifier is None
        else google_verifier
    )

    errors.install(application)
    for module in (
        health,
        users,
        profiles,
        sessions,
        email_changes,
        password_resets,
        phone_verifications,
        wallets,
        funds,
        plans,
        confirmations,
        webhooks,
    ):
        application.include_router(module.router)

    return application
