"""Fixtures for the browser tests.

``tests/presentation/api/conftest.py``'s shape, one presentation over: a real
database file under ``tmp_path``, real services, real routes, real error handlers,
and starlette's ``TestClient`` driving the application in process. Nothing about
the *application* is replaced except the two things cost forbids - argon2 and the
network - exactly as the API suite does it.

**Two things are different here, and both are facts about a browser.**

*The requests go over ``https``.* ``TestClient``'s default base URL is
``http://testserver``, and no cookie jar sends a ``Secure`` cookie over http -
``http.cookiejar`` implements that rule and httpx uses it. So a suite on the
default would have had to set ``WEB_COOKIE_SECURE=0`` to work at all, which is the
wrong trade twice over: the flag's default is what belongs in production, and a
suite that turned it off to test itself would never notice if the default changed.
Driving https instead means the session cookie round-trips *because* ``Secure`` is
on, so every test here is also a test of that.

*Redirects are not followed.* Every POST in this layer answers 303 - see
``routes.py`` - and that is the property the money tests are about. A client that
followed them would turn "the form redirected to the confirmation page" into "the
confirmation page rendered", which is a weaker claim. A test that means to follow
asks for the destination: ``Location`` is on the response either way.

**That is an argument and not a default**: ``TestClient`` follows redirects unless
it is told not to, so ``follow_redirects=False`` is spelled at every construction
below - including the two fixtures in other modules that build their own client.
It is called out because the paragraph above stood here for a while without the
line, and every test written against it was silently asserting on the page at the
far end of a 303.

**The actions live on a ``Browser`` rather than in a handful of fixtures**, and
the reason is the isolation tests. Almost everything this layer does needs to say
*which* browser it happened in, and a fixture that defaulted that - the house
pattern everywhere else in this suite - would be a fixture whose default is wrong
exactly where the answer matters most. A test that asks a second browser to
request a withdrawal has to be able to say so, and passing the browser in makes
that impossible to forget rather than merely visible.
"""

import re
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.composition_root import build_wallet_service
from app.domain.money.currency import Currency
from app.domain.money.money import Money
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)
from app.presentation.api.app import create_app
from app.presentation.web import urls
from app.presentation.web.dependencies import SESSION_COOKIE
from tests.conftest import TEST_USER_PASSWORD

#: The two addresses the web suite registers, named for the same reason the API
#: suite names its own: ``example.com`` is reserved, so nobody can mistake one for
#: a person, and it has a real domain, so a deposit against it would be billable.
ALICE = "alice@example.com"
BOB = "bob@example.com"

#: Where this suite's requests are addressed.
#:
#: ``https`` and not the default, for the reason the module docstring gives.
#: ``testserver`` is httpx's own default host, so this changes the scheme and
#: nothing else - and ``reject_cross_site`` compares the ``Origin`` header's host
#: against the ``Host`` header, both of which are this value.
BASE_URL = "https://testserver"

#: A wallet link, as ``wallets.html`` writes it.
#:
#: **This suite reads ids out of pages rather than out of a database**, and that
#: is the point rather than a shortcut: a browser has no other way to learn one,
#: so a test that reached into the store for a wallet id would be testing an
#: arrangement no person can have.
WALLET_LINK = re.compile(
    re.escape(f'href="{urls.PREFIX}/wallets/') + r"([0-9a-fA-F-]{36})"
)

#: A confirmation's path, as this layer spells it. Applied to the ``Location`` of
#: a withdrawal's 303, which is where a browser learns where to go next.
CONFIRMATION_LINK = re.compile(
    re.escape(f"{urls.PREFIX}/confirmations/") + r"([0-9a-fA-F-]{36})"
)


