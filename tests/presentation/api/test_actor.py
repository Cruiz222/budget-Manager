"""Who the API thinks is asking, and what it takes to become somebody.

``X-User-Email`` used to be the answer, and it was a shim: it named a user and
proved nothing, so a request became whoever it claimed to be. The module that
replaces it is about the opposite property - **the only way to become a user of
this API is to present a token that was issued to one** - and it is written as a
series of refusals rather than as a description, because the refusals are the
feature.

Two tests here are worth more than the rest. ``test_the_header_that_used_to_work``
is the shim's death certificate: it names an address that genuinely exists and is
refused anyway. And ``test_the_two_401s_are_indistinguishable`` is decision 55
applied to tokens, so that failing to authenticate cannot be mined for which
tokens were once real.

What is *not* here any more is the fold. It used to be tested through the header,
because the header was where an address arrived. Addresses now arrive at
registration and at login, and both go through ``find_by_email`` - so the fold is
tested where it is now load-bearing, in ``test_sessions.py`` and in
``tests/application/identity/``. What stays here is the property that survives the
change: the API has no email rule of its own.
"""

from datetime import datetime, timedelta

import pytest

from app.domain.identity.session import Session, hash_session_token
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)
from tests.conftest import TEST_USER_EMAIL, TEST_USER_PASSWORD
from tests.presentation.api.conftest import ALICE


class TestBecomingSomebody:
    """The paths that work, without which every refusal below proves nothing."""

    def test_a_token_names_the_account_it_was_issued_for(self, client, as_user):
        response = client.get("/users/me", headers=as_user(ALICE))

        assert response.status_code == 200
        assert response.json()["email"] == ALICE

    def test_two_accounts_present_two_different_people(self, client, as_user):
        """A token is not just *a* token - it resolves to the account it was for.

        Without this, an API that answered every valid token with the same user
        would pass the test above and every refusal in the file. The pair is what
        makes "who is asking?" a real question rather than a boolean.
        """
        alice = client.get("/users/me", headers=as_user(ALICE)).json()
        bob = client.get("/users/me", headers=as_user("bob@localhost")).json()

        assert alice["user_id"] != bob["user_id"]

    def test_the_actor_is_stable_across_requests(self, client, as_user):
        """One token, many requests, one identity - the ordinary case.

        Cheap, and it is the property every other test in the suite silently
        depends on: ``as_user()`` caches a token and reuses it, so a session that
        resolved once and then stopped would make half the suite fail for a reason
        no failure message would name.
        """
        headers = as_user(ALICE)

        first = client.get("/users/me", headers=headers).json()
        second = client.get("/users/me", headers=headers).json()

        assert first == second


class TestTheShimIsDead:
    """An assertion is not a credential, and these are the tests that say so."""

    def test_the_header_that_used_to_work_achieves_nothing(self, client, as_user):
        """**The phase's thesis, in one request.**

        The address below is deliberately not a stranger's: ``as_user()`` has
        just registered it and signed in as it, so it is a real account with a real
        row in this database. Naming it in the old header returns 401 anyway.

        That is a stronger claim than "an unknown address is refused", and the
        difference is the whole phase. A shim could pass *that* test - it would
        simply fail to find the account. This one fails because an address is not a
        credential and there is no code path left that treats one as though it
        were.
        """
        as_user()

        response = client.get(
            "/users/me", headers={"X-User-Email": TEST_USER_EMAIL}
        )

        assert response.status_code == 401
        assert response.json()["error"] == "MissingCredentialsError"

    def test_a_token_shaped_like_an_address_achieves_nothing(self, client, as_user):
        """The same assertion, wearing the credential's clothes.

        A client that read the old documentation and "migrated" by putting the
        address in the new header gets no further than one that did nothing. Worth
        pinning because it is the one mistake that would otherwise look like it
        worked: the header is well-formed, so nothing in the transport layer
        objects, and only the session store can refuse it.
        """
        as_user()

        response = client.get(
            "/users/me", headers={"Authorization": f"Bearer {TEST_USER_EMAIL}"}
        )

        assert response.status_code == 401
        assert response.json()["error"] == "InvalidSessionError"


