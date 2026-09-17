"""Becoming somebody over HTTP, and ceasing to be them.

Two endpoints, and the tests below are arranged around what makes each of them
unusual rather than around their request shapes. ``POST /sessions`` is the only
endpoint in the API that returns a secret, and it returns it once. ``DELETE
/sessions/current`` is the only one that is authorised by the thing it destroys -
it takes a token where every other endpoint takes an actor, and the reason is that
an expired token cannot be resolved and a client that cannot sign out is stuck.

**``POST /sessions`` now takes two identifiers, and the second one is here rather
than in a file of its own.** A number signs an account in exactly as an address
does, so it belongs beside the address tests - what is *different* about it is
which strings are the same account, and the class below is where that shows.

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

#: A number as it is typed, and the spelling the store holds. The trunk ``0`` is
#: a national convention rather than part of the number, so these are not the same
#: string and the fold is what makes them one account - see ``fold_phone``.
TYPED = "08012345678"
NUMBER = "2348012345678"

#: A number that belongs to nobody.
OTHER_TYPED = "08098765432"


def register(client, email=ALICE, password=GOOD_PASSWORD):
    return client.post("/users", json={"email": email, "password": password})


def sign_in(client, email=ALICE, password=GOOD_PASSWORD):
    return client.post("/sessions", json={"email": email, "password": password})


def sign_in_by_phone(client, phone=TYPED, password=GOOD_PASSWORD):
    """``sign_in``'s sibling, and a separate function for the same reason it is a
    separate fixture below: what differs is the request, not a value in it."""
    return client.post("/sessions", json={"phone": phone, "password": password})


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
        """Whitespace and case both go, and the expectation is written from the input.

        **The expected value is spelled out rather than written as ``ALICE``**, and
        that is a correction rather than a style choice. It read ``== ALICE`` until
        that constant moved off ``@localhost`` (decision 160) and this test failed
        for a reason that had nothing to do with folding: it was asserting that the
        fold of ``"  Alice@LocalHost  "`` equals whatever ``ALICE`` happens to be,
        which is true only for as long as the two agree. A test about a
        transformation has to state the transformation's result; a constant that
        stands in for it is that result only by coincidence, and the coincidence
        ends the next time somebody edits a fixture.

        It is the same class of coupling decision 160 is about, one layer down: the
        suite agreeing with something other than the thing under test.

        **The address it folds moved to ``example.com`` when the entry rule
        landed, and that is worth a sentence rather than being left to look like
        tidying.** A test about folding has to *register* the thing it folds, so
        it is bound by whatever registration accepts - and registration no longer
        accepts an address with no domain in it. Left at ``@localhost`` this test
        would fail at the 400 and report itself as a folding bug, which is the
        same misdirection the paragraph above is about: the fixture, not the
        subject. The fold is unchanged by this; only the address it is measured
        on had to become one the system would mint.
        """
        response = register(client, email="  Alice@Example.COM  ")

        assert response.status_code == 201
        assert response.json()["email"] == "alice@example.com"

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
        register(client, email="alice@example.com")

        response = register(client, email="ALICE@Example.COM")

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
        """``nobody@localhost`` stays, and it is the sign-in half of the entry rule.

        Every address that gets *registered* in this suite moved to a real domain
        when the entry rule landed, because registration mints an address and the
        rule governs minting. This one is not registered - it is an address with
        nothing behind it, offered to the login form - and it stays exactly as it
        was, which is the assertion: **the rule is an entry rule, not a lookup
        rule.** Had it been folded into ``find_by_email``, this test would have
        started answering 400 where it now answers 401, and that difference is the
        whole of the design.
        """
        response = sign_in(client, email="nobody@localhost")

        assert response.status_code == 401
        assert response.json()["error"] == "InvalidCredentialsError"

    def test_the_two_are_indistinguishable_over_the_wire(self, client):
        """The oracle, closed at the boundary where it would be exploitable.

        Status, error class and detail must all agree - a caller who can tell "no
        such account" from "wrong password" can walk a list of addresses and learn
        which are registered without guessing a single password. Decision 55, at
        the login form.

        Note the *timing* is not part of this assertion, and the sentence that
        used to sit here - "an unknown address returns without hashing anything,
        and that gap belongs to 2c with rate limiting" - is no longer true. Both
        paths now hash: ``LogIn._settle`` verifies against ``DUMMY_HASH`` when no
        credential was found, so the two answers cost the same order of time
        instead of two orders apart. That was fixed in the step that made it worth
        measuring, because a number is a small enumerable space - see ``LogIn``'s
        class docstring, which also records the bound: the *store* lookup still
        differs by microseconds, so rate limiting is still owed and this is not a
        claim that the oracle is closed. ``tests/application/identity/test_log_in.py``
        asserts the comparison itself, which is the half a stopwatch cannot state
        reliably.
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


