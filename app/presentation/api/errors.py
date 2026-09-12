"""How a domain refusal becomes an HTTP response.

The CLI collapses this whole tree to one line - ``error: ...`` and exit 1 - and
that is the right answer for a terminal with one user watching. HTTP has to be
more precise, because the status code is what a client *acts* on: a retry, a
re-authentication, a message to show somebody. So the grades below are part of
the contract rather than a rendering detail.

They are graded by **what kind of question was refused**, not by listing classes
as they come up:

    401  we do not know who is asking, or the proof offered did not hold
    404  the resource is not there *for this actor*
    409  the resource is there, and its current state refuses this
    400  something in the request is not acceptable
    422  the request did not have the shape the endpoint declares (FastAPI's own)
    500  a bug

Four of those six are defaults rather than lists. ``UNAUTHORIZED``,
``NOT_FOUND`` and ``CONFLICT`` are the three exceptions worth naming, and
everything else that is a ``MoneyError`` is a 400 - which is the safe direction
to be wrong in. A refusal this module has never heard of is far more likely to be
about a value the caller sent than about a resource's state, and a 400 tells the
caller to look at their request rather than to retry.

**401 sits above 404 because it is a different kind of question.** The three
below it are all answers about a resource - there, not there, refusing - and they
are only askable once the caller is known. A 401 says the question never got that
far, which is why no endpoint in this API returns both for the same request: an
unauthenticated caller learns nothing about what exists, and the isolation
guarantee that makes a foreign wallet a 404 is not reachable at all until a
session has been resolved.

**``WalletNotFoundError`` is matched before the generic case**, and that
ordering is the whole reason this module is not three lines. It is a
``MoneyError``, so a handler registered only for the root would turn it into a
400 and tell a client that a wallet it cannot see is a badly-formed request.

**The 404 is where Phase 1a's guarantee reaches the wire.** A wallet belonging to
somebody else raises the *same* exception as one that does not exist - there is
no branch between them anywhere in the domain - and the handler below must not
reintroduce a distinction that a phase was spent removing. It does not, because
it has nothing to distinguish: it prints the class name and the message, and both
are identical for the two cases. That is asserted in
``tests/presentation/api/test_isolation.py``, which is the test that would fail
if somebody later added a friendlier message for the foreign case.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.domain.identity.exception import (
    DuplicateEmailError,
    InvalidCredentialsError,
    InvalidSessionError,
    UserNotFoundError,
)
from app.domain.money.exception import (
    DuplicateFundNameError,
    FundNotFoundError,
    FundNotMaturedError,
    InsufficientFundsError,
    MaturityNotExtendedError,
    MoneyError,
    TransactionNotFoundError,
    WalletAlreadyActiveError,
    WalletAlreadyClosedError,
    WalletAlreadyFrozenError,
    WalletClosedError,
    WalletFrozenError,
    WalletNotFoundError,
)
from app.domain.planning.exception import (
    CommittedPayoutRemovalError,
    IrreversibleReleasePlanError,
    PlanAlreadyFinishedError,
    PlanNotActiveError,
    PlanNotPausedError,
    SavingsPlanNotFoundError,
)

#: We do not know who is asking, or the proof offered did not hold.
#:
#: Two classes, and they are the same answer to a client: present a token, or
#: present a better one. ``InvalidSessionError`` is a token that is unknown,
#: expired or orphaned - the domain keeps those one refusal deliberately, so this
#: list cannot split them even if it wanted to. ``InvalidCredentialsError`` is a
#: login whose address and password did not match, which arrives at the same
#: status for a different reason: the request asked to *become* somebody and the
#: answer is that it did not.
#:
#: **The 401 that goes out carries no ``WWW-Authenticate`` header.** RFC 9110
#: asks for one on a 401, and its job is to tell a client which scheme to satisfy
#: so it can retry without being told. This API has exactly one scheme, it is
#: documented in the OpenAPI description, and no client of it negotiates - so the
#: header would be a formality that the error body's ``error`` field already
#: answers more precisely. Worth naming rather than leaving for somebody to
#: notice from a spec-compliance tool.
UNAUTHORIZED = (InvalidSessionError, InvalidCredentialsError)

#: The resource is not there for the actor asking. One status, one body, whatever
#: the reason - see the module docstring.
NOT_FOUND = (
    WalletNotFoundError,
    TransactionNotFoundError,
    FundNotFoundError,
    UserNotFoundError,
    SavingsPlanNotFoundError,
)

#: The resource is there and its current state refuses this. Note every one of
#: these is a statement about a row that already exists: a pot whose name is
#: taken, a plan that is not paused, a promise that cannot be revoked. None of
#: them is about the request being malformed - the request is fine, and the
#: answer is "not from where you are standing".
#:
#: ``DuplicateEmailError`` is the newest and the one that fits least obviously.
#: The row that exists is a *user*, and the request was to create one - so this
#: is a 409 about a resource the caller never named, arrived at by an address.
#: It belongs here anyway: the request is well formed, the address is a real
#: address, and the state of the world is what refuses it. 400 would blame the
#: caller for a value that is fine; the alternative of reporting success would
#: leave them unable to log in and unsure why.
CONFLICT = (
    InsufficientFundsError,
    WalletFrozenError,
    WalletClosedError,
    WalletAlreadyFrozenError,
    WalletAlreadyClosedError,
    WalletAlreadyActiveError,
    FundNotMaturedError,
    MaturityNotExtendedError,
    DuplicateFundNameError,
    DuplicateEmailError,
    PlanNotActiveError,
    PlanNotPausedError,
    PlanAlreadyFinishedError,
    IrreversibleReleasePlanError,
    CommittedPayoutRemovalError,
)


class ApiError(Exception):
    """A refusal this layer makes, with a status code attached.

    Deliberately *not* a ``MoneyError``. Everything under that root is a
    statement the domain makes about money, and the identity of a request - which
    header carried it, whether it was there at all - is not something the domain
    has an opinion about. Deriving from it to reuse the handler would put a
    transport concern inside the domain's exception tree, which is the one place
    this codebase has been careful to keep it out of.
    """

    status_code = 500

    def __init__(self, detail: str = ""):
        super().__init__(detail)
        self.detail = detail


class MissingCredentialsError(ApiError):
    """No ``Authorization`` header on a request that needs to know who is asking.

    A 401, which is what its predecessor's docstring promised it would become:
    ``MissingActorHeaderError`` was a 400 because there was no authentication
    scheme to satisfy, and a 401 would have pointed a client at a challenge that
    did not exist. There is one now, so the honest status is the one that means
    "prove who you are and come back".

    Only the *presence* of the header is this layer's business. A header that is
    there but malformed, or that carries a token nothing recognises, goes to
    ``ResolveActorFromSession`` and comes back as ``InvalidSessionError`` - a 401
    through the domain's own handler, with the domain's own words. So the two
    ways to fail produce one status and two ``error`` names, and the difference
    is exactly the one a client acts on: "you sent nothing" against "what you
    sent was not good enough".
    """

    status_code = 401


def _grade(exc: MoneyError) -> int:
    """The status for a domain refusal, by kind rather than by name."""
    if isinstance(exc, UNAUTHORIZED):
        return 401
    if isinstance(exc, NOT_FOUND):
        return 404
    if isinstance(exc, CONFLICT):
        return 409
    return 400


def _detail(exc: BaseException) -> str:
    """The message, or the class name when the domain raised without one.

    The fallback is not decoration. ``WalletNotFoundError`` is raised bare - see
    ``SqliteWalletRepository`` - so without this every 404 would come back with an
    empty ``detail``, and a client would have a status code and nothing else to
    show. ``app.presentation.cli`` does the same thing for the same reason, in
    ``_describe``; the two presentations render the same refusal the same way,
    which is what makes it recognisable across them.
    """
    return str(exc) if str(exc) else type(exc).__name__


def _body(name: str, detail: str) -> dict:
    """The one response shape every failure uses.

    ``error`` is the exception's class name, and it is the field a client
    branches on; ``detail`` is prose for a human. Both are present on every
    failure, including the ones this layer raises itself, so a client never has
    to parse two shapes.
    """
    return {"error": name, "detail": detail}


async def money_error_handler(request: Request, exc: MoneyError) -> JSONResponse:
    """Render a domain refusal.

    ``async`` while the endpoints are not, and the difference is deliberate
    rather than an oversight. An endpoint does blocking SQLite work and belongs
    in FastAPI's threadpool, which is what a plain ``def`` gets it. A handler
    does no I/O at all - it formats a string - so there is nothing to hand off,
    and making it async keeps it on the event loop where it costs nothing.
    """
    return JSONResponse(
        status_code=_grade(exc), content=_body(type(exc).__name__, _detail(exc))
    )


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_body(type(exc).__name__, _detail(exc)),
    )


async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """The 500, with nothing of the exception in it.

    **The message is deliberately not echoed**, which is the opposite of what
    every handler above does. A domain refusal is a sentence written for a user:
    it names a pot, or an amount, or a plan, and it is safe to show. An
    unexpected exception is a sentence written for whoever is debugging it, and
    it can carry a file path, a SQL fragment, or a value out of a row that
    belongs to somebody else. So the response says only that something broke, and
    the exception goes to the server's log where it belongs.
    """
    return JSONResponse(
        status_code=500, content=_body("InternalServerError", "internal server error")
    )


def install(app: FastAPI) -> None:
    """Register every handler, in the order that matters.

    Registration order does not decide matching - Starlette walks the exception's
    MRO and picks the most specific registered class - so listing ``MoneyError``
    before ``Exception`` here is documentation rather than mechanism. It is worth
    having anyway, because "the specific ones first" is the property a reader
    needs to check and the code should be readable as the rule it obeys.
    """
    app.add_exception_handler(MoneyError, money_error_handler)
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)