class TestWhatTheTransportRefuses:
    """Absent and malformed headers, which are this layer's business."""

    def test_no_header_at_all_is_a_401(self, client):
        """401, and no longer 400.

        It was a 400 while the header was a shim and there was no authentication
        scheme to point at. There is one now, and the status is what says so: the
        request was perfectly well formed, and the caller simply did not
        authenticate.
        """
        response = client.get("/users/me")

        assert response.status_code == 401
        assert response.json()["error"] == "MissingCredentialsError"

    @pytest.mark.parametrize(
        "header",
        [
            "",
            "Bearer",
            "Bearer ",
            "abc123",
            "Token abc123",
            "Basic dXNlcjpwYXNz",
        ],
    )
    def test_a_header_that_is_not_a_bearer_token_is_a_401(self, client, header):
        """Six spellings of "you did not send me a token", one answer.

        The empty one is here rather than in a test of its own because it belongs
        to this family: a blank header is *present*, so it is not the case above,
        but it carries nothing and lands in the same place. The old suite drew that
        distinction because the shim pushed a blank address all the way to the
        aggregate; nothing does that now, and pretending otherwise would test a
        path that no longer exists.

        ``Basic ...`` is here on purpose. It is a real credential of the wrong
        kind, and refusing it as "not a bearer token" is more useful to its sender
        than sending base64 to a lookup that cannot match it.
        """
        response = client.get("/users/me", headers={"Authorization": header})

        assert response.status_code == 401
        assert response.json()["error"] == "MissingCredentialsError"


class TestWhatTheDomainRefuses:
    """A well-formed token that resolves to nobody."""

    def test_a_token_that_was_never_issued_is_a_401(self, client):
        """The header is fine, so this is not the presentation refusing.

        A different ``error`` from the tests above, and deliberately: "you sent
        nothing" and "what you sent is not good enough" have different fixes, and
        the first is a client bug while the second is usually an expired login.
        """
        response = client.get(
            "/users/me", headers={"Authorization": "Bearer not-a-real-token"}
        )

        assert response.status_code == 401
        assert response.json()["error"] == "InvalidSessionError"

    def test_an_expired_session_is_refused(self, client, as_user, db_path):
        """A session genuinely issued sixty days ago, thirty days expired.

        The row is rewritten in the store rather than mocked at the boundary, so
        the whole path is real: the token is the one the API issued, the hash is
        the one ``hash_session_token`` produces, and the only thing that changed is
        when the session was said to have been issued. ``issued_at`` moves with
        ``expires_at`` because ``Session`` refuses a window that closes before it
        opens - a backdated expiry with today's issue date is a state the aggregate
        will not represent, which is the point of that check and not an obstacle to
        this test.
        """
        headers = as_user(ALICE)
        token = headers["Authorization"].removeprefix("Bearer ")

        issued = datetime.now() - timedelta(days=60)
        self._rewrite_session(
            db_path,
            token,
            issued_at=issued,
            expires_at=issued + timedelta(days=30),
        )

        response = client.get("/users/me", headers=headers)

        assert response.status_code == 401
        assert response.json()["error"] == "InvalidSessionError"

    def test_the_two_401s_are_indistinguishable(self, client, as_user, db_path):
        """Decision 55, for tokens.

        "That token expired" and "that token never existed" are the same response,
        byte for byte. A caller who could tell them apart would have an oracle for
        which tokens were once real - the same leak a wallet that distinguished
        "foreign" from "missing" would have been, and the same answer: no branch.

        The status and body are compared as whole objects rather than field by
        field, so this keeps holding if the body ever grows a field.
        """
        headers = as_user(ALICE)
        token = headers["Authorization"].removeprefix("Bearer ")
        issued = datetime.now() - timedelta(days=60)
        self._rewrite_session(
            db_path,
            token,
            issued_at=issued,
            expires_at=issued + timedelta(days=30),
        )

        expired = client.get("/users/me", headers=headers)
        unknown = client.get(
            "/users/me", headers={"Authorization": "Bearer not-a-real-token"}
        )

        assert expired.status_code == unknown.status_code
        assert expired.json() == unknown.json()

    @staticmethod
    def _rewrite_session(db_path, token, *, issued_at, expires_at) -> None:
        """Move an existing session's window, keeping its id and its token.

        Deliberately not a second session inserted alongside the first: reusing
        the row means the token under test is still the one the API handed out, so
        the only variable in the test is the clock. A fresh row would have to
        invent a hash for a token the client was never given, which would make
        these tests about the store rather than about expiry.
        """
        factory = SqliteUnitOfWorkFactory(db_path)
        uow = factory.start()
        try:
            session = uow.sessions.find_by_token_hash(hash_session_token(token))
            assert session is not None, "as_user() did not leave a session behind"
            uow.sessions.save(
                Session(
                    session_id=session.session_id,
                    user_id=session.user_id,
                    token_hash=session.token_hash,
                    issued_at=issued_at,
                    expires_at=expires_at,
                )
            )
            uow.commit()
        finally:
            uow.rollback()


