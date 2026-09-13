"""Fixtures for the HTTP tests.

Same shape as ``tests/presentation/test_cli.py``: a real database file under
``tmp_path``, real services, no fakes of anything under test. The only thing that
is not real is the transport - starlette's ``TestClient`` drives the application
in-process, so no port is opened and nothing is left running - but every request
still goes through routing, dependency resolution, the exception handlers and
pydantic's parsing exactly as it would over a socket. A handler that is wrong is
wrong here too.

The helpers below are fixtures returning callables, which is the same house
pattern as ``build_wallet`` and ``build_plan``: a test module asks for what it
needs by name rather than importing a helper from a sibling, so every test file
reads on its own.
"""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)
from app.infrastructure.security.argon2_password_hasher import Argon2PasswordHasher
from app.presentation.api.app import create_app
from tests.conftest import TEST_USER_EMAIL, TEST_USER_PASSWORD

#: Two addresses that could never be real accounts, for the same reason the CLI's
#: default is ``test@example.com``: nobody can mistake one of these for a person.
#:
#: The domain is ``example.com`` rather than ``localhost`` because the standard
#: account has to be able to *deposit*, and no provider bills an address with no
#: real domain. ``TEST_USER_EMAIL`` carries the live evidence for that.
ALICE = "alice@example.com"
BOB = "bob@example.com"


@pytest.fixture
def db_path(tmp_path):
    """The database every client in a test shares.

    A file rather than ``:memory:``, and this one matters. Each unit of work
    opens its *own* connection, and an in-memory SQLite database lives and dies
    with the connection that made it - so every unit would see an empty database
    and nothing would ever be read back. The CLI tests use a file for the same
    reason.
    """
    return str(tmp_path / "api.db")


@pytest.fixture
def app(db_path, password_hasher, build_payment_provider):
    """The application, with a fast hasher in place of argon2 and a fake provider.

    The one place in this suite where a real component is replaced, and the
    replacement is justified by cost rather than by convenience. Argon2 is
    *designed* to take tens of milliseconds and megabytes per call, which a login
    rightly pays once and this suite would pay on every ``as_user()`` in every one
    of its tests - roughly two hundred sign-ups and logins, or some twenty seconds
    of wall clock proving that arithmetic works. ``FakePasswordHasher`` keeps every
    property the code under test can observe and drops only the price; the price
    itself is tested where it belongs, against the real adapter.

    Note what is *not* replaced: the routes, the dependencies, the error handlers,
    the services, the repositories and the database are all the real ones, so a
    test that passes here is a test that would pass against a server running
    argon2 - which is exactly what ``test_boundary.py`` and ``test_isolation.py``
    are relied upon to be true of.

    **The payment provider is injected for the same class of reason, and it is
    the newer half of this docstring.** ``create_app`` resolves a provider from
    the environment when it is not given one, and this suite runs with
    ``PAYSTACK_SECRET_KEY`` cleared - so the default here would be an install with
    no payments, and every test that touches a deposit would be exercising a 503.
    So the fake is passed, which makes the ordinary test app a *configured* one.

    That default is a decision worth stating, because the opposite is defensible:
    "no key" is the ordinary state of a fresh install. It loses on the grounds
    that the suite's job is to test the application rather than its configuration
    - the unconfigured state is one behaviour of one dependency, it is asserted
    directly, and ``unconfigured_client`` below exists so that it is asserted
    against a real application rather than described in a comment.
    """
    return create_app(
        unit_of_work_factory=SqliteUnitOfWorkFactory(db_path),
        password_hasher=password_hasher,
        payment_provider=build_payment_provider(),
    )


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client


@pytest.fixture
def payment_provider(client):
    """The fake provider this application is wired to, so a test can read it.

    Read off ``app.state`` rather than kept in a fixture of its own, and that is
    not a shortcut - it is the assertion that the seam works. A provider the test
    held separately could differ from the one the application is using, and the
    failure that produced would be a test asserting about an object the server
    had never seen.

    What a test wants it for is ``.requests``: the calls this application made,
    in order, with the arguments they carried. That is how "the payer's email was
    sent and nothing else about them" and "the reference the provider was given is
    the one the ledger holds" become assertable at all.
    """
    return client.app.state.payment_provider


