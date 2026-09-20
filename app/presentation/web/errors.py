"""A refusal, as a page.

**The same refusal, a different rendering.** Every failure in this system is
already decided somewhere else: ``app.presentation.api.errors`` grades a domain
refusal, names it, and writes a sentence for a person. That module renders the
answer as ``{"error", "detail"}`` for a program. This one renders the *same pair*
as HTML for a browser, and it deliberately decides nothing - the status comes from
``errors.status_for``, which is the one place the grade is chosen, and the two
strings come from the same functions the JSON body uses. A page that called a 409
a 400, or that rephrased a refusal, would be a second opinion about a fact this
system spent a phase making single.

**The one decision here is a redirect, and it is about 401 only.** Everything
else becomes a page; a request under this layer's prefix that fails with 401
becomes a 303 to the sign-in page, because the remedy for "your cookie is not a
session" is a form, not a paragraph telling somebody to go and find one. That is
also why the *auth pages themselves* are exempt - a failed sign-in is a 401, and
redirecting it to the sign-in page would loop while discarding the reason it
failed, which is the one thing the person needs to read.

**It does not return the browser to where it was going.** A ``next`` parameter
would, and it is deliberately absent: it is a redirect target taken from the
request, which is the classic way an open redirect gets introduced, and the
validation it would need (this prefix, no scheme, no doubled slash) is more
machinery than the courtesy is worth at this stage. Somebody following a
bookmarked wallet link signs in and lands on their wallet list.
"""

from fastapi.responses import RedirectResponse

from app.presentation.api import errors
from app.presentation.api.errors import ApiError
from app.presentation.web import urls
from app.presentation.web.templating import render
#: The paths that answer 401 on their own terms - the pages whose whole job is to
#: be reached *without* a session. A failure here is rendered rather than
#: redirected; see the module docstring.
_AUTH_PATHS = frozenset(
    {
        urls.SIGN_IN_PATH,
        urls.SIGN_IN_PATH + "/google",
        urls.SIGN_UP_PATH,
        urls.SIGN_UP_PATH + "/google",
        urls.SIGN_OUT_PATH,
    }
)


class MalformedFormError(ApiError):
    """A form arrived without something it cannot do without.

    **400, and it is a refusal this layer makes rather than the domain.** Every
    route here reads its fields by hand - see ``forms.py`` for why - so a missing
    one is caught here and reported in the same ``{"error", "detail"}`` shape the
    domain's refusals use. The alternative was FastAPI's ``Form(...)`` machinery,
    which answers a missing field with a 422 whose body is a validation report
    keyed by field name: correct, machine-readable, and a page a person cannot
    act on. The trade is deliberate and it costs exactly this class.
    """

    status_code = 400


class CrossSitePostError(ApiError):
    """A POST arrived from somewhere else, and the cookie came with it.

    **403 rather than 400**, because the request is well-formed and the caller is
    the problem: this is the refusal that says *this did not come from a page of
    ours*. It is unreachable in an ordinary browser - ``SameSite=Lax`` means a
    cross-site POST carries no cookie, and no page of ours posts anywhere but
    here - so a real occurrence means either an older browser that ignores
    ``SameSite`` or somebody constructing the request. See ``forms.py`` for the
    check and what it can and cannot see.

    ``ApiError`` rather than a domain error, because it is not a fact about money
    or identity. It is the transport observing that the three layers standing
    between this cookie and a forger have all been passed.
    """

    status_code = 403


class Browser:
    """The web layer's answer to "this request failed".

    **One instance, held by ``create_app`` and handed to ``errors.install``**,
    which is the seam that keeps the two renderings from being a choice the error
    module has to make: a handler asks this object whether a request is its
    business and, if it is, hands over. Everything below is a pure function of
    the request and the exception - the class holds no state at all, which is why
    there is one of it rather than one per request.
    """

    def handles(self, request) -> bool:
        """Whether this request belongs to the browser client.

        **The prefix is the whole test**, and it is not a guess about the
        ``Accept`` header. A request under ``/app`` was made by a browser
        navigating or posting - that is what the prefix means and there is no
        second kind of client that uses it - so the routing decision this layer
        already made is the decision to reuse. Content negotiation would be the
        other answer and a worse one: an error raised *after* the response was
        started has no headers to negotiate with, and a client that asked for
        JSON from a page under ``/app`` would be asking for something no route
        here produces.
        """
        return request.url.path.startswith(urls.PREFIX)

    def render(self, request, exc: Exception):
        """The response this failure gets, as a page or as a redirect.

        ``status_for`` decides the grade for every exception tree - a domain
        refusal, this layer's own, the router's 404, or a bug - and ``body_for``
        decides what it says. Both exist in ``app.presentation.api.errors`` for
        exactly this reason, and this method is why they exist: a page that
        graded or phrased a failure differently from the JSON body would be a
        second opinion about a fact that module was written to decide once.

        A 500 therefore renders ``internal server error`` and nothing more,
        because that is what the JSON body carries and this must not be more
        generous than the API is: an unexpected exception's own message can hold
        a path, a SQL fragment or a value out of somebody else's row. Nothing
        here needs to know that, which is the point of asking ``body_for``
        rather than deciding locally.

        **``status`` reaches the response twice, and both are needed.** It is a
        context value so the page can print the number a person is looking at,
        and it is the response's own ``status_code`` so that everything which
        never reads the body gets it too - a browser deciding what to cache, a
        proxy deciding what to pass on, an uptime check deciding whether the
        deployment answered. The two were one for a while, and the half that was
        missing made every refusal in this layer a ``200 OK`` carrying a page
        that said otherwise. A page is not a replacement for the status line; it
        is a second rendering of the same answer, and the answer is the number.
        """
        status = errors.status_for(exc)

        if status == 401 and request.url.path not in _AUTH_PATHS:
            return RedirectResponse(urls.SIGN_IN_PATH, status_code=303)

        body = errors.body_for(exc)
        return render(
            request,
            "error.html",
            status_code=status,
            status=status,
            error=body["error"],
            detail=body["detail"],
        )
