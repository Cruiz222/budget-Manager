"""The session token: where it goes, what it carries, and when it stops working.

**This file exists because the client's credential moved.** In the JSON API a
token is handed to a program once and put in a header by that program's own code;
here it is put in a cookie by this application and re-presented by the browser on
every navigation, without any page asking it to. That is what makes a browser
client possible at all, and it is also the set of properties most worth pinning:
the flags on the header, the absence of the value from every page, and the fact
that signing out ends the session rather than merely hiding it.

The suite drives https - see ``conftest.py`` - so
``test_a_browser_reached_over_https_is_signed_in`` below is not a remark about a
header string. It is why the cookie comes back at all.
"""

import re
from datetime import timedelta
from uuid import uuid4

from app.domain.identity.session import SESSION_LIFETIME
from app.presentation.web import urls
from app.presentation.web.dependencies import COOKIE_PATH, SESSION_COOKIE
from tests.conftest import TEST_USER_PASSWORD
from tests.presentation.web.conftest import ALICE

#: How far the cookie's life is allowed to differ from the session's.
#:
#: The two describe one moment - see ``set_session_cookie`` - but they are
#: computed a moment apart, and ``Max-Age`` is whole seconds, so an exact equality
#: would be a test that fails whenever the machine is busy. A second is more than
#: that slack and far less than any way of getting the lifetime wrong.
SLACK = timedelta(seconds=1)


def max_age_in(response) -> timedelta:
    """How long the response's cookie will live, as the browser will read it."""
    found = re.search(r"Max-Age=(\d+)", response.headers["set-cookie"])
    assert found is not None, response.headers["set-cookie"]
    return timedelta(seconds=int(found.group(1)))


class TestWhatTheCookieCarries:
    def test_signing_in_puts_the_token_in_an_httponly_cookie(self, browser):
        response = browser.sign_up()

        header = response.headers["set-cookie"]
        assert header.startswith(f"{SESSION_COOKIE}=")
        assert "HttpOnly" in header
        assert "Secure" in header
        assert "SameSite=lax" in header
        assert f"Path={COOKIE_PATH}" in header

    def test_the_cookie_lives_exactly_as_long_as_the_session(self, browser):
        """**The two clocks are one clock, and this is the assertion of it.**

        ``LoggedIn.session.expires_at`` is an absolute moment and the cookie's
        ``Max-Age`` is the distance from the *same* ``now`` the use case was
        handed - so the number the browser counts down and the row the server
        checks are the same number. A cookie that outlived its token would give a
        browser that believed itself signed in to a server that refused it; one
        that died early would sign somebody out for a reason nobody could see.
        """
        lived = max_age_in(browser.sign_up())

        assert SESSION_LIFETIME - SLACK < lived <= SESSION_LIFETIME

    def test_a_browser_reached_over_https_is_signed_in(self, browser):
        """**The cookie round-trips, and that is the reason this suite is https.**

        ``Secure`` is on by default, no cookie jar will send a ``Secure`` cookie
        over plain http, and ``TestClient``'s default base URL is http. So a suite
        that ran on the default would have had to set ``WEB_COOKIE_SECURE=0`` to
        work - and would then be green about an installation whose session token
        crosses the network in the clear.
        """
        browser.sign_up()

        page = browser.page(urls.LANDING_PATH)

        assert ALICE in page


class TestTheTokenIsNeverInAPage:
    """**Nothing a browser can read may contain the credential.**

    ``HttpOnly`` keeps the token away from JavaScript, and the claim here is the
    weaker but more total one: it is not in the *document* either. Server-rendered
    HTML is the one place this system writes a response body out of data it holds,
    so a token that reached a template is a token in a page somebody could save,
    forward, or paste into a bug report.
    """

    def test_the_landing_page_does_not_carry_it(self, browser):
        browser.sign_up()
        token = browser.token

        assert token not in browser.page(urls.LANDING_PATH)

    def test_the_wallet_page_does_not_carry_it(self, browser):
        wallet_id = browser.funded_wallet()
        token = browser.token

        assert token not in browser.wallet_page(wallet_id)

    def test_a_confirmation_page_does_not_carry_it(self, browser):
        wallet_id = browser.funded_wallet()
        confirmation_id = browser.request_withdrawal(wallet_id)
        token = browser.token

        assert token not in browser.confirmation_page(confirmation_id)

    def test_a_refusal_page_does_not_carry_it(self, browser):
        """The page an unexpected failure produces, which is rendered by a path
        that had an exception in hand - the one most able to leak a value into a
        message."""
        browser.sign_up()
        token = browser.token

        page = browser.get(f"{urls.PREFIX}/wallets/{uuid4()}")

        assert page.status_code == 404
        assert token not in page.text

    def test_the_sign_in_page_does_not_carry_it(self, browser):
        browser.sign_up()
        token = browser.token
        browser.sign_out()

        assert token not in browser.page(urls.SIGN_IN_PATH)


class TestSigningOut:
    def test_it_empties_the_cookie(self, browser):
        browser.sign_up()
        assert browser.token

        response = browser.sign_out()

        assert response.headers["location"] == urls.SIGN_IN_PATH
        assert browser.token is None

    def test_it_ends_the_session_and_not_merely_the_cookie(self, browser):
        """**The half a browser cannot see, and the one that decides.**

        A cookie is a string a browser keeps copies of - in a second tab, in a
        saved page, on a machine that was backed up. Deleting it takes away one
        copy and proves nothing about the rest, so the claim worth making is about
        the *row*: the token that was in that cookie is not a session any more,
        and the JSON API refuses it in the same words it refuses any other dead
        token.
        """
        browser.sign_up()
        token = browser.token

        browser.sign_out()

        refused = browser.get(
            "/users/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert refused.status_code == 401

    def test_the_next_page_sends_the_browser_to_the_form(self, browser):
        browser.sign_up()
        browser.sign_out()

        page = browser.get(urls.LANDING_PATH)

        assert page.status_code == 303
        assert page.headers["location"] == urls.SIGN_IN_PATH

    def test_signing_out_with_no_cookie_at_all_is_fine(self, browser):
        """There is nothing to end and the postcondition already holds.

        Refusing this would be refusing a request whose whole effect is to leave
        the caller with no session - and a person whose cookie has already expired
        is exactly who would send it.
        """
        response = browser.post(urls.SIGN_OUT_PATH)

        assert response.status_code == 303
        assert response.headers["location"] == urls.SIGN_IN_PATH


class TestAPageWithoutASession:
    def test_it_redirects_to_the_form(self, browser):
        page = browser.get(urls.LANDING_PATH)

        assert page.status_code == 303
        assert page.headers["location"] == urls.SIGN_IN_PATH

    def test_the_form_itself_renders_rather_than_redirecting(self, browser):
        """**The one place a 401 is not a redirect**, and the reason is a loop.

        The remedy for "your cookie is not a session" is a form, so the renderer
        sends a browser to it - except on the pages whose whole job is to be
        reachable without one. A failed sign-in is a 401, and redirecting it to
        the sign-in page would discard the reason it failed, which is the one
        thing the person needs to read.
        """
        page = browser.get(urls.SIGN_IN_PATH)

        assert page.status_code == 200
        assert "Sign in" in page.text

    def test_a_failed_sign_in_says_so_on_the_form(self, browser):
        browser.sign_up()

        response = browser.post(
            urls.SIGN_IN_PATH,
            data={"identifier": ALICE, "password": "not-the-password"},
        )

        assert response.status_code == 401
        assert TEST_USER_PASSWORD not in response.text
