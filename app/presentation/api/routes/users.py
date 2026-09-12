from datetime import datetime

from fastapi import APIRouter, Depends, status

from app.application.identity.sign_up import SignUp
from app.domain.identity.user import User
from app.presentation.api import schemas, translate
from app.presentation.api.dependencies import current_actor, sign_up_service

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
