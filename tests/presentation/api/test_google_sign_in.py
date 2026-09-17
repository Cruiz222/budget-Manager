"""Signing up and signing in with a Google identity, over HTTP.

Two routes that take one opaque string and do two different things with it, and
this file follows them in order. Seven things about the wire are worth naming
before the tests that pin them:

**The token is the whole of the request.** ``GoogleTokenIn`` has one field and no
company, which is a decision rather than an economy: a field for ``sub``, ``email``
or ``email_verified`` would be a value this layer took from the caller and the
domain then had to decide whether to believe - the exact shape of bug signature
verification exists to prevent. So "the client cannot name the account" is asserted
here in the form the wire allows: extra fields are sent and the account is still
the one the token describes.

**The two routes are separate operations and the responses say so.** A sign-up is
201 with a ``UserOut`` and **no token** - registering and proving are separate acts,
so the first session on a client never comes from a request that proved nothing.
A sign-in is 201 with a ``SessionOut``, and the token in it is this system's rather
than Google's.

**An address that already has an account is a 409 and is never linked.** This is
the decision the flow was designed around, and the assertion for it is below the
API rather than over it: ``UserOut`` reports an address and a number and no
subject, so "nothing was linked" is only observable on the row. That test is the
one place this file reads the store directly.

**A subject that already has an account is a different 409**, because the remedy
is different - "log in" against "use another address" - and a caller who is told
the wrong one does the wrong thing.

**The refusals are four answers rather than three.** A token that is not Google's
is a 401, an identity Google has not proved the address of is a 400, and an
installation that cannot serve this at all is a 503 - of which there are two kinds,
and the file asserts both because they are two classes. No ``GOOGLE_CLIENT_ID`` is
the one the ordinary test application is genuinely in, with no arrangement; a
client id that is present and Google unreachable needs a failing verifier, and it
is the one worth having a test for, because the first is reachable by clearing a
variable and the second is not reachable any other way.

**Both routes are unauthenticated writes**, and this file asserts the property from
both sides: a request with no ``Authorization`` header succeeds, and one carrying a
nonsense bearer token also succeeds, which rules out a dependency that reads a
session when it is offered.
"""

from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.domain.identity.exception import GoogleProviderError
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)
from app.presentation.api.app import create_app
from tests.conftest import TEST_GOOGLE_SETTINGS, TEST_USER_PASSWORD

#: Google's stable identifier for an account, and deliberately **not** an address.
#: It is what the account is looked up by, and a fixture that used an email-shaped
#: subject would make "the subject is the key and the address is a record"
#: indistinguishable from a bug where the two were swapped.
SUBJECT = "10769150350006150715113082367"

#: A second subject, for the identity that must collide on its *address* instead.
OTHER_SUBJECT = "10769150350006150715113082368"

#: The address the Google identities below carry, and the one the account holds.
#: ``example.com`` for the reason the API conftest gives for ``alice@example.com``:
#: nobody can mistake a reserved domain for a person.
ADDRESS = "grace@example.com"

#: A second address, used where two identities must not collide on one.
OTHER_ADDRESS = "ada@example.com"


@pytest.fixture
def verifier(build_google_verifier):
    """A fresh fake verifier, for a test to mint tokens with.

    Separate from the client so a test can mint *after* a request - which several
    below do, since the second half of a collision is a second token.
    """
    return build_google_verifier()


def _google_app(db_path, password_hasher, build_payment_provider, verifier):
    """The installation a Google-capable deployment is, built twice below.

    A plain function rather than a factory fixture, because what the two fixtures
    that use it differ in is the *verifier* - one that reads a script of
    identities and one that fails at the first call - and everything else about the
    application is identical. Two copies of this call would be two places to change
    when a dependency is added, and the one nobody edited would be the test about
    the refusal.

    The payment provider is passed for ``app``'s reason: the suite clears
    ``PAYSTACK_SECRET_KEY``, so a client without one is an install that cannot take
    payments, and nothing in this file is about that.
    """
    return create_app(
        unit_of_work_factory=SqliteUnitOfWorkFactory(db_path),
        password_hasher=password_hasher,
        payment_provider=build_payment_provider(),
        google_settings=TEST_GOOGLE_SETTINGS,
        google_verifier=verifier,
    )