@pytest.fixture
def unconfigured_app(db_path, password_hasher):
    """An application with no payment provider at all.

    The genuinely unconfigured installation: no provider injected, and
    ``PAYSTACK_SECRET_KEY`` cleared by the autouse fixture in ``tests/conftest.py``
    - so ``create_app`` resolves one from the environment, finds nothing, and
    leaves ``app.state.payment_provider`` as ``None``. That is the state a fresh
    deployment is in before anybody sets a key, and the state the 503 tests exist
    for.

    Separate from ``app`` rather than a parameter on it, because the two are
    different *installations* rather than two configurations of one test client -
    and because a test that reached for a dial here could set it without noticing
    that every other test in the file depends on the default.
    """
    return create_app(
        unit_of_work_factory=SqliteUnitOfWorkFactory(db_path),
        password_hasher=password_hasher,
    )


@pytest.fixture
def unconfigured_client(unconfigured_app):
    with TestClient(unconfigured_app) as client:
        yield client


@pytest.fixture
def argon2_client(db_path):
    """A client that hashes with real argon2, for the one test that crosses the seam.

    Every other client in this suite runs on ``FakePasswordHasher`` - see ``app``
    above for why - and this fixture exists because exactly one test has to *straddle*
    the seam rather than sit on one side of it.
    ``test_actor.py::test_the_cli_and_the_api_resolve_one_address_to_one_account``
    registers through the CLI, which builds its own composition root and therefore
    hashes with real argon2, and then signs in over HTTP. With the fake on the API
    side the two would disagree about a password neither of them got wrong, and the
    test would be measuring the fixture instead of the claim.

    So this is not "the fake, but slower" - it is the only client here whose hasher
    matches the CLI's, and that match is the property under test. It costs one hash
    and one verify, which is the price of the claim.

    Two clients over one ``db_path`` rather than one shared app: ``TestClient`` is
    what drives requests, so a second application object is a second set of routes
    over the same store - which is the real deployment shape, where the CLI and the
    server are separate processes reading one file.
    """
    with TestClient(
        create_app(
            unit_of_work_factory=SqliteUnitOfWorkFactory(db_path),
            password_hasher=Argon2PasswordHasher(),
        )
    ) as client:
        yield client


def _sign_in(client, email: str) -> str:
    """Register ``email``, sign in as it, and return the token.

    Both steps go over the wire rather than through the use cases, and that is
    deliberate for a suite whose subject is the wire: it means every one of the
    tests below exercises the sign-up endpoint, the login endpoint, the session
    store and the bearer-token dependency as a side effect of asking for headers.
    A test that fails because login broke fails here, with a message that says so,
    rather than three lines later with a mysterious 401.

    The password is the shared constant, so the account this creates is one the
    test can sign into again - which the tests about *login* do.
    """
    registered = client.post(
        "/users", json={"email": email, "password": TEST_USER_PASSWORD}
    )
    assert registered.status_code == 201, registered.text

    response = client.post(
        "/sessions", json={"email": email, "password": TEST_USER_PASSWORD}
    )
    assert response.status_code == 201, response.text
    return response.json()["token"]


@pytest.fixture
def as_user(client):
    """Authorization headers for an account, created and signed in on first use.

        as_user()            # the suite's standard owner
        as_user(BOB)         # somebody else

    **The signature did not change when the shim died, and that was the goal.** It
    used to return ``{"X-User-Email": email}``; it now returns a bearer token, and
    roughly two hundred call sites across nine files were left alone. The reason it
    could be is that the old fixture was already the right *shape* - a function
    from an address to headers - and only its contents were a lie.

    What changed is everything the contents mean. ``as_user(BOB)`` used to assert
    that the caller was Bob; it now has to *prove* it, by registering Bob and
    logging in as him. Both steps are real requests against the real endpoints, and
    the token is a row in the sessions table that ``current_actor`` looks up.

    Lazily, and cached per address: an address is registered the first time it is
    named and its token is reused afterwards, so a test that calls ``as_user()``
    eleven times does one sign-up and one login. Cache the *token* and not the
    headers, so each call gets its own dict - a test that mutated one it was
    handed could not corrupt the next.

    Two things deliberately do not happen here. The address is not folded before
    being used as a key, because folding is the application's rule and a fixture
    that did it would hide the one place it can be got wrong; and nothing is
    cached across tests, because the ``client`` it depends on is rebuilt with its
    database for each one.

    The header is spelled out rather than imported from ``dependencies``. A
    fixture that built its headers from the server's own constant would pass every
    test even if that constant were wrong, because both sides would be wrong
    together - and a client, which is what this is standing in for, knows the
    scheme from the protocol rather than from the implementation.
    """
    tokens: dict[str, str] = {}

    def _headers(email: str = TEST_USER_EMAIL) -> dict:
        if email not in tokens:
            tokens[email] = _sign_in(client, email)
        return {"Authorization": f"Bearer {tokens[email]}"}

    return _headers


