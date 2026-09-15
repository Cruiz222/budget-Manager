"""The application factory, and the one place this presentation is configured.

**Injection works the way ``app.composition_root`` already works**: every input
is an optional argument, ``None`` means "the real thing", and a test passes its
own. That is not a coincidence - it is the same seam, deliberately, because two
ways to point the system at a database would be one too many and the second one
is always the one that is wrong.

What ``create_app`` puts on ``app.state`` is the whole of the configuration: a
unit of work factory and the mail settings. Both are resolved once, when the app
is built, and never per request - which is what makes a running server act on one
configuration rather than on whatever the environment happened to be when a
request arrived.

**Nothing here is async, and nothing is decorated to look it.** Every service
below is synchronous and SQLite is synchronous, so FastAPI runs the ``def``
endpoints in its threadpool - which is the correct execution model for blocking
work. Writing ``async def`` would not make them concurrent; it would run them on
the event loop and block every other request while one query ran. The
distinction is easy to get backwards, and getting it backwards is invisible until
there is load.
"""

from fastapi import FastAPI

from app.composition_root import provider_for
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)
from app.infrastructure.security.argon2_password_hasher import Argon2PasswordHasher
from app.infrastructure.settings import database_path as configured_database_path
from app.infrastructure.settings import from_environment, paystack_from_environment
from app.presentation.api import errors
from app.presentation.api.routes import (
    confirmations,
    email_changes,
    funds,
    health,
    password_resets,
    plans,
    sessions,
    users,
    wallets,
    webhooks,
)


def create_app(
    unit_of_work_factory=None,
    settings=None,
    database_path=None,
    password_hasher=None,
    paystack_settings=None,
    payment_provider=None,
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

    application.state.unit_of_work_factory = unit_of_work_factory
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

    errors.install(application)
    for module in (
        health,
        users,
        sessions,
        email_changes,
        password_resets,
        wallets,
        funds,
        plans,
        confirmations,
        webhooks,
    ):
        application.include_router(module.router)

    return application