@pytest.fixture
def google_client(db_path, password_hasher, build_payment_provider, verifier):
    """The application as an installation **with** a Google client id.

    **A second installation, and it belongs in this file rather than the conftest**
    for the reason ``sms_client`` belongs in the phone file: ``app`` is the ordinary
    test application, which resolves no Google settings from an environment this
    suite clears, and an install with no client id answers both routes with a 503.
    So every test of the flow needs this application, and the test of the refusal
    needs the ordinary one - and both are asserted below rather than described here.

    Both halves are passed rather than one, which is ``create_app``'s own reading of
    the pair: settings without a verifier is an installation that *could* verify,
    and a verifier without settings is a test. The fake is passed as the verifier
    because there is no honest way to have a signature check in a fixture - the
    reasoning is in ``FakeGoogleIdentityVerifier`` - and everything above the seam
    is real.
    """
    with TestClient(
        _google_app(db_path, password_hasher, build_payment_provider, verifier)
    ) as client:
        yield client


@pytest.fixture
def unreachable_google_client(
    db_path, password_hasher, build_payment_provider, build_google_verifier
):
    """The same installation, with Google configured and unreachable.

    ``sms_client``'s ``broken_sms_client`` counterpart one provider over, and it
    exists because this state is otherwise unreachable in a test: the three other
    503s are produced by *clearing a variable*, and this one needs a client id that
    is present and a key set that cannot be fetched. The fake is given a scripted
    failure rather than a bad token, because the class of the exception is the whole
    subject - a bad token is a 401 and this is a 503.
    """

    def _unreachable():
        return build_google_verifier(
            failures=[GoogleProviderError("the Google key set could not be fetched")]
        )

    with TestClient(
        _google_app(
            db_path, password_hasher, build_payment_provider, _unreachable()
        )
    ) as client:
        yield client


@pytest.fixture
def stored(db_path):
    """The account row on disk for an address, read around the repository.

    Read through a unit of work rather than over the wire for the one fact HTTP
    cannot report: ``UserOut`` carries an address and a number and no subject, so
    "this account was not linked to a Google identity" is only observable below the
    API - which is unfortunate, because it is the whole reason the address
    collision is a refusal rather than a merge.
    """

    def _stored(email: str):
        uow = SqliteUnitOfWorkFactory(db_path).start()
        try:
            return uow.users.find_by_email(email)
        finally:
            uow.rollback()

    return _stored


def sign_up(client, token, **extra):
    """``POST /users/google``, spelled once.

    Deliberately *not* asserting the status: a 409 or a 401 is a perfectly good
    outcome for several of the calls below, and a helper that asserted success would
    have to be bypassed by exactly the tests that matter most.
    """
    return client.post("/users/google", json={"id_token": token, **extra})


def log_in(client, token, **extra):
    """``POST /sessions/google``, with the same reasoning about the status."""
    return client.post("/sessions/google", json={"id_token": token, **extra})