class Browser:
    """One browser against this application, and the things its person does.

    Wraps a ``TestClient``, which owns the cookie jar and therefore *is* the
    identity - there is no actor passed to any method below, because in a browser
    there is no way to pass one. Every method asserts the status its own
    precondition implies, so a test that fails does so at the step that broke
    rather than three lines later with a redirect to the sign-in page.
    """

    def __init__(self, client: TestClient, db_path: str):
        self.client = client
        self.db_path = db_path

    # --- reading ---------------------------------------------------------

    def get(self, path: str):
        return self.client.get(path)

    def post(self, path: str, **kwargs):
        return self.client.post(path, **kwargs)

    @property
    def token(self) -> str | None:
        """The session token this browser is holding, or ``None``."""
        return self.client.cookies.get(SESSION_COOKIE)

    @property
    def user_id(self) -> UUID:
        """Who this browser is, as the JSON API reports it.

        **``GET /users/me`` with the cookie's own value as a bearer token**, and
        the small surprise is the point: the cookie and the header carry one
        credential, so a browser can be asked who it is through the API without
        either side learning something new. The alternative - reading the
        ``sessions`` table - would make this fixture's idea of identity a second
        one, free to disagree with the application's.
        """
        response = self.client.get(
            "/users/me", headers={"Authorization": f"Bearer {self.token}"}
        )
        assert response.status_code == 200, response.text
        return UUID(response.json()["user_id"])

    def wallet_ids(self) -> list[str]:
        """Every wallet link on the landing page, in the order it lists them."""
        page = self.get(urls.LANDING_PATH)
        assert page.status_code == 200, page.text
        return WALLET_LINK.findall(page.text)

    def page(self, path: str) -> str:
        response = self.get(path)
        assert response.status_code == 200, response.text
        return response.text

    # --- becoming somebody ------------------------------------------------

    def sign_up(self, email: str = ALICE, password: str = TEST_USER_PASSWORD):
        """Register an account through the form, which leaves this browser in.

        The route makes both calls - ``POST /users`` and then a login - so a 303
        means the cookie is already in the jar.
        """
        response = self.post(
            urls.SIGN_UP_PATH, data={"email": email, "password": password}
        )
        assert response.status_code == 303, response.text
        assert response.headers["location"] == urls.LANDING_PATH
        return response

    def sign_in(self, identifier: str = ALICE, password: str = TEST_USER_PASSWORD):
        response = self.post(
            urls.SIGN_IN_PATH,
            data={"identifier": identifier, "password": password},
        )
        assert response.status_code == 303, response.text
        return response

    def sign_out(self):
        response = self.post(urls.SIGN_OUT_PATH)
        assert response.status_code == 303, response.text
        return response

    # --- money ------------------------------------------------------------

    def open_wallet(self, currency: str = "NGN", email: str = ALICE) -> str:
        """Become ``email`` if nobody is signed in, open a wallet, return its id.

        The id comes off the landing page after the POST, because a link is how a
        browser learns one - see ``WALLET_LINK``. It is found by *difference*
        rather than by position, so a browser opening its second wallet returns
        the second one: ``wallet_ids()[0]`` would silently hand back the first,
        and a test about two wallets would then be about one.
        """
        if self.token is None:
            self.sign_up(email)

        before = set(self.wallet_ids())
        response = self.post(f"{urls.PREFIX}/wallets", data={"currency": currency})
        assert response.status_code == 303, response.text

        fresh = [one for one in self.wallet_ids() if one not in before]
        assert len(fresh) == 1, f"opening a wallet added {len(fresh)} of them"
        return fresh[0]

    def request_withdrawal(
        self, wallet_id: str, amount: str = "500.00", ref: str | None = None
    ) -> str:
        """Ask for money to leave, and return the confirmation id the 303 names."""
        data = {"amount": amount}
        if ref is not None:
            data["ref"] = ref
        response = self.post(
            f"{urls.PREFIX}/wallets/{wallet_id}/withdrawals", data=data
        )
        assert response.status_code == 303, response.text

        found = CONFIRMATION_LINK.search(response.headers["location"])
        assert found is not None, response.headers["location"]
        return found.group(1)

    def deposit(
        self, wallet_id: str, amount: str = "500.00", ref: str | None = None
    ):
        """Ask to move money in. Returns the 303 to the provider's own page."""
        data = {"amount": amount}
        if ref is not None:
            data["ref"] = ref
        response = self.post(
            f"{urls.PREFIX}/wallets/{wallet_id}/deposits", data=data
        )
        assert response.status_code == 303, response.text
        return response

    def confirm(self, confirmation_id: str):
        """Answer a recorded request. Returns the 303 to the wallet."""
        response = self.post(
            f"{urls.PREFIX}/confirmations/{confirmation_id}/confirm"
        )
        assert response.status_code == 303, response.text
        return response

    def confirmation_page(self, confirmation_id: str) -> str:
        return self.page(f"{urls.PREFIX}/confirmations/{confirmation_id}")

    def wallet_page(self, wallet_id: str) -> str:
        return self.page(f"{urls.PREFIX}/wallets/{wallet_id}")

    # --- money that already exists ---------------------------------------

    def fund(self, wallet_id: str, amount: str = "10000.00") -> None:
        """Credit a wallet **through the use case, because no request can**.

        ``tests/presentation/api/test_money.py``'s ``funded`` fixture, one
        presentation over, and it carries the same argument: the only party who
        can honestly say money arrived is the party that sent it, so there is no
        route - here or in the API - that credits a wallet out of nowhere. The
        web layer can *start* a deposit, which writes a PENDING row and hands the
        payer to the provider; what turns that into a balance is settlement, and
        settlement arrives on ``/webhooks/paystack``.

        So this is a precondition builder rather than a subject: the tests that
        need a funded wallet are about what happens afterwards, and asking them
        each to run a deposit and a signed webhook would make every one of them
        also a test of the payment rail.

        The actor is ``self.user_id`` - read back off this browser's own session
        - so the wallet credited is genuinely the one the browser can see. A
        wallet funded for somebody else would make every assertion below a 404
        and the failure would read as a routing bug.
        """
        build_wallet_service(
            unit_of_work_factory=SqliteUnitOfWorkFactory(self.db_path),
            actor=self.user_id,
        ).deposit(
            UUID(wallet_id),
            Money(Decimal(amount), Currency.NGN),
            str(uuid4()),
        )

    def funded_wallet(self, amount: str = "10000.00", email: str = ALICE) -> str:
        """Open a wallet and put ``amount`` in it. Returns its id."""
        wallet_id = self.open_wallet(email=email)
        self.fund(wallet_id, amount)
        return wallet_id