class TestSigningInWithANumber:
    """``POST /sessions`` with ``phone`` - the second identifier, over the wire.

    The class above signs in with an address, and this one is not a parametrised
    version of it: the *request* differs, and three of the things asserted here
    have no analogue there. The first is that a number reaches an account at all,
    which is the half a stopwatch cannot see. The second is that the response can
    now be *about* such an account - ``UserOut.email`` is ``None`` and is not a
    placeholder, an omission, or an empty string. The third is the cross-identifier
    refusal, which is a case that does not exist when there is one identifier and
    which, if it were answered distinctly, would announce that a value is half
    registered.

    ``legacy_account`` seeds rather than ``POST /phone-verifications`` creating,
    for the reason that fixture states - an SMS channel and a texted code are not
    what any test here is about, and the signup is exercised end to end in
    ``test_phone_verifications.py``.
    """

    def test_it_returns_201_and_a_token(self, client, legacy_account):
        legacy_account(None, phone=TYPED)

        response = sign_in_by_phone(client)

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["token"]
        assert body["expires_at"]

    def test_the_account_it_reports_holds_a_number_and_no_address(
        self, client, legacy_account
    ):
        """``email`` is ``None``, and that is the honest rendering rather than a gap.

        ``UserOut`` carried a required ``email`` until the aggregate allowed the
        absence, and this is the response that makes the difference observable: a
        phone-only account is the ordinary case the model's docstring describes,
        not a malformed one. A placeholder - ``""``, or the number echoed into the
        field - would be a lie in the identity column, which is the argument that
        refused a synthetic sentinel address at the schema level.
        """
        legacy_account(None, phone=TYPED)

        body = sign_in_by_phone(client).json()

        assert body["user"]["phone"] == NUMBER
        assert body["user"]["email"] is None

    @pytest.mark.parametrize("spelling", [TYPED, NUMBER, "+2348012345678", "0801 234 5678"])
    def test_any_spelling_of_the_number_finds_the_account(
        self, client, legacy_account, spelling
    ):
        """The fold, at the boundary, which is where a person meets it.

        The stored number is the typed spelling folded by ``User``; every one of
        these folds to the same value, so all four are one account. Without the
        fold the ``UNIQUE`` on ``users.phone`` would be decorative - one person,
        four accounts - and this is the test that would notice, because the
        fixture writes the typed spelling and these are not all equal to it.
        """
        legacy_account(None, phone=TYPED)

        assert sign_in_by_phone(client, phone=spelling).status_code == 201

    def test_the_returned_token_authenticates(self, client, legacy_account):
        """The round trip, and it is not redundant with the address version.

        ``/users/me`` resolves an actor out of the session, so a token issued for
        an account with no address travels a path the address tests never take -
        and it is the path every other endpoint in the API will take once a
        phone-only account holds one.
        """
        legacy_account(None, phone=TYPED)

        token = sign_in_by_phone(client).json()["token"]
        response = client.get("/users/me", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200
        assert response.json()["phone"] == NUMBER
        assert response.json()["email"] is None

    def test_an_unknown_number_is_a_401(self, client):
        response = sign_in_by_phone(client, phone=OTHER_TYPED)

        assert response.status_code == 401
        assert response.json()["error"] == "InvalidCredentialsError"

    def test_a_wrong_password_against_a_number_is_a_401(self, client, legacy_account):
        legacy_account(None, phone=TYPED)

        response = sign_in_by_phone(client, password="not-the-password")

        assert response.status_code == 401
        assert response.json()["error"] == "InvalidCredentialsError"

    def test_an_unknown_number_and_a_wrong_password_are_the_same_answer(
        self, client, legacy_account
    ):
        """Decision 55 one identifier over, and the identifier where it matters most.

        A number is a small, structured, enumerable space - ten digits behind a
        known prefix - so an answer that separated "no such account" from "wrong
        password" would let anybody walk a number range and learn which handsets
        hold accounts, without ever guessing a password. Asserted by asking for
        both and comparing the *whole* body, as ``test_boundary.py`` does for
        wallets: a status and an error class that agree while a detail differs is
        the leak, not the protection.
        """
        legacy_account(None, phone=TYPED)

        unknown = sign_in_by_phone(client, phone=OTHER_TYPED)
        wrong = sign_in_by_phone(client, password="not-the-password")

        assert unknown.status_code == wrong.status_code == 401
        assert unknown.json() == wrong.json()

    def test_the_refusal_does_not_echo_the_number(self, client, legacy_account):
        """The string the caller typed must not come back, in either spelling.

        A refusal that quotes what it was given is a refusal a client can render -
        and it is also the shape a *helpful* error message takes the moment
        somebody adds "no account with the number ...". Asserted on the body text
        rather than on a field, so a message assembled from the input anywhere in
        the response is caught.
        """
        legacy_account(None, phone=TYPED)

        body = sign_in_by_phone(client, password="not-the-password").text

        assert TYPED not in body
        assert NUMBER not in body

    def test_a_number_for_an_address_only_account_is_a_401(self, client):
        """The cross-identifier case, which is a case and not a fifth refusal.

        The account exists and is identified by an address. Presenting a number
        must answer exactly as an unknown number does, because anything else
        reports that this account is *half* registered - a fact about the system
        rather than about the guess, and one that would let a caller determine
        which accounts lack a number without holding one.
        """
        register(client, email=ALICE)

        unknown = sign_in_by_phone(client, phone=OTHER_TYPED)
        cross = sign_in_by_phone(client, phone=TYPED)

        assert cross.status_code == 401
        assert cross.json() == unknown.json()

    def test_an_address_for_a_number_only_account_is_a_401(self, client, legacy_account):
        """And the same in the other direction, which is the one that looks harmless.

        "This account has no address" is the message a developer would write here
        without thinking, and it is the one that turns this endpoint into a way of
        asking whether a number is registered.
        """
        legacy_account(None, phone=TYPED)

        response = sign_in(client, email=ALICE)

        assert response.status_code == 401
        assert response.json()["error"] == "InvalidCredentialsError"

    def test_a_malformed_number_is_refused_as_unknown_rather_than_as_malformed(
        self, client
    ):
        """No shape rule on the lookup path, per ``LogIn.execute_for_phone``.

        ``checked_phone`` belongs to ``User``, and every account that exists passed
        through it - so a value that is not a number names no account and is
        refused as one. Restating the rule here would answer a mistyped number
        differently from an unknown one, which is a smaller version of the oracle
        the shared refusal exists to close.
        """
        response = sign_in_by_phone(client, phone="not-a-number")

        assert response.status_code == 401
        assert response.json()["error"] == "InvalidCredentialsError"

    @pytest.mark.parametrize(
        "payload",
        [
            {"email": ALICE, "phone": TYPED, "password": GOOD_PASSWORD},
            {"password": GOOD_PASSWORD},
        ],
    )
    def test_a_body_that_does_not_name_exactly_one_identifier_is_a_422(
        self, client, payload
    ):
        """Both directions, because they are one refusal: the body names *one*.

        This travels further than the shape checks around it. ``LogInIn``'s
        validator is what makes the route's branch total - without it, a body
        naming a number would be caught by whichever branch happened to be written
        first, and a body naming neither would reach the use case and answer 401
        for a request that never named anything. So the refusal is pydantic's 422
        rather than a 401, and the message is asserted rather than only the status,
        because a bare 422 is also what a typo in the field name produces - and
        those are different facts about the request.
        """
        response = client.post("/sessions", json=payload)

        assert response.status_code == 422, response.text
        assert "give email or phone, not both" in response.text

    def test_a_number_signs_in_an_account_that_also_has_an_address(
        self, client, legacy_account
    ):
        """An account may hold both, and then either identifier is a way in.

        This is the state the entry rule and the phone signup can both produce, and
        the one thing worth pinning about it is that the *other* identifier keeps
        working - the account is not "switched" to a number by the existence of
        one, which is the reading a per-identifier lookup table with a single
        active row would give.
        """
        legacy_account(ALICE, phone=TYPED)

        assert sign_in(client, email=ALICE).status_code == 201
        assert sign_in_by_phone(client).status_code == 201


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
