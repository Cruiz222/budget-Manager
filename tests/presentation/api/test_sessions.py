"""Becoming somebody over HTTP, and ceasing to be them.

Two endpoints, and the tests below are arranged around what makes each of them
unusual rather than around their request shapes. ``POST /sessions`` is the only
endpoint in the API that returns a secret, and it returns it once. ``DELETE
/sessions/current`` is the only one that is authorised by the thing it destroys -
it takes a token where every other endpoint takes an actor, and the reason is that
an expired token cannot be resolved and a client that cannot sign out is stuck.

``test_actor.py`` covers what the *token* means once you have one. This file
covers the two requests that produce one and get rid of it.
"""

from datetime import datetime, timedelta

import pytest

from app.domain.identity.session import SESSION_LIFETIME, hash_session_token
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)
from tests.conftest import TEST_USER_EMAIL, TEST_USER_PASSWORD
from tests.presentation.api.conftest import ALICE, BOB

GOOD_PASSWORD = TEST_USER_PASSWORD


def register(client, email=ALICE, password=GOOD_PASSWORD):
    return client.post("/users", json={"email": email, "password": password})


def sign_in(client, email=ALICE, password=GOOD_PASSWORD):
    return client.post("/sessions", json={"email": email, "password": password})


class TestRegistering:
    """``POST /users`` - the one operation that creates an identity.

    It returns the account and **not** a session, and that separation is the thing
    most of this class is about: "the address was free" and "the password works"
    are different facts, and a client that received both in one response could not
    tell which had just gone wrong.
    """

    def test_it_returns_201_and_the_account(self, client):
        response = register(client)

        assert response.status_code == 201
        body = response.json()
        assert body["email"] == ALICE
        assert body["user_id"]

    def test_it_does_not_sign_anybody_in(self, client):
        """No token in the response, and no way to get one out of it.

        The password is right there in the request and a session could be minted
        from it, which is exactly why the absence is asserted. A registration that
        silently authenticated would mean the first session a client holds came
        from a request that never checked anything.
        """
        body = register(client).json()

        assert "token" not in body
        assert "session" not in body

    def test_the_account_has_no_credential_on_it(self, client):
        """Decision 53 at the response, which is where leaking it would matter most.

        ``translate.user_out`` already had to *deliberately* omit
        ``google_subject``, and the answer to that was to keep the hash off the
        aggregate entirely. A ``User`` that holds no hash cannot leak one, so this
        is a regression test on the shape rather than on the omission.
        """
        body = register(client).json()

        assert "password" not in body
        assert "password_hash" not in body
        assert "google_subject" not in body

    def test_the_address_is_folded(self, client):
        response = register(client, email="  Alice@LocalHost  ")

        assert response.status_code == 201
        assert response.json()["email"] == ALICE

    def test_a_second_account_at_the_same_address_is_a_409(self, client):
        """And it is the one refusal that *does* confirm an account exists.

        Unavoidable: the alternative is letting two people register one address and
        discover it at the login form. See ``DuplicateEmailError``.
        """
        register(client)

        response = register(client)

        assert response.status_code == 409
        assert response.json()["error"] == "DuplicateEmailError"

    def test_a_different_spelling_of_the_same_address_is_also_a_409(self, client):
        """The fold is what makes the ``UNIQUE`` on the column mean what it looks like.

        Without it this would reach the database and come back as an unhandled
        ``IntegrityError`` - a 500 for a mistake the user made.
        """
        register(client, email="alice@localhost")

        response = register(client, email="ALICE@Localhost")

        assert response.status_code == 409

    def test_a_weak_password_is_a_400_naming_the_domain_error(self, client):
        """400 and not 422, which is the whole reason the schema carries no length rule.

        ``SignUpIn`` deliberately has no ``min_length``, so the refusal arrives in
        the domain's vocabulary - the class name a client can act on - rather than
        in pydantic's.
        """
        response = register(client, password="short")

        assert response.status_code == 400
        assert response.json()["error"] == "WeakPasswordError"

    def test_a_malformed_address_is_a_400(self, client):
        response = register(client, email="not-an-address")

        assert response.status_code == 400
        assert response.json()["error"] == "InvalidUserEmailError"

    @pytest.mark.parametrize("payload", [{}, {"email": ALICE}, {"password": GOOD_PASSWORD}])
    def test_a_body_that_is_not_the_right_shape_is_a_422(self, client, payload):
        """The transport's refusal, before the domain sees anything."""
        assert client.post("/users", json=payload).status_code == 422

    def test_a_refused_signup_leaves_no_account(self, client):
        """A weak password must not take the address.

        If it did, the address would be held by an account nobody can log into -
        and the person could neither log in nor try again, which is the invisible
        failure ``SignUp`` writes two rows in one unit to avoid.
        """
        register(client, password="short")

        assert register(client).status_code == 201


