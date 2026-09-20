"""Signing in with Google, from the page rather than from a terminal.

**The id_token flow, and the difference it makes here.** Google Identity Services
hands a token to the *page*, and the page form-posts it to a route of ours - so
the browser sees a Google credential and never one of ours, and the server
verifies the token exactly as it verifies the CLI's. No ``redirect_uri``, no
client secret, no authorization code: what Google needs for the arrangement is an
authorized JavaScript origin in its console, which is a fact about Google's side
and is why nothing on this side has a setting for it.

**What these tests replace is the cryptography and nothing else.** The verifier is
``tests/conftest.py``'s recording stub - see ``FakeGoogleIdentityVerifier`` for
why an honest fixture cannot check a signature - so "this token is valid" means
"the test said so". Everything above it is real: the route, the form field, the
two use cases, the session, the cookie, and the page. The signature check itself
is tested against real signed tokens in
``tests/infrastructure/identity/test_pyjwt_google_identity_verifier.py``.

**The one shape a browser client has that the API does not** is that signing up
and signing in are two addresses. ``POST /users/google`` deliberately creates an
account and answers with no session; ``POST /sessions/google`` deliberately
creates nothing. A page cannot make two round trips out of one click and still
have the person signed in at the end, so the sign-up route makes both calls -
which is what the first class below is about.
"""

import pytest
from fastapi.testclient import TestClient

from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)
from app.infrastructure.settings import GoogleSettings
from app.presentation.api.app import create_app
from app.presentation.web import urls
from tests.conftest import TEST_GOOGLE_SETTINGS
from tests.presentation.web.conftest import ALICE, BASE_URL, Browser

#: What the page's hidden field posts, and what the test posts to it.
#:
#: ``id_token`` belongs to this system - it is the name in the API's schema, in
#: the use case and in the verifier - while ``credential`` is what Google's own
#: JavaScript calls it. The rename happens once, in ``_google_button.html``, and
#: a test that posted ``credential`` would be testing a field no route declares.
FIELD = "id_token"


@pytest.fixture
def verifier(build_google_verifier):
    """The recording stub this module's application is wired to."""
    return build_google_verifier()


@pytest.fixture
def google_browser(db_path, password_hasher, verifier):
    """A browser against an application that has a Google identity configured.

    The ordinary ``browser`` fixture cannot serve these tests, and the reason is
    worth stating: ``GOOGLE_CLIENT_ID`` is cleared for the whole suite, so that
    application has no client id and no verifier and every Google route in it
    answers a 503. That is a real behaviour and it is asserted below - what this
    fixture is for is the *other* installation, built with both injected.
    """
    app = create_app(
        unit_of_work_factory=SqliteUnitOfWorkFactory(db_path),
        password_hasher=password_hasher,
        google_settings=TEST_GOOGLE_SETTINGS,
        google_verifier=verifier,
    )
    with TestClient(app, base_url=BASE_URL, follow_redirects=False) as client:
        yield Browser(client, db_path)


class TestSigningUpWithGoogle:
    def test_it_registers_creates_a_session_and_lands_on_the_wallets(
        self, google_browser, verifier
    ):
        """**Two calls behind one button**, and the second is a proof rather than
        a lookup: the same token is verified twice, for two different questions.
        Neither answer is guessed from the other, and neither is a find-or-create.
        """
        token = verifier.mint("google-subject-1", ALICE)

        response = google_browser.post(
            f"{urls.SIGN_UP_PATH}/google", data={FIELD: token}
        )

        assert response.status_code == 303
        assert response.headers["location"] == urls.LANDING_PATH
        assert google_browser.token is not None

    def test_the_person_is_signed_in_without_doing_anything_else(
        self, google_browser, verifier
    ):
        """The claim ``POST /users/google`` cannot make on its own: that route
        answers with the account and no session, deliberately, and a form post
        that left somebody on a sign-in page having just proved who they are
        would be the layer's mistake rather than the use case's."""
        google_browser.post(
            f"{urls.SIGN_UP_PATH}/google",
            data={FIELD: verifier.mint("google-subject-2", ALICE)},
        )

        page = google_browser.page(urls.LANDING_PATH)

        assert ALICE in page

    def test_the_account_it_created_can_sign_in_again(
        self, google_browser, verifier
    ):
        """**The two halves of the pair, against each other.** Sign-up creates,
        sign-in does not - so the second call is meaningful only because the first
        one worked, and a sign-in that quietly created an account would make this
        test pass for the wrong reason."""
        token = verifier.mint("google-subject-3", ALICE)
        google_browser.post(f"{urls.SIGN_UP_PATH}/google", data={FIELD: token})
        google_browser.sign_out()

        response = google_browser.post(
            f"{urls.SIGN_IN_PATH}/google", data={FIELD: token}
        )

        assert response.status_code == 303
        assert google_browser.token is not None

    def test_it_asks_the_verifier(self, google_browser, verifier):
        """The route's own docstring says it verifies "exactly as
        ``POST /sessions/google`` does". This is that sentence as a test: the
        token the page posted is the token the verifier was handed, twice."""
        token = verifier.mint("google-subject-4", ALICE)

        google_browser.post(f"{urls.SIGN_UP_PATH}/google", data={FIELD: token})

        assert verifier.attempts == [token, token]


