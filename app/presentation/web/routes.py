"""Every page this application serves, and the only file needed to know what it does.

**One module, thirteen routes, and the whole surface.** The API next door is
split into twelve modules because its paths are addressed by programs that know
which resource they want; a browser arrives at a page, follows a link, and posts a
form, and the list of pages is the thing worth being able to read in one sitting.
Where a page does something the domain has an opinion about, the opinion is in the
use case it calls and is not restated here.

**Every POST ends in a 303**, without exception, and the pattern is load-bearing
rather than tidy. A browser re-sends the body of the last request when a person
presses reload, so a POST that answered with a page would make the reload a second
withdrawal. Redirecting after a post means the reloaded request is a GET of the
page it landed on, which is a read. The same rule is what makes the back button
safe.

**Every mutating route takes POST and nothing else**, which is the second layer of
``forms.py``'s argument: a ``SameSite=Lax`` cookie *is* sent on a top-level GET
navigation, so a GET that moved money would be reachable by a link.

**The services are the API's, called directly.** ``app.presentation.api.dependencies``
builds one service per request with the actor that request resolved, and every one
of those builders takes its actor as a parameter rather than resolving it - so
calling one with a cookie-resolved user is a call, not a hack. The coupling runs
one way, it is one import block, and the alternative - a shared
``app.presentation.services`` holding the builders for both presentations - is
deliberately deferred because it would mean editing a module every API test depends
on. What this layer adds is the actor: ``web_actor`` reads a cookie where
``current_actor`` reads a header, and everything below that line is identical.
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app.application.wallet_service import WalletService
from app.domain.identity.phoneNumber import fold_phone
from app.domain.identity.user import User, fold_email
from app.domain.money.confirmationKind import ConfirmationKind
from app.domain.money.currency import Currency
from app.presentation.api import rate_limits, translate
from app.presentation.api.dependencies import (
    deposit_service,
    log_in_service,
    log_in_with_google_service,
    log_out_service,
    payment_provider,
    sign_up_service,
    sign_up_with_google_service,
    wallet_service,
)
from app.presentation.web import forms, urls
from app.presentation.web.dependencies import (
    SESSION_COOKIE,
    clear_session_cookie,
    set_session_cookie,
    web_actor,
)
from app.presentation.web.templating import render

router = APIRouter(prefix=urls.PREFIX, include_in_schema=False)


def _path(full: str) -> str:
    """A path from ``urls`` as this router spells it - that is, without the prefix.

    ``APIRouter`` takes its prefix once and its routes relative to it, so the
    full paths are spelled there and shortened here rather than written out
    twice. Two spellings of one URL is the drift this codebase has already paid
    for once, in a reference string with two homes; a helper costs a line.
    """
    return full.removeprefix(urls.PREFIX)


def _render(request: Request, template: str, user=None, **context):
    """One page, with the two facts every page of ours needs.

    ``user`` and ``google_client_id`` are wanted almost everywhere: the first
    because the header says who is signed in and offers a sign-out button, the
    second because the sign-in and sign-up pages carry a Google button *and* a
    note explaining its absence when this installation has no client id. Passing
    both at every call site would be two identical lines in ten places and a
    chance to forget one.
    """
    google = getattr(request.app.state, "google", None)
    return render(
        request,
        template,
        user=user,
        google_client_id=None if google is None else google.client_id,
        **context,
    )


def _redirect(path: str) -> RedirectResponse:
    """A 303 to ``path``. See the module docstring for why every POST does this."""
    return RedirectResponse(path, status_code=303)


def _signed_in(request: Request, logged_in, now: datetime) -> RedirectResponse:
    """Put a new token in the cookie jar and send the browser to its wallets."""
    response = _redirect(urls.LANDING_PATH)
    set_session_cookie(response, logged_in, now, request.app.state.web)
    return response


# --- becoming somebody -------------------------------------------------------


@router.get(_path(urls.SIGN_IN_PATH))
def sign_in_page(request: Request):
    """The form: one address-or-number box, one password box, and Google.

    **A signed-in person who navigates here sees the form anyway**, and that is a
    decision rather than an oversight. The alternative is to look for a session
    cookie and redirect to the wallet list if it resolves - which means a session
    lookup and a refusal caught and swallowed, on the one page whose whole job is
    to be reachable by somebody who has no session. Signing in twice is harmless;
    it mints a second session, and both work.
    """
    return _render(request, "sign_in.html")


@router.post(_path(urls.SIGN_IN_PATH))
def sign_in(
    request: Request,
    identifier: str | None = Form(default=None),
    password: str | None = Form(default=None),
):
    """Prove a password, and put the resulting token in a cookie. **Never in the page.**

    **The limiter is a plain call here, not a dependency**, and the difference
    from the API's equivalent is worth stating because it is a real weakening.
    ``log_in_rate_limit`` next door is a FastAPI dependency typed against
    ``LogInIn``, so it is solved with the body: a body FastAPI is going to refuse
    with a 422 never spends a credit, which is what makes malformed traffic free
    to the attacker and uncharged to the user. A form is not a JSON body, so that
    dependency cannot be declared here, and every field below is an optional
    string - there is nothing for a solver to reject and therefore nothing this
    call is arriving after. What has *not* changed is the bucket or the subject:
    ``sign_in`` and the folded identifier, the same ones the API charges, so the
    two doors share one allowance instead of offering an attacker two. Skipping
    the call altogether would have been the real mistake - it would leave this
    page a way to guess passwords that the API already bounds.

    **``identifier`` is one box and the branch below is the sniff**
    ``forms.looks_like_an_address`` argues for; the API's own route keeps two
    fields and its refusal to guess, and that is not a disagreement.

    A wrong password and an unknown identifier are the same refusal, in the same
    words the API uses, and it renders as a page rather than a redirect because
    this path is in ``errors._AUTH_PATHS`` - redirecting a failed sign-in to the
    sign-in page would discard the reason it failed.
    """
    forms.reject_cross_site(request)
    who = forms.required(identifier, "email or phone number")
    secret = forms.required(password, "password")

    if forms.looks_like_an_address(who):
        rate_limits.enforce(request, "sign_in", fold_email(who))
        service = log_in_service(request)
        logged_in = service.execute(who, secret, datetime.now())
    else:
        rate_limits.enforce(request, "sign_in", fold_phone(who))
        service = log_in_service(request)
        logged_in = service.execute_for_phone(who, secret, datetime.now())

    return _signed_in(request, logged_in, datetime.now())


@router.post(_path(urls.SIGN_IN_PATH) + "/google")
def sign_in_with_google(
    request: Request, id_token: str | None = Form(default=None)
):
    """Exchange the id_token Google Identity Services handed the page for a session.

    **No ``redirect_uri`` and no client secret, because there is no authorization
    code.** The page obtains a token from Google directly and posts it here;
    this route verifies it exactly as ``POST /sessions/google`` does, through the
    same use case and the same verifier off ``app.state``. What Google needed for
    the arrangement is an *authorized JavaScript origin* in its console - a fact
    about Google's side - which is why no setting exists on this side for it.

    **The field is called ``id_token`` and the page puts a ``credential`` in it**,
    which is worth a sentence because the two names for one value is exactly the
    kind of thing that gets "fixed" later by somebody who does not know why.
    ``id_token`` is what this system calls the value, in the API's schema, in the
    use case and in the verifier; ``credential`` is what Google's own JavaScript
    calls it. The rename happens in the page, once, at the boundary where the
    foreign name lives - and it is the only translation applied to it.

    A token naming an account this installation does not have is a 401 carrying
    the same sentence the API gives, because this route never creates one. The
    remedy a person is offered is the sign-up page beside it.
    """
    forms.reject_cross_site(request)
    rate_limits.enforce(request, "sign_in_with_google", None)
    token = forms.required(id_token, "Google credential")

    now = datetime.now()
    logged_in = log_in_with_google_service(request).execute(token, now)
    return _signed_in(request, logged_in, now)


@router.get(_path(urls.SIGN_UP_PATH))
def sign_up_page(request: Request):
    """The form: an address, a password, and Google.

    No phone box, unlike the API. A number can sign up through
    ``POST /phone-verifications``, which texts a code and takes a password at the
    answer - a two-step flow with a page of its own, and deliberately not in this
    layer yet. The sign-up page therefore offers the one path it can complete and
    does not hint at the other; see the checklist for what is still missing.
    """
    return _render(request, "sign_up.html")


@router.post(_path(urls.SIGN_UP_PATH))
def sign_up(
    request: Request,
    email: str | None = Form(default=None),
    password: str | None = Form(default=None),
):
    """Create an account, then sign in as it. **Two calls, on purpose.**

    ``POST /users`` deliberately answers with the account and *no* session -
    ``routes/users.py`` says so in as many words, and tells a client that wants
    both to make both requests. So this handler makes both, and the second one is
    not a find-or-create: the password it presents is the one it was just given,
    in the same request, so presenting it is a proof rather than a lookup by a
    name somebody supplied.

    **A person who is refused the login after being granted the account sees a
    page, and the account exists.** That is the honest outcome of an operation
    that is two operations: the account was created and the session was not. It is
    reachable in practice only by a bug - the password has just been hashed by
    this same system - which is why it is reported through the ordinary error
    renderer rather than being papered over with a redirect to the sign-in page.

    The limiter is the API's ``sign_up`` bucket, for ``sign_in``'s reason above.
    """
    forms.reject_cross_site(request)
    address = forms.required(email, "email address")
    secret = forms.required(password, "password")
    rate_limits.enforce(request, "sign_up", fold_email(address))

    now = datetime.now()
    sign_up_service(request).execute(address, secret, now)
    logged_in = log_in_service(request).execute(address, secret, now)
    return _signed_in(request, logged_in, now)


@router.post(_path(urls.SIGN_UP_PATH) + "/google")
def sign_up_with_google(
    request: Request, id_token: str | None = Form(default=None)
):
    """Register the account a Google identity names, then sign in as it.

    Two calls for ``sign_up``'s reason exactly, and the second is again a proof
    rather than a lookup: the same id_token is verified twice, by the same
    verifier, for two different questions - *should an account exist for this
    identity?* and *is this identity's holder to be given a session?* Neither
    answer is guessed from the other, and neither is a find-or-create. The cost is
    a second signature check against keys already in memory, which is why this
    page is bounded by a ceiling rather than a budget - see the API's
    ``sign_up_with_google_rate_limit``.

    The two buckets are separate, exactly as they are in the API, so an attack on
    the sign-in door does not close this one.
    """
    forms.reject_cross_site(request)
    rate_limits.enforce(request, "sign_up_with_google", None)
    token = forms.required(id_token, "Google credential")

    now = datetime.now()
    sign_up_with_google_service(request).execute(token, now)
    logged_in = log_in_with_google_service(request).execute(token, now)
    return _signed_in(request, logged_in, now)


@router.post(_path(urls.SIGN_OUT_PATH))
def sign_out(request: Request):
    """End the session the cookie names, and empty the cookie.

    **Two things happen and both are required**, which is the one place this
    layer could get sign-out wrong in a way that looks like it worked. Deleting
    the cookie alone leaves a live session row that anybody holding a copy of the
    string could use; ending the session alone leaves a browser that still sends
    a dead token on every navigation, so the person is redirected to the sign-in
    page and then signs in again and now has two sessions. ``LogOut`` is called on
    the cookie's own value rather than on a resolved actor, exactly as ``DELETE
    /sessions/current`` does and for the reason ``LogOut`` gives: refusing to end
    a session that has already lapsed would leave somebody stuck with a token they
    cannot get rid of.

    **No actor dependency, and no cross-site check either.** This route cannot
    move money, cannot read anything, and its whole effect is to destroy the
    credential the request presented - so a forged POST that reached it would be
    doing the person a favour, and refusing it would only mean a forger could
    learn nothing at no cost. It is the one POST here without the check, and it is
    deliberate rather than forgotten.

    A request with no cookie at all is also fine: ``execute`` is handed whatever
    was there, which may be nothing, and a token that names no row is already the
    postcondition. The redirect lands on the sign-in page either way.
    """
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        log_out_service(request).execute(token)

    response = _redirect(urls.SIGN_IN_PATH)
    clear_session_cookie(response, request.app.state.web)
    return response


# --- a person's money --------------------------------------------------------


@router.get(_path(urls.LANDING_PATH))
def wallets(request: Request, user: User = Depends(web_actor)):
    """Every wallet this person holds, and the form that opens another.

    **This page is the reason ``GET /wallets`` exists.** Until the browser client
    landed, nothing in the API could produce a wallet id - both reads of a wallet
    take one - so a client that had not been handed a UUID had no way to learn
    one. The route's own docstring carries that argument; what is worth saying
    here is that the *page* is shaped by it. A person with no wallets is not an
    error state, so the empty list renders an invitation rather than a 404, and
    the currency form is on this page rather than on one of its own because
    opening the first wallet is the only thing a new account can do.

    ``Currencies`` is passed as the values of the domain enum, so the form offers
    exactly what the domain accepts - a currency added to the enum appears here
    without an edit, and one removed disappears from it.
    """
    service = wallet_service(request, actor=user)
    return _render(
        request,
        "wallets.html",
        user=user,
        wallets=[
            translate.wallet_out(one) for one in service.wallets_for_actor()
        ],
        currencies=[currency.value for currency in Currency],
    )


@router.post(_path(urls.PREFIX) + "/wallets")
def open_wallet(
    request: Request,
    currency: str | None = Form(default=None),
    user: User = Depends(web_actor),
):
    """Open a wallet in the chosen currency, and go back to the list.

    **The owner is not in the form and cannot be.** The service was built for one
    actor before this ran, so there is no field through which a browser could name
    somebody else - the same property ``POST /wallets`` has for the same reason,
    and the reason the field here is a currency and nothing more.

    The currency is read through ``translate.currency_in``, so an unsupported one
    is the same refusal the API gives rather than a second opinion formed here.
    """
    forms.reject_cross_site(request)
    chosen = forms.required(currency, "currency")
    wallet_service(request, actor=user).open_wallet(translate.currency_in(chosen))
    return _redirect(urls.LANDING_PATH)


@router.get(_path(urls.PREFIX) + "/wallets/{wallet_id}")
def wallet(
    request: Request, wallet_id: UUID, user: User = Depends(web_actor)
):
    """One wallet: its balances, its pots, its ledger, and what you can do to it.

    **``wallet_id`` is a path parameter and an ownership claim, and the page
    never checks it.** Somebody else's wallet is a ``WalletNotFoundError`` from
    the service, graded 404, rendered as the same page a random UUID gets - there
    is no branch here that could tell the two apart even if somebody wanted one.
    That is Phase 1a's property asserted again at a new door, and it is the reason
    this handler reads as though it forgot something.

    The ledger is rendered whole and there is deliberately no paging. A wallet
    with ten thousand rows would render ten thousand rows, which is a real limit
    of this layer named rather than discovered.
    """
    service = wallet_service(request, actor=user)
    return _render(
        request,
        "wallet.html",
        user=user,
        wallet=translate.wallet_out(service.get_wallet(wallet_id)),
        transactions=[
            translate.transaction_out(one)
            for one in service.transactions_for_wallet(wallet_id)
        ],
        # ``payment_provider(request)`` is deliberately *not* called here, and
        # the reason is that its whole content is raising a 503. This page wants
        # to know the answer so that it can leave the deposit form out; asking
        # the question and then catching the refusal would turn a fact about the
        # installation into an exception, and would put a ``try`` in a handler
        # whose job is to render a wallet. ``provider_for`` returns ``None`` for
        # an install with no key as a state rather than a failure - the API's
        # dependency is where that state becomes a refusal, and it stays there.
        deposits_ready=request.app.state.payment_provider is not None,
    )


@router.post(_path(urls.PREFIX) + "/wallets/{wallet_id}/withdrawals")
def withdraw(
    request: Request,
    wallet_id: UUID,
    amount: str | None = Form(default=None),
    ref: str | None = Form(default=None),
    user: User = Depends(web_actor),
):
    """Record a request to move money out. **It moves nothing.**

    The form is one amount box and one optional reference, and the reference is
    the whole reason this is safe to call twice: it is the key a repeated request
    is folded onto, so a double-submitted form answers with the request already
    on file rather than recording a second one. Left blank, a fresh one is
    generated here - which is the right default for a browser, where a resubmit
    is a reload and the form is not going to invent a key.

    **Nothing here checks the balance**, and the omission is deliberate and is
    the service's: a request is not an attempt, and the balance that decides is
    the one at confirm time. A check now would be a second, weaker copy of the
    wallet's rule, free to disagree with it.

    The redirect goes to the confirmation page rather than back to the wallet,
    because the thing a person now has to do is answer the request. Sending them
    back to the wallet would show them a balance that has not changed yet.
    """
    forms.reject_cross_site(request)
    service = wallet_service(request, actor=user)
    now = datetime.now()
    requested = service.request_confirmation(
        wallet_id,
        ConfirmationKind.WITHDRAWAL,
        now,
        internal_reference=forms.optional(ref),
        amount=translate.money_in(
            forms.required(amount, "amount"),
            service.get_wallet(wallet_id).currency,
        ),
    )
    return _redirect(
        f"{urls.PREFIX}/confirmations/{requested.confirmation.confirmation_id}"
    )


@router.post(_path(urls.PREFIX) + "/wallets/{wallet_id}/deposits")
def deposit(
    request: Request,
    wallet_id: UUID,
    amount: str | None = Form(default=None),
    ref: str | None = Form(default=None),
    user: User = Depends(web_actor),
):
    """Open a collection at the provider, and send the browser to it.

    **The browser leaves this application here and comes back at ``/app/``**, if
    the installation has been told its public origin - see ``urls.callback_url``
    and ``PUBLIC_BASE_URL``. What it comes back to is a page, not a receipt:
    whether the money arrived is decided by ``/webhooks/paystack`` and by nothing
    else, so the wallet page it lands on may well show the old balance for a few
    seconds. The page says a deposit may be in flight for that reason.

    **The provider is resolved before the wallet is read**, which is the API's
    ordering and not a coincidence: ``payment_provider(request)`` refuses an
    installation with no key as a 503, and it does so before any id is looked up,
    any amount is parsed and any row is written. There is no partial state because
    nothing has happened yet - the property to want from a request whose whole
    purpose is to reach outside this system.

    The redirect is to the provider's own page, ``authorization_url``. It is a URL
    this system did not compose and does not interpret; the only handling it gets
    anywhere is the JSON encoding a client's HTTP library would do, and here it is
    a ``Location`` header.
    """
    forms.reject_cross_site(request)
    provider = payment_provider(request)
    service = wallet_service(request, actor=user)
    started = deposit_service(request, actor=user, provider=provider).execute(
        wallet_id,
        translate.money_in(
            forms.required(amount, "amount"),
            service.get_wallet(wallet_id).currency,
        ),
        forms.optional(ref) or _fresh_reference(),
    )
    return _redirect(started.authorization_url)


def _fresh_reference() -> str:
    """A reference for a deposit whose form did not bring one.

    ``uuid4`` and not a counter, for the reason the API's deposit route gives: the
    reference is the key a repeated call is folded onto, so it has to be unique
    per request *and* came from nowhere a client can predict - a client that could
    predict one could collide with somebody else's deposit.
    """
    from uuid import uuid4

    return str(uuid4())


# --- answering a request -----------------------------------------------------


@router.get(_path(urls.PREFIX) + "/confirmations/{confirmation_id}")
def confirmation(
    request: Request, confirmation_id: UUID, user: User = Depends(web_actor)
):
    """One recorded request, and whether it can still be answered.

    **``status`` is the server's, and the page must not re-derive it.** A request
    past its window still stores ``awaiting`` - nothing sweeps, and expiry is
    derived - so the server reports ``expired`` for it and this page draws that
    instead of a button. Computing the same thing here from ``expires_at`` against
    the browser's clock would be a second definition of the window's last instant,
    free to disagree with the one the next confirm would act on - and it would
    disagree in the direction of offering a button that refuses.

    Scoped like every other read: somebody else's request is the same 404 a random
    UUID gets.
    """
    now = datetime.now()
    service = wallet_service(request, actor=user)
    return _render(
        request,
        "confirmation.html",
        user=user,
        confirmation=translate.confirmation_out(
            service.get_confirmation(confirmation_id, now), now
        ),
    )


@router.post(_path(urls.PREFIX) + "/confirmations/{confirmation_id}/confirm")
def confirm(
    request: Request, confirmation_id: UUID, user: User = Depends(web_actor)
):
    """Answer a request. **This is where the money moves.**

    No form fields, and the absence is the design rather than an economy: the
    operation was decided when the request was recorded, so the confirm call names
    nothing but which row - and there is therefore no spelling of this request
    that could carry out a different movement from the one somebody asked for. See
    ``POST /confirmations/{id}/confirm``.

    **What the page proves is that the browser holds the session, and nothing
    more.** There is no password here, no second factor and no mailed code: this
    is an accident guard, a second look before something irreversible. The
    confirmation page is the second look, and it is a page rather than a browser
    dialog because a page can say what the request is *for*.

    The redirect lands on the wallet, which is the answer to the question the
    person is about to ask - the balance. A withdrawal leaves it PENDING, so the
    page they land on shows a debited balance and a pending row, which is the
    truthful pair; the money has left the wallet and has not reached the bank.
    """
    forms.reject_cross_site(request)
    service = wallet_service(request, actor=user)
    answered = service.confirm(confirmation_id, datetime.now())
    return _redirect(f"{urls.PREFIX}/wallets/{answered.wallet.wallet_id}")


# --- the front door ----------------------------------------------------------


root = APIRouter(include_in_schema=False)


@root.get("/")
def front_door():
    """Send ``/`` to the pages, so the bare hostname is not a 404.

    **A 303 and not a 307**, for the reason the API's 307 exists elsewhere and
    would be wrong here: a redirect that a client may repeat with the same method
    is right for a URL that is the *same resource* written differently, and this
    is not - ``GET /`` and ``GET /app/`` are two addresses for one page, and a
    permanent-looking redirect of a POST to ``/`` would post a form to a page that
    takes none.

    A separate router rather than a route on the one above, because the prefix
    belongs to that one and this path is the one exception to it. Keeping the
    exception visible in the file is worth two lines.
    """
    return _redirect(urls.LANDING_PATH)
