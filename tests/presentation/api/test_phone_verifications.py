"""Signing up with a number over HTTP, from both ends and with no session.

The two routes are one feature and this file follows them in order. Five things
about the wire are worth naming before the tests that pin them:

**The request is 202 and it says what it did.** ``posts/password_resets.py``
answers with an empty body for a good reason - the endpoint asks the store a
question whose answer would be an enumeration oracle - and this one asks nothing,
so it can honestly say which number it is about to text and how long the person
has. That asymmetry is asserted here rather than left to the route's docstring:
the two responses look alike and are not.

**The code exists in the message and nowhere else.** Not in the response body, not
in a header. So a test that wants to answer a request reads it out of the channel,
exactly as the person does - which is why ``code_in`` is imported rather than
reinvented, and why one test asserts the absence in the strongest available form:
the *literal code* is not a substring of the response text.

**Both routes are unauthenticated writes**, and this file asserts the property
from both sides for the confirm - a request with no ``Authorization`` header
succeeds, and one carrying a nonsense bearer token also succeeds, which rules out
a dependency that reads a session when it is offered.

**The number is never in the confirm's body.** It is read off the claimed row, so
a client that sends one anyway cannot redirect the account it is creating - and
that is a test rather than a comment, because pydantic ignores an unknown field
and the request would otherwise look harmless.

**Two installations, two behaviours.** With Termii settings the request texts a
code; without them it refuses with a 503 naming the missing variable. The
*confirm*, by contrast, needs no channel at all - a code that was texted by some
other install is answerable here - and that pair is what the last two classes
below pin.
"""

from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app import composition_root
from app.domain.identity.phoneVerification import (
    PHONE_VERIFICATION_LIFETIME,
    PhoneVerification,
)
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
    open_sqlite_connection,
)
from app.presentation.api.app import create_app
from app.domain.identity.user import User
from tests.conftest import (
    SIGNUP_CODE_LABEL,
    TEST_TERMII_SETTINGS,
    code_in,
)
from tests.presentation.api.conftest import ALICE

#: The number as a person in Nigeria types it, and the one most of this file
#: starts from. What the store and the provider see is not what was typed, and
#: several tests here exist to say so.
TYPED = "08012345678"
FOLDED = "2348012345678"

#: A second handset, for the tests that need two numbers in play at once.
OTHER_TYPED = "08098765432"
OTHER_FOLDED = "2348098765432"

#: The password the confirmed account is given, and a different string from the
#: suite's shared constant: the account it creates is not one any other fixture
#: can sign into, so reusing the constant would only invite a test that tried.
SIGNED_UP_PASSWORD = "a-longer-and-different-one"

#: A password ``PlainPassword`` refuses, for the divergence test.
WEAK_PASSWORD = "short"


class Handset:
    """The installation's SMS account, faked at the one place it is reached.

    ``composition_root._sms_channel_for`` builds a **new** ``TermiiSmsChannel`` on
    every request - the adapter holds nothing worth keeping between sends - so what
    stands in for it here is a factory rather than a single channel. A fake that
    handed back one shared channel would differ from the real adapter in exactly
    the way these tests care about, which is what the handset is doing *at the
    moment of this send*.

    ``calls`` records the arguments rather than ignoring them, so a wiring mistake
    in ``_sms_channel_for`` - the key, the sender id, a settings object that
    arrived as ``None`` - is a failing assertion somewhere instead of a silent
    difference between the fake and the real adapter.

    Note what it is *not*: ``Mailbox``'s exact counterpart, and by design. There is
    no ``latest`` message needing a subject, no ``sender``, and no ``starttls`` -
    an SMS has two fields, which is why the port is a different port. What is
    identical is the arming: ``break_next_send`` fails one send and then leaves the
    handset working, because "the next send" rather than "every send from now on"
    is the state a route's error handling should be tested against.
    """

    def __init__(self, build_channel):
        self._build = build_channel
        self.calls: list = []
        self.channels: list = []
        self._armed: list = []

    def __call__(self, settings, channel=None):
        self.calls.append((settings, channel))
        built = self._build(failures=self._armed)
        # Consumed by the channel that was just built, so the *next* request finds a
        # working handset.
        self._armed = []
        self.channels.append(built)
        return built

    def break_next_send(self, error) -> None:
        """Make the next send through the next channel raise ``error``."""
        self._armed = [error]

    @property
    def messages(self) -> list:
        """Everything that arrived, across every channel, in the order it did."""
        return [message for channel in self.channels for message in channel.sent]

    @property
    def latest(self):
        """The message that arrived last, which is the one a test is usually after."""
        return self.messages[-1]

    @property
    def attempts(self) -> int:
        """How many sends were *tried*, which a failure makes differ from arrivals."""
        return sum(len(channel.attempts) for channel in self.channels)