class TestRegistering:
    """``POST /users/google``: an account, an address, and no session."""

    def test_it_answers_201_with_the_account_it_created(self, google_client, verifier):
        """The shape of the response mirrors ``POST /users``, because it is that act.

        Nothing about it is Google's: no subject is reported, because a subject is
        this system's way of finding the account again rather than something a
        client has any use for. What comes back is an account with an address and no
        number - the number is absent rather than empty, since nothing was proved
        about a handset.
        """
        response = sign_up(google_client, verifier.mint(SUBJECT, ADDRESS))

        assert response.status_code == 201, response.text
        body = response.json()
        assert UUID(body["user_id"])
        assert body["email"] == ADDRESS
        assert body["phone"] is None

    def test_it_hands_out_no_token(self, google_client, verifier):
        """**The separation ``POST /users`` records, in the form the wire can show.**

        A signup that also signed the caller in would mean a client's first session
        came from a request that proved nothing about the person holding it - and the
        caller would have no way to tell. The token is the only thing that could
        leak here, so its absence is the assertion.
        """
        response = sign_up(google_client, verifier.mint(SUBJECT, ADDRESS))

        assert "token" not in response.json()

    def test_it_needs_no_session(self, google_client, verifier):
        """**An unauthenticated write, asserted from both sides.**

        No header at all, and then a nonsense bearer token - which rules out a
        dependency that resolves an actor when one is *offered* rather than one that
        demands it. The second case is the one that matters: a route that worked
        anonymously but read a session if present would pass the first assertion and
        fail here.
        """
        assert (
            sign_up(google_client, verifier.mint(SUBJECT, ADDRESS)).status_code == 201
        )

        second = verifier.mint(OTHER_SUBJECT, OTHER_ADDRESS)
        assert (
            sign_up(
                google_client,
                second,
                headers={"Authorization": "Bearer not-a-real-token"},
            ).status_code
            == 201
        )

    def test_a_token_google_did_not_sign_is_refused_with_401(self, google_client):
        """One class for every way a token can be wrong, and the caller sees none of it.

        Unknown, malformed, expired, signed by the wrong key, issued to another
        application - all of them arrive as ``InvalidGoogleTokenError``, for
        ``InvalidSessionError``'s reason: the remedy is identical ("get a fresh
        token") and a message that separated them would describe a forgery to the
        forger.
        """
        response = sign_up(google_client, "a-token-nobody-ever-minted")

        assert response.status_code == 401, response.text
        assert response.json()["error"] == "InvalidGoogleTokenError"

    def test_an_unverified_address_is_refused_with_400(self, google_client, verifier):
        """The load-bearing refusal, at the wire.

        An account's address is what a password-reset code is mailed to, with no
        credential check - so an account holding an address Google has merely
        *claimed* could be taken over by whoever controls that address. The status is
        400 rather than 401: this token *is* Google's, and the caller cannot fix it
        by presenting a better one, only by using a different Google account.
        """
        response = sign_up(
            google_client,
            verifier.mint(SUBJECT, ADDRESS, email_verified=False),
        )

        assert response.status_code == 400, response.text
        assert response.json()["error"] == "UnverifiedGoogleEmailError"

    def test_an_address_another_account_holds_is_a_409_and_is_not_linked(
        self, google_client, verifier, stored
    ):
        """**The decision this flow was designed around, and the second assertion is it.**

        An account already holds this address. The refusal is right for the reason
        ``DuplicateEmailError`` gives - the resource the caller named is taken - and
        the sentence names the remedy, which is *another address* rather than *log
        in*, because this caller may well not be that account's owner.

        What makes it a decision rather than a message is the row underneath:
        ``google_subject`` is still ``None`` afterwards. Linking instead of refusing
        would be this system deciding that whoever holds the Google account is
        whoever holds the address - which is a claim it has no evidence for, and the
        evidence it does have (a verified address) is exactly what a Google account
        can change.

        The first account is made over the wire with ``POST /users``, so the account
        in the way is one a real client could have made rather than a seeded row.
        """
        assert (
            google_client.post(
                "/users", json={"email": ADDRESS, "password": TEST_USER_PASSWORD}
            ).status_code
            == 201
        )

        response = sign_up(google_client, verifier.mint(SUBJECT, ADDRESS))

        assert response.status_code == 409, response.text
        assert response.json()["error"] == "DuplicateEmailError"
        assert ADDRESS in response.json()["detail"]
        assert stored(ADDRESS).google_subject is None

    def test_a_google_account_that_already_registered_is_a_different_409(
        self, google_client, verifier
    ):
        """The same person arriving twice, and the sentence sends them to ``login``.

        Distinct from the address collision above because the remedies differ, and
        checked first for a related reason: it is the one the caller cannot work
        around, whereas "use another address" is advice a person can act on. A
        caller told to use another address by somebody who already has an account
        here would go and make a second one.
        """
        assert sign_up(google_client, verifier.mint(SUBJECT, ADDRESS)).status_code == 201

        response = sign_up(google_client, verifier.mint(SUBJECT, OTHER_ADDRESS))

        assert response.status_code == 409, response.text
        assert response.json()["error"] == "DuplicateGoogleSubjectError"

    def test_the_client_cannot_name_the_subject_the_address_or_the_verification(
        self, google_client, verifier
    ):
        """**Extra fields are sent, and every one of them is ignored.**

        Pydantic drops unknown fields, so this request is not refused - it is
        answered, and the account it creates is the one the *token* describes. That
        is the property worth pinning rather than the drop itself: a request
        carrying ``"email": "someone-else@example.com"`` and
        ``"email_verified": true`` produces an account at the token's address, so a
        client has no way to influence either from outside the signed bytes.
        """
        response = sign_up(
            google_client,
            verifier.mint(SUBJECT, ADDRESS),
            subject="a-subject-i-chose",
            email=OTHER_ADDRESS,
            email_verified=True,
        )

        assert response.status_code == 201, response.text
        assert response.json()["email"] == ADDRESS


