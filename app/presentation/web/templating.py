"""The template environment: one of them, built once, and read-only.

**Autoescaping is the whole reason this module exists**, and it is worth saying
so plainly because a reader looking for a Jinja setup will find four lines and
wonder what the fuss is. Jinja2 escapes by default when the environment is built
from a file loader, and this system's pages print values somebody else supplied:
the address an account signed up with, the prose of a domain refusal, the
reference a person typed into a withdrawal form. An unescaped one of those is a
script injected into the page that holds the session cookie - and because that
cookie is ``HttpOnly``, the script cannot read it, which is not a defence: it
posts the withdrawal form on the visitor's behalf instead. ``requirements.txt``
carries the fuller argument under the ``Jinja2`` line.

**``autoescape=True`` is written out rather than left to the default.** It is
what Jinja does with a file loader, and spelling it means a later edit that
builds the environment some other way cannot quietly lose it. The companion rule
is a convention rather than a setting: **no template in this directory uses
``|safe``**, on any value, for any reason. The one value that reaches a page
inside markup rather than as text is the Google client id, in
``_google_button.html``, and it goes through ``tojson`` - which is a Jinja
built-in that escapes ``<``, ``>``, ``&`` and ``'`` out of the way, and is
therefore the opposite of ``|safe`` rather than an exception to the rule. It is
also configured data rather than anything a person supplied, which is worth
knowing but is not the argument.

**One environment for the process, built at import.** Jinja's environment holds
a compiled-template cache, so building one per request would recompile seven
templates on every page view. It holds no per-request state - the loader is a
directory, the globals below are constants - which is what makes it safe to
share across FastAPI's threadpool, exactly as ``create_app`` shares one payment
provider and one rate limiter. The context that varies per request is passed to
``render`` and never stored.

**``urls`` is injected as a global rather than passed to every render call.** The
alternative is every route passing the same six constants into every template,
which is a line of noise in each one and a chance to forget one. A global is
what the module is: a constant namespace, read by the template that needs it.
"""

from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.presentation.web import urls

#: Where the templates are. Resolved from this file rather than from the working
#: directory, because a server started from somewhere else - which is what
#: ``uvicorn`` does under a process manager - must still find them.
TEMPLATES_DIRECTORY = Path(__file__).parent / "templates"

#: The one environment. ``Jinja2Templates`` is FastAPI's thin wrapper around a
#: ``jinja2.Environment``, and it is used rather than the raw class for exactly
#: one reason: it gives ``TemplateResponse`` a ``request`` and does the
#: bookkeeping Starlette's signature asks for, so no route here has to know that
#: a response object wants the request twice.
templates = Jinja2Templates(directory=str(TEMPLATES_DIRECTORY))

# ``autoescape`` is the default with a file loader and is set here anyway; see the
# module docstring. ``undefined`` is deliberately left alone: Jinja's default
# raises on a missing variable in an output position, which turns a template that
# forgot a context value into a 500 at the first render rather than a page with a
# hole in it. A hole is the failure that ships.
templates.env.autoescape = True
templates.env.globals["urls"] = urls


def render(request: Request, template: str, status_code: int = 200, **context):
    """One page, as a response.

    ``request`` comes first and by position because Starlette's template
    machinery needs it to build the response; everything else is a keyword, so a
    call site reads as the values the page is given rather than as an argument
    list. Nothing is added here that a caller did not pass - in particular the
    session token is never put in a context, and no template has a variable that
    could hold it. See ``TestTheTokenIsNeverInAPage`` in
    ``tests/presentation/web/test_credential.py``.

    **``status_code`` defaults to 200 and every page but one wants that.** The
    exception is ``errors.Browser``: a refusal rendered as a page is *still a
    refusal*, and the number on the status line is the only part of it a client,
    a proxy or a monitor can see. A 404 page answered with ``200 OK`` is a page
    that tells a browser to cache it, tells a search engine to index it and
    tells every uptime check that the deployment is healthy - and the body
    saying "404" is invisible to all three. So the status is a parameter here
    rather than a constant, and the renderer that grades a failure passes its
    grade through to the response rather than only into the template.
    """
    return templates.TemplateResponse(
        request, template, context, status_code=status_code
    )
