"""``GET /health`` - the one endpoint with no actor.

Its tests are short because its job is short. What they are checking is mostly
what it *doesn't* do: it does not need to be told who is asking, and it does not
say anything about what the installation holds.
"""

import sqlite3

from fastapi.testclient import TestClient

from app.presentation.api.app import create_app


class ExplodingFactory:
    """A storage layer that cannot be reached, for the 503 path.

    A stub rather than a mock of ``SqliteUnitOfWorkFactory``: the thing being
    tested is what ``health`` does when ``start`` raises, and a real database that
    happens to be locked would be a fragile way to arrange that. This raises the
    error SQLite raises for an unopenable file, which is what a missing volume or
    a bad path actually produces.
    """

    def start(self):
        raise sqlite3.OperationalError("unable to open database file")


def test_it_answers_on_an_empty_database(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_it_needs_no_actor(client, as_user):
    """The whole reason this endpoint exists in the shape it does.

    A probe that had to present a token could not run before there is an account
    to issue one, which is exactly when somebody wants to ask whether the service
    is up. The second assertion is the one that would catch a stray ``Depends``
    being added later: a probe that happens to work with credentials is not a probe
    that works without them.
    """
    without = client.get("/health")
    with_actor = client.get("/health", headers=as_user())

    assert without.status_code == 200
    assert with_actor.status_code == 200
    assert without.json() == with_actor.json()


def test_it_says_nothing_about_what_the_installation_holds(
    client, as_user, open_wallet
):
    """A health check is reachable by anything that can open a socket.

    Including, in most deployments, things that cannot reach the rest of the API.
    "How many wallets does this installation hold?" is not a question a probe
    needs answered, so the answer is the same body whether the database has one
    wallet in it or none.
    """
    empty = client.get("/health").json()

    open_wallet()

    assert client.get("/health").json() == empty


def test_it_reports_unavailable_when_storage_cannot_be_reached():
    """A 503 rather than a 500, and the difference is what a caller does with it.

    A 500 says "this request broke and retrying will not help"; a 503 says "this
    service is not in a state to serve", which is what a load balancer needs in
    order to stop sending traffic. Reporting a broken database as a 500 would
    take a healthy process out of rotation for a fault that is somewhere else.
    """
    unreachable = create_app(unit_of_work_factory=ExplodingFactory())
    with TestClient(unreachable) as probe:
        response = probe.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


def test_the_unreachable_case_does_not_leak_why(client):
    """The 503 body is a status, not a diagnosis.

    ``sqlite3.OperationalError("unable to open database file")`` names a real
    fact about a real file, and echoing it to an unauthenticated endpoint would
    be handing out a description of the server's filesystem. So the body is the
    constant above and the reason goes to the log.
    """
    unreachable = create_app(unit_of_work_factory=ExplodingFactory())
    with TestClient(unreachable) as probe:
        body = probe.get("/health").text

    assert "unable to open" not in body
    assert "sqlite" not in body.lower()
