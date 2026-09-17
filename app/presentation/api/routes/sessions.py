"""Becoming somebody, and ceasing to be them.

The two endpoints that hand a token out and take it away, kept in a file of
their own because they are about the *session* rather than the account. ``users``
creates an identity and reports it; this creates the proof that a request is that
identity, which is a different resource with a different lifetime - a user lasts
until it is deleted, a session lasts thirty days.

Nothing here is under ``/users`` for that reason. A path nested under the account
would suggest a session is a part of one, and the operation that matters most -
signing out - is available to a caller who cannot be resolved to an account at
all, since the token being discarded may already be dead.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Response, status

from app.application.identity.log_in import LogIn
from app.application.identity.log_out import LogOut
from app.presentation.api import schemas, translate
from app.presentation.api.dependencies import (
    bearer_token,
    log_in_service,
    log_out_service,
)

router = APIRouter(tags=["identity"])


@router.post(
    "/sessions",
    response_model=schemas.SessionOut,
    status_code=status.HTTP_201_CREATED,
)
def log_in(
    body: schemas.LogInIn, service: LogIn = Depends(log_in_service)
) -> schemas.SessionOut:
    """Exchange an address or a number, and a password, for a token.

    **This is the only endpoint in the API that returns a secret, and it returns
    it once.** There is no endpoint that reads a session back out, so a client
    that loses the token has no way to recover it and must log in again - which
    is the property that makes a leaked session table useless rather than
    catastrophic. The server holds only a SHA-256 of the token; the plaintext
    exists in this response and nowhere else at any point in the system's life.

    **201, because a session is created** - it is a row with an id, an owner and
    an expiry, and this request is what brought it into being. No ``Location``
    header, deliberately: there is no URL at which the session can be fetched,
    and pointing at one that does not exist would be worse than pointing nowhere.

    A wrong password and an unknown identifier are the *same* response here -
    401 with ``InvalidCredentialsError`` - because distinguishing them would turn
    this endpoint into a way to test whether an address or a number is registered.
    The same answer covers the third case that arrived with numbers: a phone
    presented for an account whose identifier is an address. See ``LogIn``, which
    also records what has changed about that protection since this step - the
    *timing* difference between the branches is spent away now, and rate limiting
    remains owed.

    **The two identifiers are two branches, not one lookup that sniffs its
    argument**, and ``LogInIn``'s validator is what makes this branch total: the
    body names exactly one of the two, so the ``else`` cannot see a body that
    named a number. ``LogIn.execute`` accepts an absent address anyway, so the
    failure mode if that ever stopped being true is the 401 below rather than a
    500 - the invariant is enforced where it is stated, and not relied on three
    layers down.

    ``datetime.now()`` is read here rather than inside the use case, following
    ``current_actor``: the boundary is where the wall clock enters, so every
    moment-sensitive rule underneath stays a comparison that a test can drive.
    """
    now = datetime.now()
    if body.phone is not None:
        logged_in = service.execute_for_phone(body.phone, body.password, now)
    else:
        logged_in = service.execute(body.email, body.password, now)
    return translate.session_out(logged_in)


@router.delete("/sessions/current", status_code=status.HTTP_204_NO_CONTENT)
def log_out(
    service: LogOut = Depends(log_out_service),
    token: str = Depends(bearer_token),
) -> Response:
    """Discard the token this request presented.

    **It takes the token and not an actor**, which is the one endpoint here that
    does. Everywhere else the order is "resolve who is asking, then decide
    whether they may"; here the caller names the thing to destroy by presenting
    the only proof that they hold it, so resolving an actor first would add a
    failure mode without adding a check. That failure mode is the point: an
    expired token cannot be resolved, and refusing to sign out somebody whose
    session has just lapsed would leave them stuck with a token they cannot get
    rid of - see ``LogOut``, which argues this at length.

    **204, and idempotent.** A token that was never valid, one that expired, and
    one that has already been signed out all produce this same empty success,
    because in every one of those cases the postcondition - "this token does not
    authenticate" - is now true. The alternative, a 401 for a token that does not
    resolve, would report a failure to a caller whose intent was satisfied.

    ``response_class=Response`` and an explicit empty response, rather than
    returning ``None`` with a 204 status. FastAPI's default JSON rendering would
    try to serialise the ``None`` into a body, and a 204 is defined to carry
    none - so a client that read the body would see a literal ``null`` where the
    spec says there is nothing. Declaring the response class is what makes the
    body genuinely empty.
    """
    service.execute(token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