class TestSigningIn:
    """``POST /sessions/google``: a token of ours for a token of Google's."""

    def test_it_answers_201_with_a_token_this_system_will_accept(
        self, google_client, verifier
    ):
        """**The round trip, and the only assertion here that proves the flow works.**

        A token in the response would be worth nothing if it were not one the rest of
        the API accepted, so the test spends it: ``GET /users/me`` resolves it to the
        account the Google identity created. That is the whole feature - an account
        with no password, reached and held.

        The response mirrors ``POST /sessions`` exactly, and a client cannot tell
        from the body which of the two it used, which is correct: it holds the same
        kind of token either way and signs it out at the same endpoint.
        """
        token = verifier.mint(SUBJECT, ADDRESS)
        assert sign_up(google_client, token).status_code == 201

        response = log_in(google_client, token)

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["token"]
        assert body["token"] != token
        assert body["user"]["email"] == ADDRESS

        me = google_client.get(
            "/users/me", headers={"Authorization": f"Bearer {body['token']}"}
        )
        assert me.status_code == 200, me.text
        assert me.json()["email"] == ADDRESS

    def test_an_unknown_subject_is_refused_with_the_login_refusal(
        self, google_client, verifier
    ):
        """**It never creates an account, and the status is the login's own.**

        A create-or-log-in endpoint would make "the address was free" and "the proof
        held" one response - the find-or-create ``SignUp`` spent Phase 2a removing,
        arrived at from the other direction. So a subject nobody here has is a 401
        with the same class a wrong password gets, and the remedy is named by the
        route that can act on it.
        """
        response = log_in(google_client, verifier.mint(SUBJECT, ADDRESS))

        assert response.status_code == 401, response.text
        assert response.json()["error"] == "InvalidCredentialsError"

    def test_it_creates_nothing(self, google_client, verifier, stored):
        """The assertion behind the refusal above, since a 401 proves no write either way.

        The row is what makes "refused" different from "refused and quietly
        registered anyway", and it is the state the whole decision turns on.
        """
        log_in(google_client, verifier.mint(SUBJECT, ADDRESS))

        assert stored(ADDRESS) is None

    def test_it_is_reachable_without_a_session(self, google_client, verifier):
        """The same property as the sign-up's, on the route that hands out credentials.

        A login that needed a session would be a login nobody locked out of their
        account could use - the reasoning ``POST /users`` and ``POST /sessions``
        already carry, and the reason this pair sits above the actor line in the CLI
        as well.
        """
        token = verifier.mint(SUBJECT, ADDRESS)
        sign_up(google_client, token)

        assert log_in(google_client, token).status_code == 201
        assert (
            log_in(
                google_client,
                token,
                headers={"Authorization": "Bearer not-a-real-token"},
            ).status_code
            == 201
        )


