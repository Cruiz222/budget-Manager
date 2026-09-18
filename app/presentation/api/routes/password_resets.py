"""Setting a forgotten password, in two steps and under one roof.

The request and the confirm are one feature split across two paths, and unlike
``routes/email_changes.py`` the split is *not* a difference of authorisation -
neither half is authenticated. They are separate paths anyway, for a reason that
is specific to this flow: the request can be reached by anybody and the confirm
can be reached only by somebody holding a code, so the two have very different
abuse profiles and a reader looking for "what can a stranger make this server do?"
should be able to answer it from one prefix rather than from two.

**Neither path is nested under ``/users/me``, and here that is not a decision
about authorisation - it is the absence of one.** ``routes/sessions.py`` and
``routes/email_changes.py`` both use the nesting rule that a path under an account
means *you must be that account*, and the address-change request qualifies because
a session names whose address it is. Nothing names whose password this is: the
caller cannot prove they are anybody, the account is inferred from an address, and
the mail goes to whatever mailbox that address names. Putting either path under
``/users/me`` would promise a check this feature cannot perform.

**The code travels in the body and never in a path**, on ``email_changes``'
argument and with more force. A token in a URL is a token in every access log,
every proxy log and every ``Referer`` header the URL is pasted into; this one
replaces the secret the account is proved by, and it is also the only credential a
client holds at that moment - so a leaked one is not a stale session but the
account.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, status

from app.application.identity.confirm_password_reset import ConfirmPasswordReset
from app.application.identity.request_password_reset import RequestPasswordReset
from app.presentation.api import schemas, translate
from app.presentation.api.dependencies import (
    confirm_password_reset_service,
    request_password_reset_rate_limit,
    request_password_reset_service,
)

router = APIRouter(tags=["identity"])


@router.post(
    "/password-resets",
    response_model=schemas.PasswordResetAcceptedOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def request_password_reset(
    body: schemas.PasswordResetRequestIn,
    _: None = Depends(request_password_reset_rate_limit),
    service: RequestPasswordReset = Depends(request_password_reset_service),
) -> schemas.PasswordResetAcceptedOut:
    """Ask for a reset code, and be told nothing about whether the address exists.

    **202 and not 201, and it is the decision this route is built around.**
    ``POST /users/me/email-changes`` answers 201 because something is always
    created there - a pending request on an install with mail, a moved account on
    one without. Here an unknown address creates *nothing*, so a 201 would be a lie
    in half the cases it was returned in. Worse than the lie is the alternative
    that fixes it: a 404, or a 201 for a hit and a 200 for a miss, would each turn
    this endpoint into an enumeration oracle - a stranger could learn which
    addresses are registered as fast as they can type them, which is a question
    this API answers nowhere else. 202 is the one status that is honest in both
    cases: the request was *received*, and the work it asks for may or may not
    happen.

    **The body is byte-identical for an address that names an account and one that
    does not**, and that is enforced by construction rather than by care:
    ``password_reset_accepted_out`` takes the outcome and never reads it, so there
    is not a branch here that could vary a word. The test that matters asserts the
    two raw response texts are equal, because the moment somebody adds a friendly
    "we have emailed ..." to one arm, that assertion is what fails.

    **The limiter above counts both arms, and that is the one thing about it that
    could reintroduce the leak this endpoint was built to close.** It runs as a
    dependency, so it is asked before ``RequestPasswordReset`` is reached and
    therefore before the use case can return early on the unknown-address arm -
    which is what makes 429-versus-202 a statement about how often the *caller* has
    asked rather than about whether the address exists. Had it been a check inside
    the handler body, after the branch, the two arms would carry different budgets
    and the status would be an enumeration oracle built out of the very control
    meant to protect this route. The invariant is asserted directly in
    ``tests/presentation/api/test_rate_limits.py``.

    **No ``Authorization`` header is accepted, and there is no ``current_actor``
    dependency to supply one.** This is the API's fifth unauthenticated write and
    the first aimed at an account its caller has no claim on - ``POST /users``
    creates a new one, ``POST /sessions`` exchanges a secret the caller already
    knows, ``POST /email-changes/confirm`` acts on one proved by password and
    mailbox, ``POST /password-resets/confirm`` on one proved by mailbox alone,
    and this writes a pending reset against an account the caller has proved
    nothing about. That is what the flow is: the person is asking precisely
    because they cannot authenticate. What limits the damage is that this
    operation cannot read an account, cannot change one, and can only cause a
    mail to be sent - and that every consequence of reading that mail requires
    the mailbox.

    **A 503 is possible and is about the installation rather than the request.** On
    an install with no SMTP configured, the request is refused with
    ``NoMailAccountError`` naming the variable that is missing - see
    ``RequestPasswordReset`` for why a forgotten password cannot fall back the way
    an address change does. Note the shape of the trade: this is the one refusal on
    an unauthenticated endpoint that says something about the deployment, and it
    says it because a 400 blaming the caller's address would send them hunting for a
    typo in a value that is correct.

    **A 500 is possible here and is not a bug in this route.** On an install with
    mail, a reset message that could not be sent raises, because a request whose
    code did not arrive is a request nobody can answer - and here the person is
    already locked out, so leaving them watching a mailbox that will stay empty is
    the worst thing this endpoint could silently do.

    **The timing of this endpoint leaks what its body does not**, and the route is
    not where that is fixed or hidden - see the README's open item on this flow. An
    SMTP round trip happens on one arm and not the other, and a round trip is far
    more measurable than the hashing work decision 81 equalises - which is itself a
    fix that decision records as *unshipped*, so this flow inherits that gap and
    widens it. So a caller who measures rather than reads can still infer whether an
    address names an account. Recording it here rather than leaving it to be
    discovered is the point: this is where somebody building rate limiting will look
    first, and the honest statement is that the response is identical and the
    latency is not.

    **The rate limiter above changes the price of that attack without closing it,
    and the distinction is worth being exact about.** Three requests an hour per
    address is a budget an attacker can still spend - a dozen addresses is
    thirty-six measurements an hour, which is enough to distinguish a round trip
    from no round trip if the timing difference is large enough to see at all.
    What the limit does is make the attack slow and leave a trace rather than
    make it impossible, so this paragraph is *not* obsolete and the follow-up it
    describes is still owed. Nothing about the limiter should be read as having
    answered it.
    """
    return translate.password_reset_accepted_out(
        service.execute(body.email, datetime.now())
    )


@router.post(
    "/password-resets/confirm",
    response_model=schemas.PasswordResetConfirmedOut,
    status_code=status.HTTP_201_CREATED,
)
def confirm_password_reset(
    body: schemas.ConfirmPasswordResetIn,
    service: ConfirmPasswordReset = Depends(confirm_password_reset_service),
) -> schemas.PasswordResetConfirmedOut:
    """Replace the password the mailed code authorises, and sign every device out.

    **201, because something was created in the only sense that matters here.** The
    account has a credential it did not have before - a different hash, set at a
    different moment - and the response describes that account. There is no
    ``Location`` header for the same reason ``POST /sessions`` has none: the thing
    created is a state, not a URL.

    **The three refusals are three different statuses, exactly as the change flow's
    are.** **401** ``InvalidPasswordResetTokenError`` for a code that means nothing
    - never existed, or names an account that is gone. **409**
    ``PasswordResetExpiredError`` for one whose fifteen minutes are up. **409**
    ``PasswordResetAlreadyUsedError`` for one already spent, reachable only because
    the row survives being spent. The last two must not be collapsed, and the
    remedies are the same two words the change flow uses: *ask again*, against *ask
    again, and answer it sooner*. That this mapping is identical is the strongest
    evidence that the two flows really do share one lifecycle and merely spell their
    fields differently.

    **A password that is too short does not spend the code**, and this is the one
    behaviour a reader will assume the opposite of. Every other refusal in this
    route is *after* the claim; this one is before it, so the 400 that comes back
    from ``WeakPasswordError`` leaves the code usable and the caller can present the
    same one with a longer password. See ``ConfirmPasswordReset`` for the argument,
    which turns on the difference between a change in the world and a typo in the
    request.

    **The code is spent by the attempt in every other case.** A confirm that fails
    for any reason after the claim - the account deleted, a bug in the credential
    write - leaves the code spent, and the remedy is to ask for another. That is the
    same ordering ``ConfirmationStatus`` records for money.

    **The notice to the account cannot fail this request.** It is sent after the
    password has been replaced and the sessions deleted, and a channel that refuses
    it is reported in ``notice_sent``/``notice_error`` rather than raised - refusing
    to apply a reset because the warning about it bounced would leave the person
    locked out with the code spent, which is the worst state this feature can
    produce.
    """
    return translate.password_reset_confirmed_out(
        service.execute(body.token, body.password, datetime.now())
    )
