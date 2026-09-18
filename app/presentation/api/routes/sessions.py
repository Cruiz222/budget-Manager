"""Becoming somebody, and ceasing to be them.

The endpoints that hand a token out and take it away, kept in a file of
their own because they are about the *session* rather than the account. ``users``
creates an identity and reports it; this creates the proof that a request is that
identity, which is a different resource with a different lifetime - a user lasts
until it is deleted, a session lasts thirty days.

**Two of the three hand one out, and the pair is the shape of this file's most
recent addition.** ``POST /sessions`` and ``POST /sessions/google`` produce the
same resource from two different proofs - a password the caller typed against a
Google signature over an assertion somebody else made - and they are two routes
rather than one with a branch because the proofs have nothing in common: one is a
secret this system stored, and the other is a value checked against a third
party's published keys. What they share is the answer, so they share ``LoggedIn``
and the translation below, and a client cannot tell from the body which one it
used - which is correct, since the token is the same kind of token either way.

Nothing here is under ``/users`` for that reason. A path nested under the account
would suggest a session is a part of one, and the operation that matters most -
signing out - is available to a caller who cannot be resolved to an account at
all, since the token being discarded may already be dead.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Response, status

from app.application.identity.log_in import LogIn
from app.application.identity.log_in_with_google import LogInWithGoogle
from app.application.identity.log_out import LogOut
from app.presentation.api import schemas, translate
from app.presentation.api.dependencies import (
    bearer_token,
    log_in_rate_limit,
    log_in_service,
    log_in_with_google_rate_limit,
    log_in_with_google_service,
    log_out_service,
)

router = APIRouter(tags=["identity"])


@router.post(
    "/sessions",
    response_model=schemas.SessionOut,
    status_code=status.HTTP_201_CREATED,
)
def log_in(
    body: schemas.LogInIn,
    _: None = Depends(log_in_rate_limit),
    service: LogIn = Depends(log_in_service),
) -> schemas.SessionOut:
    """Exchange an address or a number, and a password, for a token.

    **This is one of two endpoints that return a secret, and both return it
    once.** The other is ``POST /sessions/google`` below, which is the same act
    reached by a different proof and answers with the same ``SessionOut``. There
    is no endpoint that reads a session back out, so a client that loses the
    token has no way to recover it and must log in again - which is the property
    that makes a leaked session table useless rather than catastrophic. The server
    holds only a SHA-256 of the token; the plaintext exists in a response and
    nowhere else at any point in the system's life.

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
    *timing* difference between the branches is spent away now, and the rate
    limiter above is the second half of the same protection: a caller may no
    longer distinguish the two cases by how long they take, and may no longer
    distinguish them by how many they can afford to try. The budget is keyed on
    whichever identifier the body named, so naming the number and naming the
    address do not add up to two budgets for one account - ``rate_limits.py``
    holds the numbers.

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


@router.post(
    "/sessions/google",
    response_model=schemas.SessionOut,
    status_code=status.HTTP_201_CREATED,
)
def log_in_with_google(
    body: schemas.GoogleTokenIn,
    _: None = Depends(log_in_with_google_rate_limit),
    service: LogInWithGoogle = Depends(log_in_with_google_service),
) -> schemas.SessionOut:
    """Exchange a Google id_token for a token of ours, for an account that exists.

    **The same status, the same body and the same lifetime as ``POST /sessions``
    above**, reached by a different proof, and the two are separate routes rather
    than one with a branch because there is nothing for the branch to test: a
    password and a Google signature have no property in common short of "it
    verified". What they do share is the answer, so a client holding a token from
    either cannot tell from anything this returns which one it used - which is
    correct, since it holds the same kind of token either way and signs it out at
    the same endpoint.

    **It never creates an account, and that is the decision this route exists to
    keep legible.** A token whose subject names nobody here is a 401 carrying
    ``LogIn``'s own sentence - the same words an unknown address and a wrong
    password get a route above - rather than a registration. A single
    create-or-log-in endpoint would have made "the address was free" and "the proof
    held" one response, and would be the find-or-create ``SignUp`` spent Phase 2a
    removing: an account created as a side effect of a caller naming it. So the
    remedy for a caller with no account is ``POST /users/google``, and the
    ``DuplicateGoogleSubjectError`` that route can return is only meaningful
    because this one does not quietly succeed instead.

    **The subject is what is looked up, and the address on the token is not
    consulted.** A subject is stable for the life of a Google account; the address
    on it can change, so an account found by address would be unreachable the
    moment its owner changed their Gmail - and, worse, could silently re-point an
    account's stored address at whatever a later token happened to say. The address
    a token carries is used by the sign-up and never again.

    **An unverified address is refused here too**, which looks redundant on a login
    and is not. An account's address is what a password-reset code is mailed to, so
    a login that accepted an identity whose address Google had merely *claimed*
    would hand a session to somebody presenting a Google account whose address had
    since changed hands. It is the same check the sign-up makes, for a different
    consequence: there it stops a hijackable address being written, here it stops
    one being used.

    A token that does not verify is a 401 with ``InvalidGoogleTokenError``, and a
    missing client id is a 503 - the installation cannot do this, which is a
    different sentence from the caller being unproved. See
    ``routes/users.py`` for the fuller account of both, and ``GoogleTokenIn`` for
    why the body is one opaque string.

    Its limiter is a ceiling and not a budget, for the reason given on
    ``POST /users/google``: the subject the request could be keyed on arrives
    inside the token, and checking the token is the cost being bounded. Note this
    is a bucket of its own rather than one shared with the sign-up, so an attack
    on one of these doors does not close the other.
    """
    return translate.session_out(service.execute(body.id_token, datetime.now()))


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