def test_an_address_with_no_at_sign_is_refused_by_the_aggregate(client):
    """The API has no email rule of its own, and this is what says so.

    The address is judged at registration, by ``User.__post_init__``, through the
    same handler as every other domain refusal - which is what keeps a second,
    weaker email rule from being written in the presentation. It used to be
    checked through the header; the header is gone and the property is not, so the
    test moved to the endpoint where addresses now arrive.
    """
    response = client.post(
        "/users", json={"email": "not-an-address", "password": "correct-horse-battery"}
    )

    assert response.status_code == 400
    assert response.json()["error"] == "InvalidUserEmailError"


def test_the_cli_and_the_api_resolve_one_address_to_one_account(
    argon2_client, typed_password, db_path, capsys
):
    """Both presentations reach one account through one store, and this says so.

    Measured by *identity* rather than by behaviour, as it was before: the CLI
    registers an account and signs in as it, and the API then signs in as the same
    address. One id means one account rather than two doors onto two systems
    sharing a database file.

    Note what the CLI does here that it never used to: it proves the password. The
    old version of this test asserted a shared *lookup* - both layers folded an
    address the same way - and the new one asserts a shared *identity*, arrived at
    through two independent logins. If the CLI hashed a password differently from
    the API, or stored a session the API could not find, this is where it would
    show.

    **``argon2_client`` and not the suite's ``client``, and the first version of
    this test got that wrong.** The CLI builds its own composition root, so it
    hashes with real argon2; the ordinary ``client`` fixture verifies with
    ``FakePasswordHasher``. Nothing is wrong with either - but this test crosses
    the seam between them, and a fake on one side answers "those details did not
    match" about a password that was typed correctly. The failure was in the
    fixture, not the code, and it presented as the one thing this test exists to
    rule out.

    The password is answered at the prompt rather than passed as an argument,
    because passing it is exactly what the CLI refuses to allow - see
    ``_prompt_password``. ``signup`` asks twice and the fixture answers both, so
    the confirmation path is the real one.
    """
    from app.presentation.cli import main

    address = "shared@localhost"
    session = f"{db_path}.session"
    typed_password()

    assert main(["--db", db_path, "signup", address]) == 0
    assert main(["--db", db_path, "--session", session, "login", address]) == 0
    capsys.readouterr()
    assert main(["--db", db_path, "--session", session, "whoami"]) == 0
    cli_id = capsys.readouterr().out.split("user_id:")[1].strip()

    # The API signs in as the same address, with the same password. The account it
    # lands on must be the one the CLI registered - which is only true if the hash
    # the CLI wrote is one the API can verify, and if both are reading one store.
    response = argon2_client.post(
        "/sessions", json={"email": address, "password": TEST_USER_PASSWORD}
    )

    assert response.status_code == 201, response.text
    assert response.json()["user"]["user_id"] == cli_id
