"""Proving a number, in two steps, so that an account can be created on it.

The request and the confirm are one feature split across two paths, following
``routes/password_resets.py`` and for its reason: neither half is authenticated,
but the request can be reached by anybody with a number and the confirm only by
somebody holding a code, so the two have very different abuse profiles and a
reader asking "what can a stranger make this server do, and what does it cost?"
should find both answers under one prefix rather than under two.

**This is the only prefix in the API whose request half costs money per call.** The
reset flow's request sends one mail through an account the operator already pays
for; this one sends a text through Termii, billed per message, on an endpoint with
no actor and no rate limiter. That is why the routing is commented here rather than
left to the module docstring of a use case: whoever builds rate limiting will look
at the list of unauthenticated writes, and this is the one where the cost is not
wall clock.

**Neither path is nested under ``/users/me``, and here it is stronger than an
absence of authorisation - it is an absence of a user.** ``routes/email_changes.py``
and ``routes/sessions.py`` nest because a session names whose account is being
acted on; a signup has no account yet, because the account is what answering
creates. A path under ``/users/me`` would promise a check there is nothing to
perform it against.

**The code travels in the body and never in a path**, on ``password_resets``'
argument unchanged: a token in a URL is a token in every access log, every proxy
log and every ``Referer`` header the URL is pasted into.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, status

from app.application.identity.confirm_phone_sign_up import ConfirmPhoneSignUp
from app.application.identity.request_phone_verification import (
    RequestPhoneVerification,
)
from app.presentation.api import schemas, translate
from app.presentation.api.dependencies import (
    confirm_phone_sign_up_service,
    request_phone_verification_service,
)

router = APIRouter(tags=["identity"])


@router.post(
    "/phone-verifications",
    response_model=schemas.PhoneVerificationAcceptedOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def request_phone_verification(
    body: schemas.PhoneVerificationRequestIn,
    service: RequestPhoneVerification = Depends(request_phone_verification_service),
) -> schemas.PhoneVerificationAcceptedOut:
    """Text a code to a number, so whoever holds the handset can create an account.

    **202 and not 201, and here the reason is not silence but latency.** The reset
    route answers 202 because an unknown address creates nothing and a 201 would be
    a lie in half the cases it was returned in; this route answers 202 because the
    row it writes is a *pending* request rather than the thing the caller asked for.
    Nothing is verifiable yet - the person has to read a message and come back - so
    a 201 would claim an act that has not happened. It is the same status for a
    related but distinct reason, which is worth stating rather than assuming the two
    agree.

    **A malformed number is a 400, and this route is where the difference from the
    reset flow shows.** ``RequestPasswordReset`` deliberately folds whatever arrives
    and looks for it, so ``not-an-address`` is an address naming no account; a
    number is not looked up, it is dialled, and a value that is not a number has no
    account-or-not question hiding behind it. Refusing it outright leaks nothing and
    saves a provider call.

    **A 503 is possible and is about the installation.** On an install with no
    Termii credentials, the request is refused with ``NoSmsAccountError`` naming the
    variable that is missing - see ``RequestPhoneVerification`` for why a number has
    no fallback where an address change has one.

    **A 500 is possible and is not a bug in this route.** A text that could not be
    sent raises, because a code that did not arrive makes the row unanswerable - and
    the person is waiting on a handset, so a silent success would leave them staring
    at a phone that will never buzz while a pending verification holds the
    ``UNIQUE`` slot against their own number.

    **This response carries the folded number and the deadline**, unlike the reset
    route's deliberately empty body, and the difference is that this endpoint asks
    the store no question at all: there is no hit and no miss for a body to betray.
    See ``PhoneVerificationAcceptedOut``.

    **No ``Authorization`` header is accepted and there is no ``current_actor``
    dependency.** This is the sixth unauthenticated write in the API and the only one
    that creates an account *and* spends money on the installation's behalf. What
    limits it is that it cannot read anything, cannot name an account, and cannot
    spend anything except a row the same number will supersede - and that the code
    it sends reaches only a handset somebody is holding.
    """
    return translate.phone_verification_accepted_out(
        service.execute(body.phone, datetime.now())
    )


@router.post(
    "/phone-verifications/confirm",
    response_model=schemas.UserOut,
    status_code=status.HTTP_201_CREATED,
)
def confirm_phone_sign_up(
    body: schemas.ConfirmPhoneSignUpIn,
    service: ConfirmPhoneSignUp = Depends(confirm_phone_sign_up_service),
) -> schemas.UserOut:
    """Create the account the texted code authorises, holding that number.

    **201, and this is the one response in the API that returns an account nobody
    has logged in to.** The account exists, its number is proved, and it has a
    password - so the honest next step is ``POST /sessions`` with the same two
    facts the caller is holding, which is what makes this a signup rather than a
    login. No token is minted here, on ``POST /users``' precedent: registering and
    proving are separate acts, and a signup that also returned a session would be
    handing out a credential the caller has not used yet. There is no ``Location``
    header for the reason ``POST /sessions`` has none - the thing created is an
    account, and ``GET /users/me`` would answer it only with the token this route
    deliberately did not make.

    **The number is not in the request, and that is the decision to read the
    signature for.** It is read off the claimed row, so a body carrying one could
    only disagree with the number the code actually proves. See
    ``ConfirmPhoneSignUp.execute`` and
    ``PhoneVerificationRepository.claim_by_token_hash``.

    **The three refusals are three different statuses, exactly as the other two
    flows' are.** **401** ``InvalidPhoneVerificationTokenError`` for a code that
    means nothing - never existed, or a row whose type no longer parses. **409**
    ``PhoneVerificationExpiredError`` for one whose ten minutes are up. **409**
    ``PhoneVerificationAlreadyUsedError`` for one already spent, reachable only
    because the row survives being spent, and here the most actionable of the three:
    a spent verification means the account was created, so the remedy is to log in
    rather than to ask for another text. That the mapping is identical across three
    flows is the strongest evidence they share one lifecycle.

    **A 409 ``DuplicatePhoneError`` arrives from the other direction**, and it is the
    one refusal here that is about the world rather than about the code: the number
    was claimed by somebody else between the request and the answer. It is given at
    *this* step rather than at the request on purpose - asking earlier would make the
    request endpoint an enumeration oracle over a small, guessable space, whereas by
    now the caller has proved they hold the handset and the sentence is theirs to
    read. Note this refusal spends the code, so the remedy is still "ask again".

    **A password that is too short does not spend the code**, following
    ``confirm_password_reset`` and for its reason: every other refusal here is after
    the claim, and this one is before it, so the 400 from ``WeakPasswordError``
    leaves the code usable with a longer password.
    """
    return translate.user_out(
        service.execute(body.code, body.password, datetime.now())
    )