@pytest.fixture
def handset(build_sms_channel, monkeypatch):
    """A handset the requests really go through, and no socket anywhere.

    **The adapter builder is replaced rather than the adapter**, and the seam is the
    one ``tests/test_composition_root.py`` already uses for mail. What is
    substituted is the thing that would open a connection, so everything above it -
    the builder's choice, the settings it reads, the service's use of the result,
    the whole of both use cases - is the real code.
    """
    box = Handset(build_sms_channel)
    monkeypatch.setattr(composition_root, "_sms_channel_for", box)
    return box


def _sms_app(db_path, password_hasher, build_payment_provider):
    """The installation an SMS-capable deployment is, built twice below.

    A plain function rather than a factory fixture, because what the two fixtures
    that use it differ in is how the client is *driven* - one raises on a server
    error and one turns it into a response - and it is the same application in both.
    Two copies of this call would be two places to change when a dependency is
    added, and the one nobody edited would be the failing-send test.

    The payment provider is passed for ``app``'s reason: the suite clears
    ``PAYSTACK_SECRET_KEY``, so a client without one is an install that cannot take
    payments, and nothing in this file is about that.
    """
    return create_app(
        unit_of_work_factory=SqliteUnitOfWorkFactory(db_path),
        password_hasher=password_hasher,
        payment_provider=build_payment_provider(),
        termii_settings=TEST_TERMII_SETTINGS,
    )


@pytest.fixture
def sms_client(db_path, password_hasher, build_payment_provider, handset):
    """The application as an installation *with* an SMS account.

    **A third installation, and it belongs in this file rather than the conftest**
    for the reason ``mail_client`` belongs in the reset file: ``app`` is the
    ordinary test application, which resolves no Termii settings from the
    environment, and an install that cannot text answers this request with a 503.
    So every test of the texting path needs this application, and every test of the
    refusal needs the ordinary one - and both are asserted below rather than
    described here.

    It shares ``db_path`` with ``client``, and the two must not be used in one
    test. They are one store seen through two configurations, so a test that
    registered through one and asked through the other would be relying on that
    rather than saying it.
    """
    with TestClient(_sms_app(db_path, password_hasher, build_payment_provider)) as client:
        yield client


@pytest.fixture
def broken_sms_client(db_path, password_hasher, build_payment_provider, handset):
    """The same installation, with a server error arriving as a 500 response.

    **A second client rather than a flag on the first**, and the reset file makes
    the same split: the ordinary client is deliberately left raising, so a route
    that blew up in a test about anything else fails loudly with its traceback
    instead of quietly answering 500 and being asserted about as if it had worked.
    Only the tests that *expect* a server error drive the lenient one.
    """
    with TestClient(
        _sms_app(db_path, password_hasher, build_payment_provider),
        raise_server_exceptions=False,
    ) as client:
        yield client