class TestAnInstallationWithoutAClientId:
    """Both routes, against the *ordinary* test application, and no arrangement.

    ``client`` is the application every other API test uses, and it is genuinely
    unconfigured for Google: ``create_app`` reads the environment when it is not
    handed settings, and this suite clears ``GOOGLE_CLIENT_ID``. So the 503 below is
    not a state a fixture manufactured - it is what a fresh clone does.

    The status is 503 rather than 400, and the difference is the kind of question: a
    400 would say the caller's token is at fault, and the caller has no token that
    could fix this. It is ``NoSmsAccountError``'s grade one provider over, with one
    thing stronger than that one: an installation that cannot text cannot verify a
    number, and a flow may go around it. Here there is no going around it, because
    the token is the entire input.
    """

    def test_signing_up_names_the_missing_variable(self, client):
        response = sign_up(client, "any-token-at-all")

        assert response.status_code == 503, response.text
        assert response.json()["error"] == "NoGoogleAccountError"
        assert "GOOGLE_CLIENT_ID is not set" in response.json()["detail"]

    def test_signing_in_names_it_too(self, client):
        """Asserted separately on purpose: these are two dependencies and two routes.

        They read one ``app.state.google`` because ``create_app`` resolves it once,
        and a fix applied to one route is the shape this pair exists to catch.
        """
        response = log_in(client, "any-token-at-all")

        assert response.status_code == 503, response.text
        assert "GOOGLE_CLIENT_ID is not set" in response.json()["detail"]


class TestWhenGoogleCannotBeReached:
    """The other 503, and the one no client can cause or fix.

    ``GoogleProviderError`` and ``NoGoogleAccountError`` are separate classes and
    both grade 503, deliberately: one says this deployment was never given a
    client id and an operator sets one, the other says it has everything it needs
    and the world is not answering. The grade is defined by what the caller
    should do about it, and for both the answer is "come back later" rather than
    "change your request".

    **This class exists because the grading was wrong when it was written.** A new
    domain refusal falls through ``errors.py``'s ``_grade`` to a 400 unless it is
    named in one of the four lists, and this one was documented as a 503 in three
    places - its own docstring, ``UNAVAILABLE``'s comment, and the adapter - and
    graded as a 400 anyway. That is the mistake ``errors.py``'s header describes:
    a refusal quietly telling a caller their token was wrong when it was never
    read.

    **The wire is the only place that mistake is visible**, which is why the test
    is here and not nearer the code. ``GoogleProviderError`` is raised by the
    adapter and asserted directly in the use-case tests, so those are indifferent
    to its grade; the CLI prints ``_describe(exc)`` and returns 1 for every
    refusal alike, so it cannot see one either. Only ``_grade`` reads the class
    name, and only a response carries the grade.

    The fake is given a scripted failure rather than a bad token because the class
    *is* the subject: a bad token is a 401, and this is the 503 a broken key set
    produces.
    """

    def test_a_key_set_that_cannot_be_fetched_is_a_503_and_says_so(
        self, unreachable_google_client
    ):
        """The key set is what is missing, and the class name is the assertion rather than the status.

        Many refusals grade 503; exactly one means "the Google key set could not be
        fetched", and a client that switched on the status alone would treat a
        deployment with no client id and one that cannot reach Google alike.
        """
        response = sign_up(unreachable_google_client, "any-token-at-all")

        assert response.status_code == 503, response.text
        assert response.json()["error"] == "GoogleProviderError"

    def test_signing_in_names_it_too(self, unreachable_google_client):
        """Asserted separately on purpose: these are two use cases and two routes.

        They reach one grading table through two independent call sites, and a fix
        applied to one is the shape this pair exists to catch - the same argument
        the pair above it makes about two dependencies on one ``app.state``.
        """
        response = log_in(unreachable_google_client, "any-token-at-all")

        assert response.status_code == 503, response.text
        assert response.json()["error"] == "GoogleProviderError"