class TestSigningIn:
    """``POST /sessions`` - the only endpoint that returns a secret."""

    def test_it_returns_201_and_a_token(self, client):
        register(client)

        response = sign_in(client)

        assert response.status_code == 201
        body = response.json()
        assert body["token"]
        assert body["user"]["email"] == ALICE
        assert body["expires_at"]

    def test_the_expiry_is_a_moment_thirty_days_out(self, client):
        """Sent as an absolute instant, matching ``FundOut`` and ``PlanOut``.

        A client that knows when its token dies can refresh before a request fails.
        The window is asserted against the constant rather than a literal, so a
        changed lifetime does not silently invalidate this.
        """
        register(client)
        before = datetime.now()

        expires_at = datetime.fromisoformat(sign_in(client).json()["expires_at"])

        assert before + SESSION_LIFETIME <= expires_at <= datetime.now() + SESSION_LIFETIME

    def test_the_token_is_not_the_hash_that_was_stored(self, client, db_path):
        """The plaintext goes to the client; only the hash goes to the database.

        Asserted across the boundary - the response on one side, the row on the
        other - because that is the claim: the value the server keeps cannot be
        presented to authenticate.
        """
        register(client)
        token = sign_in(client).json()["token"]

        factory = SqliteUnitOfWorkFactory(db_path)
        uow = factory.start()
        try:
            stored = uow.sessions.find_by_token_hash(hash_session_token(token))
        finally:
            uow.rollback()

        assert stored is not None
        assert stored.token_hash != token

    def test_the_returned_token_authenticates(self, client):
        """The round trip, which is what a token is for.

        Without this the whole file would pass for a login that returned a
        well-shaped string that meant nothing.
        """
        register(client)
        token = sign_in(client).json()["token"]

        response = client.get("/users/me", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200
        assert response.json()["email"] == ALICE

    def test_the_token_is_returned_once_and_cannot_be_read_back(self, client):
        """There is no endpoint that hands a token out for an existing session.

        That absence is what makes a leaked session table useless rather than
        catastrophic - the server holds only a hash, and losing the token means
        logging in again. Asserted as a 404/405 for the obvious candidate paths,
        because "we did not build that" is only true until somebody does.
        """
        register(client)
        token = sign_in(client).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        for method, path in (("get", "/sessions"), ("get", "/sessions/current")):
            response = getattr(client, method)(path, headers=headers)
            assert response.status_code in (404, 405), response.text

    def test_two_logins_are_two_tokens(self, client):
        """A laptop and a phone, neither ending the other."""
        register(client)

        first = sign_in(client).json()["token"]
        second = sign_in(client).json()["token"]

        assert first != second
        for token in (first, second):
            response = client.get(
                "/users/me", headers={"Authorization": f"Bearer {token}"}
            )
            assert response.status_code == 200

    def test_a_wrong_password_is_a_401(self, client):
        register(client)

        response = sign_in(client, password="not-the-password")

        assert response.status_code == 401
        assert response.json()["error"] == "InvalidCredentialsError"

    def test_an_unknown_address_is_a_401(self, client):
        response = sign_in(client, email="nobody@localhost")

        assert response.status_code == 401
        assert response.json()["error"] == "InvalidCredentialsError"

    def test_the_two_are_indistinguishable_over_the_wire(self, client):
        """The oracle, closed at the boundary where it would be exploitable.

        Status, error class and detail must all agree - a caller who can tell "no
        such account" from "wrong password" can walk a list of addresses and learn
        which are registered without guessing a single password. Decision 55, at
        the login form.

        Note the *timing* is not indistinguishable, and that gap is real and
        documented on ``LogIn``: an unknown address returns without hashing
        anything. It belongs to 2c with rate limiting.
        """
        register(client)

        unknown = sign_in(client, email="nobody@localhost")
        wrong = sign_in(client, password="not-the-password")

        assert unknown.status_code == wrong.status_code == 401
        assert unknown.json() == wrong.json()

    def test_a_password_that_could_not_be_one_is_a_400(self, client):
        """Malformed rather than wrong, and it leaks nothing - the length policy is public.

        Collapsing this into the 401 would send somebody to reset a password they
        had typed correctly.
        """
        register(client)

        response = sign_in(client, password="short")

        assert response.status_code == 400
        assert response.json()["error"] == "WeakPasswordError"

    def test_it_is_an_unauthenticated_endpoint(self, client):
        """The way in cannot itself require being in.

        Stated rather than assumed, because the pressure when adding a dependency
        to the identity router is to add it to every route on it.
        """
        register(client)

        # No Authorization header anywhere in this class, and it works.
        assert sign_in(client).status_code == 201

    def test_a_signed_in_token_is_not_confused_with_another_account(self, client):
        """The control. Two accounts, two tokens, each reporting itself."""
        register(client, email=ALICE)
        register(client, email=BOB)

        alice_token = sign_in(client, email=ALICE).json()["token"]
        bob_token = sign_in(client, email=BOB).json()["token"]

        alice = client.get("/users/me", headers={"Authorization": f"Bearer {alice_token}"})
        bob = client.get("/users/me", headers={"Authorization": f"Bearer {bob_token}"})

        assert alice.json()["email"] == ALICE
        assert bob.json()["email"] == BOB
        assert alice.json()["user_id"] != bob.json()["user_id"]


class TestSigningOut:
    """``DELETE /sessions/current`` - authorised by the thing it destroys."""

    def test_it_is_a_204_with_an_empty_body(self, client, as_user):
        response = client.delete("/sessions/current", headers=as_user())

        assert response.status_code == 204
        assert response.content == b""

    def test_the_token_stops_working(self, client, as_user):
        headers = as_user()

        client.delete("/sessions/current", headers=headers)

        assert client.get("/users/me", headers=headers).status_code == 401

    def test_the_session_row_is_gone(self, client, as_user, db_path):
        """Revocation is a deletion, not a flag - decision 49.

        A ``revoked_at`` column would leave every query in the codebase obliged to
        remember to check it, and one that forgot would be a session that never
        ended.
        """
        token = as_user()["Authorization"].removeprefix("Bearer ")
        client.delete("/sessions/current", headers={"Authorization": f"Bearer {token}"})

        factory = SqliteUnitOfWorkFactory(db_path)
        uow = factory.start()
        try:
            stored = uow.sessions.find_by_token_hash(hash_session_token(token))
        finally:
            uow.rollback()

        assert stored is None

    def test_it_ends_only_the_token_that_presented_it(self, client, as_user):
        """Two sessions for one account, and signing out of one leaves the other.

        The failure this catches is a delete whose ``WHERE`` clause lost its
        argument - SQLite accepts it, it removes every session in the database, and
        it would sign out every user of the installation the first time anybody
        logged out.
        """
        token = as_user()["Authorization"].removeprefix("Bearer ")
        other = sign_in(client, email=TEST_USER_EMAIL).json()["token"]

        client.delete("/sessions/current", headers={"Authorization": f"Bearer {token}"})

        assert client.get("/users/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
        assert (
            client.get("/users/me", headers={"Authorization": f"Bearer {other}"}).status_code
            == 200
        )

    def test_signing_out_twice_is_not_an_error(self, client, as_user):
        """204 both times. The postcondition is already true the second time."""
        headers = as_user()

        assert client.delete("/sessions/current", headers=headers).status_code == 204
        assert client.delete("/sessions/current", headers=headers).status_code == 204

    def test_a_token_that_was_never_issued_is_a_204(self, client):
        """The case that makes this endpoint take a token rather than an actor.

        A client whose session is already gone - signed out on another machine,
        expired, or holding a stale file - must be able to discard the token it
        has. A logout built on ``current_actor`` would answer 401 to somebody
        signing out, having failed at the one thing it does.
        """
        response = client.delete(
            "/sessions/current", headers={"Authorization": "Bearer never-issued"}
        )

        assert response.status_code == 204

    def test_an_expired_token_can_still_be_signed_out(self, client, as_user, db_path):
        """The sharpest version of the previous test, and the reason it matters.

        An expired session cannot be *resolved* - that is ``current_actor``'s job -
        so this row is backdated and the same token that ``/users/me`` refuses is
        still accepted here. Built by writing the session's window directly, since
        waiting thirty days is not a test.
        """
        token = as_user()["Authorization"].removeprefix("Bearer ")
        factory = SqliteUnitOfWorkFactory(db_path)
        uow = factory.start()
        try:
            stored = uow.sessions.find_by_token_hash(hash_session_token(token))
            now = datetime.now()
            stored.issued_at = now - timedelta(days=60)
            stored.expires_at = now - timedelta(days=30)
            uow.sessions.save(stored)
            uow.commit()
        finally:
            uow.rollback()

        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/users/me", headers=headers).status_code == 401

        assert client.delete("/sessions/current", headers=headers).status_code == 204

    def test_no_header_at_all_is_a_401(self, client):
        """The one case where sign-out cannot succeed: there is no token to destroy."""
        response = client.delete("/sessions/current")

        assert response.status_code == 401
        assert response.json()["error"] == "MissingCredentialsError"

    def test_it_does_not_delete_the_account(self, client, as_user):
        """Signing out is not deleting yourself.

        The delete goes through the session store keyed on a hash and the two
        tables are one join apart, so this is worth one assertion: the wrong
        repository would present as an account that vanished when somebody signed
        out on a shared machine.
        """
        headers = as_user()

        client.delete("/sessions/current", headers=headers)

        assert sign_in(client, email=TEST_USER_EMAIL).status_code == 201