@pytest.fixture
def open_wallet(client, as_user):
    """Open a wallet and return its id.

    Asserts its own success, so a test that is really about plans does not fail
    with "KeyError: wallet_id" three lines later when the thing that actually
    broke was the wallet.
    """

    def _open(headers=None, currency="NGN") -> str:
        response = client.post(
            "/wallets",
            json={"currency": currency},
            headers=as_user() if headers is None else headers,
        )
        assert response.status_code == 201, response.text
        return response.json()["wallet_id"]

    return _open


@pytest.fixture
def balance_of(client):
    """A wallet's available balance, as the API reports it - ``"5000.00"``.

    Read back through ``GET /wallets/{id}`` rather than through a repository, so
    that the number a test asserts is the number a *client* would see. A
    settlement that credited correctly and a read that reported it wrongly would
    be one bug, and this is the seam where that shows up.

    A string rather than a number, because that is what the API returns - see
    ``MoneyOut`` - and a fixture that parsed it into a ``Decimal`` would be
    quietly undoing the serialization decision every test that used it was
    supposed to be checking.
    """

    def _balance(wallet_id, headers):
        response = client.get(f"/wallets/{wallet_id}", headers=headers)
        assert response.status_code == 200, response.text
        return response.json()["available_balance"]["amount"]

    return _balance


@pytest.fixture
def open_pot(client, as_user):
    """Open a pot on a wallet and return its name."""

    def _open(wallet_id, headers=None, name="Vacation", kind="personal", **extra):
        payload = {"name": name, "kind": kind, **extra}
        response = client.post(
            f"/wallets/{wallet_id}/funds",
            json=payload,
            headers=as_user() if headers is None else headers,
        )
        assert response.status_code == 201, response.text
        return response.json()["name"]

    return _open


@pytest.fixture
def payout_line():
    """Build one valid payout instruction payload.

    A fixture rather than a dict written out in each test, and the reason is a
    mistake worth recording: a ``bank_account`` destination requires a
    ``bank_code`` detail (``Destination._REQUIRED_DETAILS``), so a hand-written
    destination that omits ``details`` is refused with a 400 by the domain rather
    than by anything about the test's actual subject. Three tests failed that way
    at once, each for a reason that looked unrelated to the others.

    So the rail's requirements live in one place, and a test that is about
    something else - a total, an edit, a currency - says ``payout_line("500.00")``
    and does not have to know them.
    """

    def _line(amount="2500.00", label="March rent", **extra):
        payload = {
            "action": "payout",
            "amount": amount,
            "label": label,
            "destination": {
                "kind": "bank_account",
                "identifier": "0123456789",
                "name": "Chinedu Okafor",
                "details": {"bank_code": "058"},
            },
        }
        payload.update(extra)
        return payload

    return _line


@pytest.fixture
def create_plan(client, as_user, payout_line):
    """Create a plan and return its response body.

    Defaults describe the product's headline case in the shape this API takes it:
    a monthly payout from the available balance, starting 2 March 2026. Every
    dial a test could turn is a keyword argument, and ``**extra`` lets a test
    override or add any request field without this helper having to predict it.
    """

    def _create(wallet_id, headers=None, **extra):
        payload = {
            "wallet_id": wallet_id,
            "name": "Rent",
            "source": "available",
            "schedule": {"cadence": "monthly", "anchor": "2026-03-02T12:00:00"},
            "instructions": [payout_line()],
        }
        payload.update(extra)
        return client.post(
            "/plans",
            json=payload,
            headers=as_user() if headers is None else headers,
        )

    return _create


@pytest.fixture
def unknown_id():
    """A UUID that is certainly not in the database."""
    return str(uuid4())