@pytest.fixture
def seed_verification(db_path):
    """Write a pending verification straight into the store, for the states HTTP cannot reach.

    **Expiry is the reason.** A request's window is ten minutes of wall clock and no
    test may wait for one, so an expired code is produced the way time produces
    one: a request issued eleven minutes ago. That is not a workaround for a
    missing seam - it *is* the state, written by the same ``PhoneVerification.issue``
    the use case calls and read back by the same claim, and it is honest about what
    an expired code is: a perfectly valid row that the window has moved past.

    A client is accepted and ignored, so a test can name it or not: the row is about
    a number rather than about an account, which is this table's difference from the
    two it resembles.
    """

    def _seed(phone: str = TYPED, age: timedelta = timedelta(0)) -> str:
        verification, token = PhoneVerification.issue(
            phone=phone, now=datetime.now() - age
        )

        uow = SqliteUnitOfWorkFactory(db_path).start()
        try:
            uow.phone_verifications.save(verification)
            uow.commit()
        finally:
            uow.rollback()
        return token

    return _seed


def ask(client, phone=TYPED):
    """``POST /phone-verifications``, spelled once.

    Deliberately *not* asserting the status: a 503 is a perfectly good outcome for
    some of the calls below, and a helper that asserted success would have to be
    bypassed by exactly the tests that matter most.
    """
    return client.post("/phone-verifications", json={"phone": phone})


def answer(client, code, password=SIGNED_UP_PASSWORD, headers=None, **extra):
    """``POST /phone-verifications/confirm``, with or without a session."""
    payload = {"code": code, "password": password, **extra}
    if headers is None:
        return client.post("/phone-verifications/confirm", json=payload)
    return client.post("/phone-verifications/confirm", json=payload, headers=headers)


class TestAskingForACode:
    """The request route: what it answers, and what it hands out."""

    def test_it_answers_202(self, sms_client):
        """**202 and not 201, and here the reason is latency rather than silence.**

        The row it writes is a *pending* request, not the thing the caller asked
        for: nothing is verifiable until somebody reads a message and comes back.
        A 201 would claim an act that has not happened.
        """
        response = ask(sms_client)

        assert response.status_code == 202, response.text

    def test_the_body_names_the_number_and_the_deadline(self, sms_client):
        """**This is the difference from the reset route, asserted on the body.**

        That route's response is byte-identical for a hit and a miss because the
        endpoint asks the store a question. This one asks nothing, so the number and
        the deadline are not a leak - they are the only useful thing the server can
        say, and a client that knows when the code dies can ask for another before
        the person is staring at a failure.
        """
        body = ask(sms_client).json()

        assert body["phone"] == FOLDED
        assert body["status"] == "accepted"
        assert datetime.fromisoformat(body["expires_at"]) > datetime.now()

    @pytest.mark.parametrize(
        "typed",
        ["08012345678", "+2348012345678", "2348012345678", "0801 234 5678", "0801-234-5678"],
        ids=["national", "international", "no plus", "spaced", "dashed"],
    )
    def test_the_body_reports_the_folded_number_whichever_way_it_was_typed(
        self, sms_client, typed
    ):
        """**One number, one spelling, and the client can see which one.**

        The response echoes the value actually texted rather than the string that
        was typed, which is the one place a client can check that its own
        normalisation agrees with the server's before a code is burned. Sending back
        the typed spelling would be more comforting and less true.
        """
        assert ask(sms_client, typed).json()["phone"] == FOLDED

    def test_it_texts_the_number_once(self, sms_client, handset):
        """One message per request, billed once per request."""
        ask(sms_client)

        assert len(handset.messages) == 1
        assert handset.messages[0].recipient == FOLDED

    def test_the_code_is_in_the_text_and_not_in_the_response(self, sms_client, handset):
        """**The absence asserted in the strongest available form.**

        Not "the body has no code field" - the *literal code* is not a substring of
        the response text. A body that leaked it, in any field, under any name,
        would be a code in every proxy log and every client's debug console, on an
        endpoint whose whole authorisation is holding that code.
        """
        response = ask(sms_client)
        texted = code_in(handset.messages[0], SIGNUP_CODE_LABEL)

        assert response.status_code == 202
        assert texted not in response.text

    def test_a_malformed_number_is_a_400(self, sms_client, handset):
        """**Refused rather than folded into a request that can never be answered.**

        This is where the route differs from the reset route's treatment of a typo,
        and the difference is what the two values are for: an address is a key being
        looked up, a number is an address being dialled. There is no
        registered-or-not question hiding behind ``not-a-number``, so refusing it
        leaks nothing and saves a provider call.
        """
        response = ask(sms_client, "not-a-number")

        assert response.status_code == 400, response.text
        assert response.json()["error"] == "InvalidUserPhoneError"
        # A count and not a list: ``attempts`` sums across every channel the
        # installation built, because the adapter is new per request.
        assert handset.attempts == 0
        assert handset.messages == []

    def test_nothing_is_written_for_a_malformed_number(self, sms_client, db_path):
        ask(sms_client, "not-a-number")

        connection = open_sqlite_connection(db_path)
        try:
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM phone_verifications"
                ).fetchone()[0]
                == 0
            )
        finally:
            connection.close()

    def test_a_number_that_already_has_an_account_is_still_answered(
        self, sms_client, handset, db_path
    ):
        """**The wire-level form of the decision the whole flow rests on.**

        A taken number answers exactly like a free one and the text still goes out.
        Refusing here would be free for whoever intends to claim it - they will try
        anyway - and *asking* would turn this endpoint into an enumeration oracle
        over a space that is small, structured and guessable, guessed from a form
        with no password and no session to slow an attacker down.

        The refusal is given one step later, at the confirm, where the caller has
        proved they hold the handset and is owed a sentence about it.
        """
        factory = SqliteUnitOfWorkFactory(db_path)
        uow = factory.start()
        try:
            uow.users.save(
                User(
                    user_id=uuid4(),
                    email=None,
                    phone=TYPED,
                    google_subject=None,
                    created_at=datetime.now(),
                )
            )
            uow.commit()
        finally:
            uow.rollback()

        response = ask(sms_client)

        assert response.status_code == 202, response.text
        assert len(handset.messages) == 1

    def test_it_needs_no_authorization(self, sms_client):
        """There is no account to authorise against, and no session to offer."""
        response = ask(sms_client)

        assert response.status_code == 202

    def test_a_nonsense_bearer_token_changes_nothing(self, sms_client):
        """**Which rules out a dependency that reads a session when it is offered.**

        A route with no ``current_actor`` would pass the test above while still
        refusing - or worse, resolving an actor - for a caller that happened to send
        a header. The token here is a well-formed string that names no session.
        """
        response = sms_client.post(
            "/phone-verifications",
            json={"phone": TYPED},
            headers={"Authorization": "Bearer not-a-real-session-token"},
        )

        assert response.status_code == 202, response.text