class TestSigningInWithGoogle:
    def test_a_token_naming_nobody_is_refused(self, google_browser):
        """**401, and no account.** ``LogInWithGoogle`` creates nothing, which is
        what makes the refusal meaningful - the remedy for somebody with no
        account is the sign-up page beside this one."""
        response = google_browser.post(
            f"{urls.SIGN_IN_PATH}/google", data={FIELD: "not-a-google-token"}
        )

        assert response.status_code == 401

    def test_and_it_does_not_sign_anybody_in(self, google_browser):
        google_browser.post(
            f"{urls.SIGN_IN_PATH}/google", data={FIELD: "not-a-google-token"}
        )

        assert google_browser.token is None

    def test_a_token_naming_nobody_is_not_a_registration(self, google_browser):
        """The same claim from the other side: the sign-in route's refusal must
        not have left an account behind. If it had, this second call would
        succeed - because it is the same token."""
        google_browser.post(
            f"{urls.SIGN_IN_PATH}/google", data={FIELD: "not-a-google-token"}
        )

        again = google_browser.post(
            f"{urls.SIGN_IN_PATH}/google", data={FIELD: "not-a-google-token"}
        )

        assert again.status_code == 401

    def test_signing_up_twice_with_one_identity_is_refused(
        self, google_browser, verifier
    ):
        """``DuplicateGoogleSubjectError``, which is the constraint that makes a
        Google identity an identity rather than a way in."""
        token = verifier.mint("google-subject-5", ALICE)
        google_browser.post(f"{urls.SIGN_UP_PATH}/google", data={FIELD: token})
        google_browser.sign_out()

        again = google_browser.post(
            f"{urls.SIGN_UP_PATH}/google", data={FIELD: token}
        )

        assert again.status_code == 409

    def test_an_address_that_already_has_a_password_account_is_refused(
        self, google_browser, verifier
    ):
        """Two ways to become one account, and the second is refused rather than
        merged - which is ``POST /users/google``'s rule, arriving here as a
        duplicate-email conflict."""
        google_browser.sign_up(ALICE)
        google_browser.sign_out()

        response = google_browser.post(
            f"{urls.SIGN_UP_PATH}/google",
            data={FIELD: verifier.mint("google-subject-6", ALICE)},
        )

        assert response.status_code == 409

    def test_an_empty_credential_is_a_malformed_form(self, google_browser):
        """The field is absent or blank when GIS has not finished loading and
        somebody clicks anyway, and the refusal names the box rather than failing
        at the verifier."""
        response = google_browser.post(
            f"{urls.SIGN_IN_PATH}/google", data={FIELD: "   "}
        )

        assert response.status_code == 400


class TestTheButtonItself:
    def test_the_page_carries_the_client_id_and_google_script(self, google_browser):
        page = google_browser.page(urls.SIGN_IN_PATH)

        assert TEST_GOOGLE_SETTINGS.client_id in page
        assert "accounts.google.com/gsi/client" in page

    def test_the_client_id_is_a_javascript_literal_and_not_markup(
        self, db_path, password_hasher, verifier
    ):
        """**The one value in this layer that reaches a page inside markup.**

        Everything else a page prints is text between two tags, and Jinja's
        autoescaping is all it needs. The client id goes into a ``<script>``
        block as an object literal, which is a different escaping problem: HTML
        escaping is not a defence there, because the danger is a value that closes
        the script tag.

        So the test uses a client id that *would* - a deployment whose
        configuration is hostile is the only arrangement in which ``|safe``,
        ``|tojson`` and a bare interpolation are distinguishable, and the three
        differ in nothing else. ``tojson`` writes ``\\u003c`` where the bare
        version writes ``<``; a template that reached for ``|safe`` here would
        look reasonable, would pass every other test in this file, and would be
        the one unescaped interpolation in the layer.

        See ``templating.py`` for the rule this is the exception-that-is-not.
        """
        hostile = "</script><script>alert(1)</script>"
        app = create_app(
            unit_of_work_factory=SqliteUnitOfWorkFactory(db_path),
            password_hasher=password_hasher,
            google_settings=GoogleSettings(client_id=hostile),
            google_verifier=verifier,
        )
        with TestClient(app, base_url=BASE_URL, follow_redirects=False) as client:
            page = Browser(client, db_path).page(urls.SIGN_IN_PATH)

        assert hostile not in page
        assert "\\u003c/script\\u003e" in page

    def test_the_sign_up_page_carries_it_too(self, google_browser):
        page = google_browser.page(urls.SIGN_UP_PATH)

        assert TEST_GOOGLE_SETTINGS.client_id in page

    def test_an_installation_with_no_client_id_explains_itself(self, browser):
        """**The ordinary state of a fresh clone, rendered rather than refused.**

        ``GOOGLE_CLIENT_ID`` is cleared for the whole suite, so ``browser`` here
        is an application with no Google configuration at all. The page says so
        instead of drawing a button that could not work - and instead of a 503,
        which would take the sign-in page down over one optional way in.
        """
        page = browser.page(urls.SIGN_IN_PATH)

        assert "accounts.google.com/gsi/client" not in page
        assert "Google" in page

    def test_and_its_google_route_refuses_rather_than_pretending(self, browser):
        """What that installation does *not* do is accept a token. The refusal is
        the API's own 503, because the missing thing is the installation's and
        not the caller's."""
        response = browser.post(
            f"{urls.SIGN_IN_PATH}/google", data={FIELD: "anything"}
        )

        assert response.status_code == 503
