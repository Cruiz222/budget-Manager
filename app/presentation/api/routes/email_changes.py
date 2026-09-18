"""Moving an account's address, in two steps and under two different roofs.

The request and the confirm are one feature split across two paths, and the split
is the reason this file exists rather than the routes being appended to ``users``
and ``sessions``. They are authorised by different things - the request by a
session *and* the account's password, the confirm by a token mailed to the address
being moved to - so they belong under different prefixes, and a single module is
what lets that difference be read in one place instead of two.

**``POST /users/me/email-changes`` is under ``/users/me`` and
``POST /email-changes/confirm`` is not**, which is the same argument
``routes/sessions.py`` makes about sessions: a path nested under an account should
mean *this is part of that account, and you must be that account to touch it*.
The request qualifies - it is the caller's own address, changed by the caller - and
the confirm does not. It is reachable by somebody holding a code and nothing else,
which is deliberate: the code was mailed to the address being moved to, so it
proves more about that address than a session would, and requiring a session on top
would refuse the person who asked at a desk and opened the mail on a phone. Putting
it under ``/users/me`` would have said the opposite in the one place a reader looks
first.

**The code travels in the body and never in a path.** A token in a URL is a token
in every access log, every proxy log and every ``Referer`` header the URL is pasted
into; in a body it is a field of a request nothing logs. This one moves an account,
so the difference is not academic.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, status

from app.application.identity.confirm_email_change import ConfirmEmailChange
from app.application.identity.request_email_change import RequestEmailChange
from app.domain.identity.user import User
from app.presentation.api import schemas, translate
from app.presentation.api.dependencies import (
    confirm_email_change_service,
    current_actor,
    request_email_change_rate_limit,
    request_email_change_service,
)

router = APIRouter(tags=["identity"])


@router.post(
    "/users/me/email-changes",
    response_model=schemas.EmailChangeOut,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
)
def request_email_change(
    body: schemas.EmailChangeIn,
    actor: User = Depends(current_actor),
    _: None = Depends(request_email_change_rate_limit),
    service: RequestEmailChange = Depends(request_email_change_service),
) -> schemas.EmailChangeOut:
    """Ask to move this account to a new address, if you can prove the password.

    **201 in both of the two things that can happen**, and that is a decision about
    what was created rather than a convenience. On an installation with mail, this
    creates a pending request - a row with an id, an owner and a deadline - which
    is exactly what the 201 in ``POST /sessions`` reports. On an installation
    *without* mail there is no request to create and the change is applied here and
    now, which is also a creation: the account is at a different address than it
    was. The ``status`` field says which of the two happened, because the two are
    different enough that a client must not have to guess.

    **``response_model_exclude_none`` is doing exactly one thing here**, and it is
    worth naming because the flag looks broader than it is: ``expires_at`` is the
    only nullable field in ``EmailChangeOut``, so omitting ``None`` means the
    deadline is *absent* on an applied change rather than present and null. That
    is what makes "present exactly when pending" a statement about the response
    rather than a habit the reader has to verify.

    The refusals arrive with the domain's own words and this module adds none of
    them: **401** ``InvalidCredentialsError`` for a password that does not match,
    **409** ``EmailUnchangedError`` for the address the account already holds,
    **409** ``DuplicateEmailError`` for somebody else's, **400**
    ``UnusableEmailError`` for an address with no domain in it, and **400**
    ``InvalidUserEmailError`` for something that is not an address at all. The last
    two are the entry rule, and the order they are reached in is why a malformed
    address is not reported as an unusable one - see ``RequestEmailChange``.

    A **500** is possible here and is not a bug in this route: on an installation
    with mail, a verification message that could not be sent raises, because a
    request whose code did not arrive is a request nobody can answer. See
    ``RequestEmailChange`` for why that one is allowed to fail the request while
    the notice at confirm time is not.

    **The limiter sits below ``current_actor``, and that order is a decision.**
    ``request_email_change_rate_limit`` declares ``current_actor`` as its own
    dependency, so the actor is resolved - and a bad token refused as a 401 -
    before the limiter is consulted. Returning 429 to a caller who has not proved
    who they are would answer a question they have no standing to ask, and would
    be a behaviour change on this route, which is the only limited one behind a
    session. It also means the subject it counts is the *account*, since the
    actor is the one thing in this request that is known rather than supplied;
    ``dependencies.py`` records what that key does and does not cover.
    """
    return translate.email_change_out(
        service.execute(body.email, body.password, datetime.now())
    )


@router.post(
    "/email-changes/confirm",
    response_model=schemas.EmailChangeConfirmedOut,
    status_code=status.HTTP_201_CREATED,
)
def confirm_email_change(
    body: schemas.ConfirmEmailChangeIn,
    service: ConfirmEmailChange = Depends(confirm_email_change_service),
) -> schemas.EmailChangeConfirmedOut:
    """Apply the change the mailed code names, and tell the address being left.

    **No ``Authorization`` header, and there is no ``current_actor`` dependency to
    supply one.** This is the API's third unauthenticated write, after
    ``POST /users`` and ``POST /sessions``, and the reason it is not a hole is the
    shape of the credential: the code exists only because somebody already
    presented the account's password to mint it, and it was mailed to the address
    being moved to. So the password proof is already spent on this change, and
    what a session would add is a second way for the person who asked to be
    refused - the one who asked on a laptop and opened the mail on a phone is
    exactly the person this operation exists for. See ``ConfirmEmailChange``.

    **201, because the account moved.** The address is a different one than it was
    before this request, and the resource that changed - the user - is the one the
    body describes. There is no ``Location`` header for the same reason
    ``POST /sessions`` has none: the thing created is a state, not a URL.

    The three refusals are three different statuses on purpose, because the
    remedies differ and the difference is what a client acts on. **401**
    ``InvalidEmailChangeTokenError`` for a code that means nothing - never existed,
    or names an account that is gone. **409** ``EmailChangeExpiredError`` for one
    whose fifteen minutes are up. **409** ``EmailChangeAlreadyUsedError`` for one
    that has already been spent, which is reachable only because the row survives
    being spent. The last two are the pair that must not be collapsed: *ask again*
    against *ask again, and answer it sooner*.

    **A code is spent by the attempt, not by the success.** If the address was
    registered by somebody else during the window, this is a **409**
    ``DuplicateEmailError`` *and the code is dead* - the request is committed as
    spent before the refusal is raised. So a client that retries the same code
    after a duplicate gets "already used", not the duplicate again, and the remedy
    is to ask for a change to a different address. That ordering is the same one
    ``ConfirmationStatus`` records for money.

    **The notice to the old address cannot fail this request.** It is sent after
    the change has committed, and a channel that refuses it is reported in
    ``notice_sent``/``notice_error`` rather than raised - refusing to move an
    account because a farewell bounced would strand the very account this
    endpoint exists to rescue, and a notice to a stranded address is *expected* to
    bounce.
    """
    return translate.email_change_confirmed_out(
        service.execute(body.token, datetime.now())
    )