class TestAnsweringTheCode:
    """The confirm route, and the account it creates."""

    def test_it_answers_201_with_the_account(
        self, sms_client, handset, seed_verification
    ):
        """The one response in the API that returns an account nobody has logged in to."""
        response = answer(sms_client, seed_verification())

        assert response.status_code == 201, response.text
        assert UUID(response.json()["user_id"])

    def test_the_body_holds_the_number_and_no_address(
        self, sms_client, seed_verification
    ):
        """``email`` is ``None``, and it is not an omission.

        The account was made from a text, so it has the number the code was sent to
        and no address at all - and that absence is what a deposit will refuse on
        until its holder sets one. Rendering it as ``""`` or inventing a placeholder
        would hide the one fact the person needs to act on.
        """
        body = answer(sms_client, seed_verification()).json()

        assert body["phone"] == FOLDED
        assert body["email"] is None

    def test_the_number_in_the_text_is_the_one_the_account_holds(
        self, sms_client, seed_verification
    ):
        """The code was texted to a number; that number is what the account gets."""
        body = answer(sms_client, seed_verification(OTHER_TYPED)).json()

        assert body["phone"] == OTHER_FOLDED

    def test_a_number_sent_alongside_the_code_is_ignored(
        self, sms_client, seed_verification
    ):
        """**The absence of a third field, asserted rather than assumed.**

        ``ConfirmPhoneSignUpIn`` has no ``phone`` field, and pydantic ignores an
        unknown one rather than refusing it - so a client that sends a number
        anyway could be quietly redirecting the account it is creating if the use
        case read the request at all. It does not: the number is read off the row
        the code claimed, so the only number that matters is the one the code
        proves.
        """
        body = answer(
            sms_client, seed_verification(), phone=OTHER_TYPED
        ).json()

        assert body["phone"] == FOLDED

    def test_the_code_cannot_be_answered_twice(self, sms_client, seed_verification):
        """409, and this is the refusal that means "log in, your account exists".

        The row survives being spent, which is what makes the two states
        distinguishable at all - deleting it on use would collapse this into "that
        code means nothing".
        """
        code = seed_verification()
        first = answer(sms_client, code)
        second = answer(sms_client, code)

        assert first.status_code == 201
        assert second.status_code == 409, second.text
        assert second.json()["error"] == "PhoneVerificationAlreadyUsedError"

    def test_a_code_that_means_nothing_is_a_401(self, sms_client):
        response = answer(sms_client, "a-code-nobody-ever-minted")

        assert response.status_code == 401, response.text
        assert response.json()["error"] == "InvalidPhoneVerificationTokenError"

    def test_an_expired_code_is_a_409(self, sms_client, seed_verification):
        """The window is the aggregate's, and the store tests the same boundary."""
        expired = seed_verification(age=PHONE_VERIFICATION_LIFETIME + timedelta(minutes=1))

        response = answer(sms_client, expired)

        assert response.status_code == 409, response.text
        assert response.json()["error"] == "PhoneVerificationExpiredError"

    def test_a_weak_password_is_a_400(self, sms_client, seed_verification):
        response = answer(sms_client, seed_verification(), password=WEAK_PASSWORD)

        assert response.status_code == 400, response.text
        assert response.json()["error"] == "WeakPasswordError"

    def test_a_weak_password_does_not_spend_the_code(self, sms_client, seed_verification):
        """**The remedy is "type a longer password", and this is what makes that true.**

        Every other refusal in this flow is after the claim; this one is before it,
        because ``PlainPassword`` is constructed before the unit is opened. So the
        code is still answerable, and the person does not have to ask for a second
        text - which costs a message - to correct a mistake they can fix themselves.
        """
        code = seed_verification()

        assert answer(sms_client, code, password=WEAK_PASSWORD).status_code == 400
        assert answer(sms_client, code).status_code == 201

    def test_a_number_taken_in_the_meantime_is_a_409(
        self, sms_client, seed_verification, db_path
    ):
        """**The one refusal here that is about the world rather than about the code.**

        Somebody claimed the number between the request and the answer. This is
        where that is given - after the caller has proved they hold the handset, so
        the sentence is theirs to read and asking a moment earlier could not have
        helped them.
        """
        factory = SqliteUnitOfWorkFactory(db_path)
        uow = factory.start()
        try:
            uow.users.save(
                User(
                    user_id=uuid4(),
                    email=ALICE,
                    phone=TYPED,
                    google_subject=None,
                    created_at=datetime.now(),
                )
            )
            uow.commit()
        finally:
            uow.rollback()

        response = answer(sms_client, seed_verification())

        assert response.status_code == 409, response.text
        assert response.json()["error"] == "DuplicatePhoneError"
        assert FOLDED in response.json()["detail"]

    def test_the_refusal_does_not_name_the_other_account(
        self, sms_client, seed_verification, db_path
    ):
        """The sentence says the number is registered and nothing more.

        Not whose it is, not since when, not how many accounts hold it - a person
        who has just proved they hold the handset is entitled to know the number is
        taken, and to nothing about the account it was taken by.
        """
        factory = SqliteUnitOfWorkFactory(db_path)
        uow = factory.start()
        try:
            uow.users.save(
                User(
                    user_id=uuid4(),
                    email=ALICE,
                    phone=TYPED,
                    google_subject=None,
                    created_at=datetime.now(),
                )
            )
            uow.commit()
        finally:
            uow.rollback()

        detail = answer(sms_client, seed_verification()).json()["detail"]

        assert ALICE not in detail
        assert "already registered" in detail

    def test_it_needs_no_authorization(self, sms_client, seed_verification):
        """The code *is* the authorisation, and there is nothing to hold it against."""
        assert answer(sms_client, seed_verification()).status_code == 201

    def test_a_nonsense_bearer_token_changes_nothing(self, sms_client, seed_verification):
        """A client holding a stale token must be neither helped nor refused by it."""
        response = answer(
            sms_client,
            seed_verification(),
            headers={"Authorization": "Bearer not-a-real-session-token"},
        )

        assert response.status_code == 201, response.text


