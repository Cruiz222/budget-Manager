"""Two presentations, one address space, and nothing between them to collide.

**The API's namespace is flat and prefix-free by deliberate policy** - ``/wallets/
{id}``, ``/sessions``, ``/confirmations/{id}`` - which is a good property of a
JSON contract and a hostile one to hang a second client on. The pages therefore
live under ``/app``, and the two sets must not meet: the day one does, the loser
is whichever router was registered second, **silently** - a request goes to the
wrong handler and answers with something plausible.

So the test is not "does ``/wallets`` clash today" but "can anything land under
``/app`` without this layer having declared it", and it is derived from
``create_app().routes`` rather than from a written-out list, for the reason every
schema test is: a route added later is covered by the test without anybody
remembering to add it.

**The paths are gathered from three places, deliberately**, because a single
source would be the thing checking itself. The API's come from the router modules
``create_app`` registers; this layer's come from the two routers it registers for
the web; and both are then compared against what the assembled application
actually holds.
"""

from app.presentation.api.routes import (
    confirmations,
    email_changes,
    funds,
    health,
    password_resets,
    phone_verifications,
    plans,
    profiles,
    sessions,
    users,
    wallets,
    webhooks,
)
from app.presentation.web import urls
from app.presentation.web import routes as web_routes

#: The API's routers, in the order ``create_app`` includes them.
#:
#: Spelled out here rather than imported as a tuple from ``app.py``, because
#: importing the list from the thing under test would make "one was added and
#: nobody updated this file" invisible - which is the one failure this module
#: exists to catch. A module added to ``app.py`` and not here would leave its
#: paths out of ``api_paths`` below, and the count assertion at the end is what
#: notices.
API_MODULES = (
    health,
    users,
    profiles,
    sessions,
    email_changes,
    password_resets,
    phone_verifications,
    wallets,
    funds,
    plans,
    confirmations,
    webhooks,
)

#: The paths FastAPI mounts itself. Not the API's and not this layer's: they are
#: the framework's, they do not start with ``/app``, and naming them means the
#: count assertion below can be about the two presentations rather than about
#: ``/docs``.
FRAMEWORK_PATHS = frozenset(
    {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
)


def api_paths() -> set[str]:
    """Every path the JSON API declares, from its own routers."""
    return {route.path for module in API_MODULES for route in module.router.routes}


def web_paths() -> set[str]:
    """Every path this layer declares, from its own routers.

    Both of them: the prefixed one holds the thirteen pages and the second holds
    the one path that is deliberately outside the prefix - see
    ``web_routes.root``.
    """
    return {route.path for route in web_routes.router.routes} | {
        route.path for route in web_routes.root.routes
    }


def registered_paths(app) -> set[str]:
    """Every path the assembled application holds, framework's included."""
    return {route.path for route in app.routes}


class TestTheTwoNamespacesAreDisjoint:
    def test_no_api_path_is_under_the_web_prefix(self, app):
        """**The assertion the file exists for.**

        A JSON route that landed under ``/app`` would be answered by this layer's
        error renderer for every one of its failures - a page where a program is
        waiting for a body - and would be unreachable at the address the API
        documents.
        """
        intruders = sorted(
            path for path in api_paths() if path.startswith(urls.PREFIX)
        )

        assert intruders == []

    def test_no_web_path_is_outside_the_prefix(self, app):
        """With one exception, and it is named rather than filtered: the front
        door, which is ``/`` and redirects into the pages."""
        outside = sorted(
            path
            for path in web_paths()
            if not path.startswith(urls.PREFIX) and path != "/"
        )

        assert outside == []

    def test_nothing_is_in_both(self, app):
        assert not (api_paths() & web_paths())

    def test_the_two_sets_are_both_real(self, app):
        """**The guard on all three above.** Two empty sets are disjoint, so a
        mis-derived ``api_paths`` would make every test here pass by having
        nothing to compare. Two paths named from each side rather than a count,
        because a count nobody can check by reading is a number a reader has to
        take on trust - and it is the number that would be edited to make a
        failure go away."""
        assert "/users/me" in api_paths()
        assert "/webhooks/paystack" in api_paths()
        assert urls.LANDING_PATH in web_paths()
        assert urls.SIGN_IN_PATH in web_paths()

    def test_the_api_is_the_one_the_application_registers(self, app):
        """The API's paths, as its routers declare them, are all present in the
        running application - which is what makes the disjointness claim above a
        claim about the thing that serves requests rather than about a list."""
        assert api_paths() <= registered_paths(app)

    def test_the_application_holds_nothing_else(self, app):
        """**The count, and it is the assertion that nothing was left out.**

        Every path the assembled application has is one of four things: an API
        route, a page, one of FastAPI's own four, or the static mount. A router
        added to ``create_app`` and not to ``API_MODULES`` above would make this
        fail, which is what stops the list at the top of this file from becoming
        a stale copy of the one in ``app.py``.
        """
        assert len(registered_paths(app)) == (
            len(api_paths()) + len(web_paths()) + len(FRAMEWORK_PATHS) + 1
        )


class TestUnderTheWebPrefixIsOnlyThisLayer:
    def test_every_registered_page_is_one_of_ours(self, app):
        """**The direction that catches a silent collision.**

        Anything answering under ``/app`` is a page, so anything under ``/app``
        must be one this layer declared. A route added to the API with a path
        that happened to begin with the prefix would fail here, at the moment it
        was added, rather than in production as a JSON endpoint that returns HTML.
        """
        under = {
            path
            for path in registered_paths(app)
            if path.startswith(urls.PREFIX)
        }

        assert under - web_paths() == {urls.STATIC_PREFIX}

    def test_the_only_page_outside_the_prefix_is_the_front_door(self, app):
        """``/`` is this layer's one route outside ``/app``, and it is registered
        by the second router rather than by the prefixed one - which is what
        ``web_routes.root`` is, and what makes the exception visible in the
        module that contains it."""
        outside = sorted(
            path
            for path in registered_paths(app)
            if not path.startswith(urls.PREFIX)
            and path not in FRAMEWORK_PATHS
            and path not in api_paths()
        )

        assert outside == ["/"]

    def test_the_front_door_is_registered(self, app):
        assert "/" in registered_paths(app)


class TestTheStylesheet:
    """``/app/static`` is a ``Mount`` rather than an ``APIRoute``, which is why
    it is asserted separately: it is the one path under the prefix that no router
    in this layer declared, and the test above excludes it by name."""

    def test_it_is_mounted_under_the_prefix(self, app):
        mounted = [
            route.path
            for route in app.routes
            if type(route).__name__ == "Mount"
        ]

        assert mounted == [urls.STATIC_PREFIX]

    def test_the_stylesheet_is_served(self, browser):
        response = browser.get(f"{urls.STATIC_PREFIX}/app.css")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/css")

    def test_a_file_that_is_not_there_is_a_404(self, browser):
        assert browser.get(f"{urls.STATIC_PREFIX}/nope.css").status_code == 404