@pytest.fixture
def db_path(tmp_path):
    """The database every browser in a test shares.

    A file rather than ``:memory:``, for the API suite's reason: each unit of work
    opens its own connection, and an in-memory database lives and dies with the
    connection that made it.
    """
    return str(tmp_path / "web.db")


@pytest.fixture
def app(db_path, password_hasher, build_payment_provider):
    """The application, with a fast hasher and a fake provider.

    The same two substitutions the API suite makes and for the same reasons - see
    ``tests/presentation/api/conftest.py``. Note what is *not* substituted: the web
    routes, the templates, the cookie and its flags, the origin check and the
    error renderer are all the real ones, because they are the subject.
    """
    return create_app(
        unit_of_work_factory=SqliteUnitOfWorkFactory(db_path),
        password_hasher=password_hasher,
        payment_provider=build_payment_provider(),
    )


@pytest.fixture
def browser(app, db_path):
    """The browser a test is about."""
    with TestClient(app, base_url=BASE_URL, follow_redirects=False) as client:
        yield Browser(client, db_path)


@pytest.fixture
def other_browser(app, db_path):
    """A second browser over the same application, with its own cookie jar.

    Two clients rather than two applications, because what the isolation tests ask
    is whether one signed-in person can reach another's money - a question about
    the session and the store, not about the process. httpx gives every client its
    own jar, so these two really are two browsers: signing in on one does nothing
    to the other, which is the precondition every one of those tests needs.
    """
    with TestClient(app, base_url=BASE_URL, follow_redirects=False) as client:
        yield Browser(client, db_path)


# --- time -------------------------------------------------------------------

def freeze_routes_at(monkeypatch, moment: datetime) -> None:
    """Make every web route believe it is ``moment``.

    **The one place this suite moves a clock, and the reason it can.** The web
    routes are the only code here that reads ``datetime.now()`` for itself - the
    services below them are handed the moment by their caller, which is the
    property that makes them testable - so patching the name in ``routes`` is
    patching the boundary rather than reaching past it.

    A subclass rather than a replacement object, so ``isinstance`` still answers
    about a ``datetime`` for anything that happens to ask, and ``now`` is the only
    method overridden. Used for the appointment this feature cannot reach any
    other way: a request recorded fifteen minutes ago, whose window has closed.
    """
    from app.presentation.web import routes

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment

    monkeypatch.setattr(routes, "datetime", Frozen)


def a_while_later(seconds: int = 0, **kwargs) -> datetime:
    """A moment in the future, for ``freeze_routes_at``."""
    return datetime.now() + timedelta(seconds=seconds, **kwargs)
