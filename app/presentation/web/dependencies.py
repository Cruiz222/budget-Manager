"""The cookie, and who holding it makes the request.

**One credential, two faces.** The value in this cookie is the *same* opaque
token ``POST /sessions`` has always issued - not a second kind of secret, not a
JWT, not a signed blob. ``web_actor`` below resolves it with the same
``ResolveActorFromSession`` the API's ``current_actor`` uses, against the same
``sessions`` table, with the same thirty-day lifetime and the same logout. The
API's session machinery does not learn that a browser exists, and the day a
token's lifetime changes there is one place to change it.

What the cookie buys over a header is that it is *automatic*: a browser attaches
it to every navigation under its path, so no page has to carry a token in a URL,
in a form field or in a script variable - which is the whole reason a browser
client is possible at all. What it costs is the set of attacks a header cannot
have: a cookie is attached by the browser rather than by the code, so anything
that can make the browser issue a request can spend it. ``forms.py`` carries the
three layers that answer that.

**The cookie's name, path and flags are constants here, and the name is not a
setting.** See ``SESSION_COOKIE`` for the name, ``COOKIE_PATH`` for the path, and
``set_session_cookie`` for the flags - each has an argument rather than a
default, and the only one an installation may change is ``Secure``, which is a
fact about the deployment and lives in ``WebSettings``.

**This module is the second place in this system where a credential is read off
a request**, and ``app.presentation.api.dependencies`` says in its own docstring
that it is the only one. It is not any more, and that sentence is corrected in
place rather than left to rot: the claim it was making still holds and is worth
restating in its changed form - **there is no code path in this system by which a
request becomes a user without presenting a token.** Two functions can produce a
``User`` and both of them require one; they differ in where the client put it.
"""

from datetime import datetime

from fastapi import Request, Response

from app.composition_root import build_resolve_actor
from app.domain.identity.user import User
from app.infrastructure.settings import WebSettings
from app.presentation.api.errors import MissingCredentialsError

#: The cookie's name. ``budget_session`` rather than ``session`` or ``token``,
#: because a browser holds cookies for every site it visits and a name is what a
#: person reading their browser's storage has to go on. A generic name there is
#: how somebody deletes the wrong one.
SESSION_COOKIE = "budget_session"

#: Where the cookie is sent. **Narrower than ``/`` on purpose**, and the argument
#: is not tidiness: a cookie is attached by the browser to every request whose
#: path starts with this one, so the path is the size of the surface the
#: credential is offered on. Nothing outside this layer reads cookies, and
#: ``/`` would offer it to every future route - including any JSON endpoint added
#: later, where a browser-driven request could then be authenticated by accident.
#:
#: **The cost is real and worth naming**: a future page that fetched the JSON API
#: with ``fetch`` would need ``credentials: 'include'`` *and* would find the path
#: did not match, so it would silently un-authenticate. There is no such fetch
#: today - every page here is server-rendered and posts to its own layer - and
#: the day one appears, this constant is the thing to read first.
COOKIE_PATH = "/app"


def web_actor(request: Request) -> User:
    """The user this request acts as, proved by the cookie it carried.

    ``current_actor``'s counterpart in every respect but one, and the difference
    is the whole of this function: that one reads a header and this one reads a
    cookie. Both then hand a token to the same use case, read the clock at the
    same boundary, and let the same ``InvalidSessionError`` out for a token that
    is unknown, expired or orphaned. A client cannot tell from a refusal which
    face it presented, which is correct - it is the same credential.

    **``MissingCredentialsError`` is raised here rather than a page being
    rendered**, and that is what puts the sign-in redirect in one place: the
    error renderer sees a 401 on a page under this prefix and sends the browser
    to ``/app/sign-in``. A function that redirected directly would be a second
    place that knew where the sign-in page is - and it would have to return a
    response, which a dependency shaped like this one cannot do.

    **FastAPI caches this for the duration of one request**, so a page that
    builds two services resolves the actor once. That matters here more than it
    does in the API: a second resolution is a second session lookup, and a page
    that reads a wallet and its confirmation would otherwise do that work twice
    and could, on a clock boundary, disagree with itself about who is asking.

    ``datetime.now()`` is read at this line and nowhere below, exactly as the
    API's version does it, so every expiry rule underneath stays a pure
    comparison a test can drive.
    """
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise MissingCredentialsError(
            "this page needs a session; sign in at the sign-in page"
        )
    return build_resolve_actor(
        unit_of_work_factory=request.app.state.unit_of_work_factory
    ).execute(token, datetime.now())


