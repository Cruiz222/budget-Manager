"""The origin check: the layer that has to compare two values spelled differently.

``forms.reject_cross_site`` is the third of the three layers standing between a
cookie and a forger, and it is the one that could plausibly be got *wrong* rather
than merely left out - because what it has to compare is a ``Host`` header, which
behind a TLS-terminating proxy reads ``budget.example``, against an ``Origin``,
which reads ``https://budget.example``. A comparison of those two strings refuses
every real user on the one deployment that matters.

So the tests below are in two halves, and the second is the one that earns the
file: **the refusals**, and then the four requests that must *not* be refused.
A check that failed closed would pass every refusal test here and take the
product down, and a suite that only wrote the first half could not tell the two
apart.
"""

from app.presentation.web import urls
from tests.conftest import TEST_USER_PASSWORD
from tests.presentation.web.conftest import ALICE, BASE_URL

#: The host this suite's requests are addressed to, without a scheme.
#:
#: Read off ``conftest``'s base URL rather than written out, because the whole
#: subject here is the difference between a host and an origin - and a test that
#: hard-coded the host would go on passing if the base URL changed to one whose
#: ``Host`` was different, which is precisely the case being tested.
HOST = BASE_URL.split("://", 1)[1]

#: An origin that is not this application's, in the two ways one can differ.
SOMEBODY_ELSE = "https://evil.example"
TAKEN_LITERALLY = "null"


class TestWhatIsRefused:
    def test_a_post_from_another_origin(self, browser):
        response = browser.post(
            urls.SIGN_IN_PATH,
            data={"identifier": ALICE, "password": TEST_USER_PASSWORD},
            headers={"Origin": SOMEBODY_ELSE},
        )

        assert response.status_code == 403

    def test_a_post_from_another_origin_cannot_move_money(self, browser):
        wallet_id = browser.funded_wallet()

        response = browser.post(
            f"{urls.PREFIX}/wallets/{wallet_id}/withdrawals",
            data={"amount": "1.00"},
            headers={"Origin": SOMEBODY_ELSE},
        )

        assert response.status_code == 403

    def test_an_origin_that_is_not_a_url(self, browser):
        """**``Origin: null``, which is what a sandboxed frame and a ``file://``
        page send** - and the value that makes this a function rather than a
        comparison at a call site. ``urlsplit("null").netloc`` is ``""``, which
        equals no real host, so it is refused for the same reason a foreign
        origin is. A check written as ``origin != expected`` would refuse it too;
        one written as ``expected in origin`` would not.
        """
        response = browser.post(
            urls.SIGN_IN_PATH,
            data={"identifier": ALICE, "password": TEST_USER_PASSWORD},
            headers={"Origin": TAKEN_LITERALLY},
        )

        assert response.status_code == 403

    def test_an_origin_on_another_port(self, browser):
        """A port is part of an origin, and dropping it from the comparison
        would make every service on the host interchangeable. ``forms``' own
        docstring makes this claim; this is the test of it."""
        response = browser.post(
            urls.SIGN_IN_PATH,
            data={"identifier": ALICE, "password": TEST_USER_PASSWORD},
            headers={"Origin": f"https://{HOST}:8443"},
        )

        assert response.status_code == 403

    def test_a_foreign_origin_is_not_rescued_by_a_trusting_referer(self, browser):
        """**No fallback from a mismatching ``Origin`` to ``Referer``.**

        Both headers are on the browser's forbidden list, so a page cannot set
        either. A request carrying an ``Origin`` that is not ours was therefore
        made by a browser on a page that is not ours, or composed deliberately -
        and falling back would only ever rescue the second kind.
        """
        response = browser.post(
            urls.SIGN_IN_PATH,
            data={"identifier": ALICE, "password": TEST_USER_PASSWORD},
            headers={"Origin": SOMEBODY_ELSE, "Referer": f"{BASE_URL}/app/"},
        )

        assert response.status_code == 403

    def test_a_foreign_referer_is_refused_too(self, browser):
        response = browser.post(
            urls.SIGN_IN_PATH,
            data={"identifier": ALICE, "password": TEST_USER_PASSWORD},
            headers={"Referer": f"{SOMEBODY_ELSE}/app/"},
        )

        assert response.status_code == 403


class TestWhatIsAllowed:
    """**The half that keeps the check from taking the product down.**"""

    def test_an_origin_whose_scheme_differs_and_whose_host_is_ours(self, browser):
        """**The Caddy case, and the reason the comparison is of hosts.**

        This application runs behind Caddy, which terminates TLS: the request
        that reaches the application carries ``Host: budget.example`` while the
        browser's ``Origin`` reads ``https://budget.example``. Here that is
        ``Origin: http://testserver`` against ``Host: testserver`` - the same
        request with the same disagreement, and it must be *accepted*. A check
        that compared origins would refuse every real user, on the one deployment
        that matters.
        """
        browser.sign_up()

        response = browser.post(
            urls.SIGN_IN_PATH,
            data={"identifier": ALICE, "password": TEST_USER_PASSWORD},
            headers={"Origin": f"http://{HOST}"},
        )

        assert response.status_code == 303

    def test_an_origin_matching_in_every_respect(self, browser):
        browser.sign_up()

        response = browser.post(
            urls.SIGN_IN_PATH,
            data={"identifier": ALICE, "password": TEST_USER_PASSWORD},
            headers={"Origin": BASE_URL},
        )

        assert response.status_code == 303

    def test_a_referer_with_no_origin_at_all(self, browser):
        """``Referer`` is consulted only when ``Origin`` is absent, which is what
        an older browser sends. It is compared the same way."""
        browser.sign_up()

        response = browser.post(
            urls.SIGN_IN_PATH,
            data={"identifier": ALICE, "password": TEST_USER_PASSWORD},
            headers={"Referer": f"{BASE_URL}{urls.SIGN_IN_PATH}"},
        )

        assert response.status_code == 303

    def test_neither_header_at_all(self, browser):
        """**An absent header is allowed, and the default is correct rather than
        a hole.**

        A browser that sends an ``Origin`` sends it on every POST it makes, so a
        request with neither header is not a browser on somebody else's page - it
        is a client that could have set one if it wanted to, and whose actual
        defence is the credential it holds. Refusing here would break the CLI's
        own tests and every program that posts a form.
        """
        browser.sign_up()

        response = browser.post(
            urls.SIGN_IN_PATH,
            data={"identifier": ALICE, "password": TEST_USER_PASSWORD},
        )

        assert response.status_code == 303

    def test_money_still_moves_with_a_trusting_origin(self, browser):
        """The refusals above are about a *check*, and a check that refused the
        real thing would make them all pass. So the whole withdrawal flow is run
        once with an origin on it."""
        wallet_id = browser.funded_wallet()
        headers = {"Origin": BASE_URL}

        requested = browser.post(
            f"{urls.PREFIX}/wallets/{wallet_id}/withdrawals",
            data={"amount": "500.00"},
            headers=headers,
        )

        assert requested.status_code == 303
        confirmation_id = requested.headers["location"].rsplit("/", 1)[-1]
        answered = browser.post(
            f"{urls.PREFIX}/confirmations/{confirmation_id}/confirm",
            headers=headers,
        )

        assert answered.status_code == 303
        assert "9500.00" in browser.wallet_page(wallet_id)