class TestTheInstallationWithNoSmsAccount:
    """The refusal, and the half that does not need an SMS account at all."""

    def test_the_request_is_refused(self, client):
        """503, and it is about the installation rather than about the caller.

        A number has no fallback the way an address change has one: the reason to
        believe somebody holds a handset *is* a message arriving on it, so an
        install that cannot text is an install where this operation is impossible,
        and the only honest answer is to say so.
        """
        response = ask(client)

        assert response.status_code == 503, response.text
        assert response.json()["error"] == "NoSmsAccountError"

    def test_the_refusal_names_the_missing_variable(self, client):
        """``describe_termii_configuration``'s whole purpose, reaching a client unchanged.

        The failure mode of a misconfigured notifier is silence, and "TERMII_API_KEY
        is not set" is the difference between a two-minute fix and an afternoon of
        guessing. The sentence is composed once by the composition root - so the CLI
        says the same words - and this asserts it survives the trip to the wire.
        """
        response = ask(client)

        assert "TERMII_API_KEY is not set" in response.json()["detail"]

    def test_nothing_is_written(self, client, db_path):
        """Refused before a unit is opened, so there is no row to find."""
        ask(client)

        connection = open_sqlite_connection(db_path)
        try:
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM phone_verifications"
                ).fetchone()[0]
                == 0
            )
        finally:
            connection.close()

    def test_the_confirm_still_works(self, client, seed_verification):
        """**The asymmetry between the two halves, and it is the design.**

        ``build_confirm_phone_sign_up`` takes no settings and no channel, because a
        confirm is only reachable through a request that required one - so by the
        time a code exists, some install texted it. Here the code was seeded
        directly, standing in for "some other process sent this", and the
        unconfigured installation answers it: nothing is sent from this half, and a
        text saying "your number is verified" would tell somebody a fact they
        learned by answering the code.
        """
        response = answer(client, seed_verification())

        assert response.status_code == 201, response.text
        assert response.json()["phone"] == FOLDED