def web_settings(request: Request) -> WebSettings:
    """This installation's web settings, off ``app.state``.

    Resolved once at startup by ``create_app``, like every other piece of
    configuration, rather than read from the environment per request. It is a
    dependency rather than an attribute read because two call sites below want
    it and a third will eventually: the environment is read in one module, and
    nothing outside it should be reaching for ``os.environ`` even indirectly.
    """
    return request.app.state.web


def set_session_cookie(
    response: Response, logged_in, now: datetime, settings: WebSettings
) -> None:
    """Put a freshly minted token in the browser's cookie jar.

    **Five flags, and every one of them is doing something.** ``HttpOnly`` keeps
    the token out of JavaScript, which is what makes an injected script unable to
    read it - and note what it does *not* do, which is stop the same script from
    using it: a cookie is attached by the browser, so a script does not need to
    read the token to spend it. That is the residual risk ``forms.py`` is about,
    and it is why this flag is a mitigation rather than a defence.

    ``SameSite=Lax`` is the second layer and the larger one: a cross-site POST
    does not carry the cookie at all, so the classic form-forgery has nothing to
    send. ``Lax`` and not ``Strict``, deliberately, because ``Strict`` would
    mean a person following a link to this application from anywhere - a mail, a
    bookmark, a message - arrives signed out and is shown a sign-in page having
    clicked a link to their own wallet. ``Lax`` sends the cookie on a top-level
    *navigation*, which is what a followed link is, and not on a cross-site
    POST, which is what forgery is. That is the exact split this layer needs.

    ``Secure`` is the one an installation may turn off, and ``WebSettings``
    carries the argument for why the default is on and what turning it off costs.
    **``Path`` is ``COOKIE_PATH`` and must be spelled the same at deletion** -
    a cookie is identified by name, path and domain together, so a ``delete_cookie``
    without the matching path sets a second, empty cookie and leaves the real one
    in the jar. ``clear_session_cookie`` below is where that is honoured.

    **``Max-Age`` is the session's own remaining life rather than thirty days
    written twice.** ``LoggedIn.session.expires_at`` is an absolute moment, and
    the subtraction here is against the *same* ``now`` the route handed the use
    case - which is what keeps the browser's idea of when this cookie dies and
    the server's idea of when the token dies from being computed from two
    different clock reads. A cookie that outlived its token would present a
    browser that believed itself signed in to a server that refused it; a cookie
    that died early would sign somebody out for no reason they could see. The
    clamp at one second is for the impossible case only - a session whose expiry
    the use case had already decided - because ``Max-Age=0`` does not mean "very
    briefly", it means *delete this cookie now*.
    """
    response.set_cookie(
        SESSION_COOKIE,
        logged_in.token,
        max_age=max(1, int((logged_in.session.expires_at - now).total_seconds())),
        path=COOKIE_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )


def clear_session_cookie(response: Response, settings: WebSettings) -> None:
    """Take the token back out of the browser's jar.

    **The flags must match the ones it was set with**, and the two that matter
    are ``Path`` and ``Secure``: a browser identifies a cookie by name, path and
    domain, so a deletion missing the path writes a *different* cookie with an
    empty value and leaves the original in place. The failure is silent and looks
    like a sign-out button that does nothing - which is why this is a function
    beside its setter rather than a ``delete_cookie`` call at the route, where
    the duplication would be invisible until it drifted.

    **Deleting the cookie is not signing out, and this function does neither.**
    Ending the session is ``LogOut``'s job, on the token, and it happens in the
    route before this is called. A browser that kept a copy - a saved page, a
    back button, a second tab - would still be holding a string; the reason it is
    worthless is that the row behind it is gone. See ``DiscardSession`` for the
    half of that this layer cannot do.
    """
    response.delete_cookie(
        SESSION_COOKIE,
        path=COOKIE_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
