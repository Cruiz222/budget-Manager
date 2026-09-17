from datetime import datetime

from fastapi import APIRouter, Depends, status

from app.application.identity.sign_up import SignUp
from app.application.identity.sign_up_with_google import SignUpWithGoogle
from app.domain.identity.user import User
from app.presentation.api import schemas, translate
from app.presentation.api.dependencies import (
    current_actor,
    sign_up_service,
    sign_up_with_google_service,
)

router = APIRouter(tags=["identity"])


@router.post("/users", response_model=schemas.UserOut, status_code=status.HTTP_201_CREATED)
def sign_up(
    body: schemas.SignUpIn,
    service: SignUp = Depends(sign_up_service),
) -> schemas.UserOut:
    """Register an address, with a password to prove it later.

    **201 and not 200**, because this request creates something at a location
    that did not exist before - and because the distinction is load-bearing for a
    client: a 200 for "you are registered" and a 200 for "that address was
    already taken" would be indistinguishable to anything that only looked at the
    status. The taken case is a 409 with ``DuplicateEmailError`` in its body, so
    the two outcomes are as different as they should be.

    **It returns the account and not a session.** Registering does not sign you
    in, and the separation is the design rather than an extra step: this is the
    one operation that creates an identity, and it is deliberately not the one
    that proves you hold it. A client that wants both makes both requests, and
    the second one is the one that could have failed - which is exactly the
    distinction worth keeping, because "the address was free" and "the password
    works" are different facts and a client that got them in one response could
    not tell which had just gone wrong.

    An empty body, a missing password or a password outside the policy all arrive
    as refusals with the domain's own words: a 422 from the schema for a body
    that is not the right shape, and a 400 naming ``WeakPasswordError`` or
    ``InvalidUserEmailError`` for one that is. The store's own backstop - the
    ``UNIQUE`` on ``users.email`` - is one layer further down and is not what
    answers a duplicate; see ``SignUp``.
    """
    return translate.user_out(service.execute(body.email, body.password, datetime.now()))


@router.post(
    "/users/google",
    response_model=schemas.UserOut,
    status_code=status.HTTP_201_CREATED,
)
def sign_up_with_google(
    body: schemas.GoogleTokenIn,
    service: SignUpWithGoogle = Depends(sign_up_with_google_service),
) -> schemas.UserOut:
    """Register the identity a Google id_token describes, and set no password.

    **The same 201, the same body and the same refusal to sign you in as
    ``POST /users`` above**, and the two are worth reading together because what
    differs is only *what the caller had to prove*: an address and a password
    there, a signature from Google here. Registering is still not signing in, for
    the reason that route gives - "the address was free" and "the proof held" are
    two facts, and a client that got them in one response could not tell which had
    gone wrong. So a first-time client sends its token twice, and the second call
    is cheap because a token is reusable for its lifetime.

    **No password is set, and that account is still reachable.** It is the case
    ``LogIn._settle`` already handles and ``RequestPasswordReset`` was already
    designed for: an account with no credential still gets a mailed code and may
    thereby set a first password. So the recovery path exists before the accounts
    do, which is the order worth having.

    **Two things can be 409, and the bodies name which.** ``DuplicateEmailError``
    means somebody already holds this address - the address is *not* linked to the
    Google identity, and that refusal is deliberate: linking would let whoever
    controlled the Google account take over an account they never proved anything
    about. ``DuplicateGoogleSubjectError`` means this Google account already has an
    account here. The remedies differ and both are in the refusal: use another
    address, or log in.

    **The caller cannot name the account it creates.** There is no field for a
    subject, an address or a verification flag in ``GoogleTokenIn`` - one opaque
    string - so everything the account is built from arrives inside a signature
    this installation checks against Google's published keys *and* against its own
    client id. A token Google issued to any other application is a valid Google
    token and not a credential here, which is the check that makes this route
    about this application rather than about Google at large.

    A token that does not verify is a 401 with ``InvalidGoogleTokenError``, for
    every reason it might not have: unknown, expired, wrong key, wrong audience,
    wrong issuer. One class, because the remedy is one thing - get a fresh token -
    and because splitting them would describe a forgery to whoever forged it. An
    address Google has not proved is a 400 with ``UnverifiedGoogleEmailError``, and
    that refusal is load-bearing rather than tidy: an unproved address stored here
    would be an address this system mails password-reset codes to.
    """
    return translate.user_out(service.execute(body.id_token, datetime.now()))


@router.get("/users/me", response_model=schemas.UserOut)
def whoami(actor: User = Depends(current_actor)) -> schemas.UserOut:
    """The account this request acts as.

    The HTTP spelling of the CLI's ``whoami``, and it exists for the same reason:
    "who do you think I am?" is the first question anybody debugging an
    authentication problem asks, and the answer should not require reading the
    database by hand.

    **What it returns is now evidence rather than a claim.** It used to resolve
    the ``X-User-Email`` header and create the account if nobody held it, which
    made it a registration endpoint that had got away - calling it with a new
    address made a user. It now reports the account a valid token belongs to, and
    the only way to reach it is to have logged in. Called without a token it is a
    401, so the endpoint that answers "who am I" is also the cheapest way to ask
    "is my token still good?".
    """
    return translate.user_out(actor)