class TestWhenTheTextCannotBeSent:
    """The failure that leaves a row nobody can answer."""

    def test_it_is_a_500(self, broken_sms_client, handset):
        """**Not a silent success, and the route's docstring says why.**

        A code that did not arrive makes the row unanswerable, and the person is
        waiting on a handset - so letting this pass quietly would leave them staring
        at a phone that will never buzz while a pending verification holds the
        ``UNIQUE`` slot against their own number. An error at least tells the client
        something went wrong.

        An ``OSError`` from the channel and not an ``SmsProviderError``, and the
        difference is the grade: a provider *refusal* is a ``MoneyError`` and would
        be rendered as a 400 with its own name, while a transport failure is not a
        refusal at all and falls through to the framework's handler. The reset flow's
        equivalent test breaks its channel the same way for the same reason.
        """
        handset.break_next_send(OSError("connection refused"))

        response = ask(broken_sms_client)

        assert response.status_code == 500, response.text
        assert response.json()["error"] == "InternalServerError"

    def test_the_row_survives(self, broken_sms_client, handset, db_path):
        """The survivable half of "durable before wire".

        What is left is a row holding a hash of a code nobody has - harmless, since
        it can never be claimed, and superseded the moment the person asks again.
        Rolling it back would leave the same state from the person's side and would
        lose the record that somebody asked.
        """
        handset.break_next_send(OSError("connection refused"))

        ask(broken_sms_client)

        connection = open_sqlite_connection(db_path)
        try:
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM phone_verifications"
                ).fetchone()[0]
                == 1
            )
        finally:
            connection.close()
