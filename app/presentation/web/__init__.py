"""The browser half: the third presentation, and the first one with a screen.

**Why a third package rather than more routes in ``api``.** ``app.presentation``
had two members before this one and they are not the same kind of thing. The CLI
is an adapter over the same use cases for a person at a terminal; the API is an
adapter over them for a program. This is an adapter over them for a *browser*,
and what makes it a package rather than a third module is that it needs things
neither of the others has: a template engine, a form reader, a stylesheet, and a
credential that travels in a cookie rather than a header. None of that belongs
in a module whose whole subject is a JSON contract.

**It presents the same domain and reuses the same wiring.** Every page here ends
at a use case in ``app.application`` reached through a builder in
``app.composition_root`` - the same ones the API calls, built the same way. What
differs is the *shape of the question*: where the API resolves an actor from an
``Authorization`` header, this resolves one from a session cookie, and where the
API serialises a refusal into ``{"error", "detail"}``, this renders it into a
page. Those are two renderings of one system, not two systems.

**Nothing in this package is imported by ``api``, and the dependency runs the
other way on purpose.** ``app.presentation.api.app:create_app`` includes this
layer's routers and mounts its static files, because there is one application
and one port. That is the whole of the coupling: the API builds the app and the
web layer hangs off it. See ``app.presentation.api.errors`` for the one other
place the two meet, which is where a refusal becomes a page instead of a body.

The files, in the order a reader should meet them:

- ``urls.py`` - the paths, and the one join that makes a deposit returnable.
- ``templates/`` - what a page is made of.
- ``static/`` - the one stylesheet ``create_app`` mounts.
- ``forms.py`` - what a browser sends, and what is refused.
- ``dependencies.py`` - the cookie, and who it makes the request.
- ``routes.py`` - every route, and the only file that needs reading to know what
  this layer does.
- ``errors.py`` - a failure, as a page.
"""

from pathlib import Path

#: Where this package's files are, and where the stylesheet is.
#:
#: **Resolved from this file rather than from the working directory**, and it
#: lives on the package rather than in ``urls.py`` or ``templating.py`` because it
#: is a fact about the layer's *files* and not about its routes or its templates.
#: ``create_app`` needs it to mount the stylesheet and is the only importer;
#: ``templating.py`` resolves its own directory the same way, one line of
#: duplication accepted so that neither module has to import this one to find
#: itself.
STATIC_DIRECTORY = Path(__file__).parent / "static"
