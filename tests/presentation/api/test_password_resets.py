"""Resetting a forgotten password over HTTP, from both ends and with no session.

The two routes are one feature and this file follows them in order, but five
things about the wire are worth naming before the tests that pin them:

**The 202 is the same bytes either way.** This is the only response in the API
that is deliberately identical for an address that names an account and one that
does not, and the test asserts it on the raw text rather than on a parsed body -
a difference of one word is what an enumeration oracle looks like.

**The code exists in the mail and nowhere else.** Not in the response body, not in
a header, not in the outcome. So a test that wants to answer a request reads it
out of the envelope, exactly as the person does - which is why ``code_in`` is
imported rather than reinvented, and why one test asserts the absence in the
strongest available form: the *literal code* is not a substring of the response
text.

**Both routes are unauthenticated writes**, and this file asserts the property
from both sides for the confirm: a request with no ``Authorization`` header
succeeds, and one carrying a nonsense bearer token also succeeds - which rules out
a dependency that reads a session when it is offered. The request route has no
such seam at all.

**The headline claim is a session that stops working.** A token minted before the
reset must be a 401 afterwards and the new password must log in - both over the
wire, because "the sessions were deleted" is only worth anything if the door
actually refuses them.

**Two installations, two behaviours.** With mail settings the request mails a code;
without them it refuses with a 503 naming the missing variable, which is the
opposite of what the address change does and the whole of the third ruling behind
this feature.
"""

from datetime import datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app import composition_root
from app.domain.identity.passwordReset import PASSWORD_RESET_LIFETIME, PasswordReset
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
    open_sqlite_connection,
)
from app.infrastructure.settings import EmailSettings
from app.presentation.api.app import create_app
from tests.conftest import RESET_CODE_LABEL, TEST_USER_PASSWORD, code_in
from tests.presentation.api.conftest import ALICE, BOB

#: The password the reset tests set, and a different string from the one the
#: account was registered with. Deliberately not the shared constant: the claim
#: being tested is that the *old* password stopped working, and a test that reused
#: it could not tell an update from a no-op.
NEW_PASSWORD = "a-longer-and-different-one"

#: A password ``PlainPassword`` refuses, for the divergence test.
WEAK_PASSWORD = "short"

#: A complete mail account, so ``_channel_for`` takes the branch under test. The
#: values are never dialled - the adapter itself is replaced below - but they must
#: be *complete*, or ``from_environment``'s absence is what the fake would stand in
#: for and the mailing path would never be reached.
MAIL = EmailSettings(
    host="smtp.example.com",
    port=2525,
    sender="budget@example.com",
    recipient="budget@example.com",
    starttls=False,
)


class Mailbox:
    """The installation's mail account, faked at the one place it is reached.

    ``composition_root._channel_for`` builds a **new** ``SmtpNotificationChannel``
    on every request - the adapter holds nothing worth keeping between sends - so
    what stands in for it here is a factory rather than a single channel. A fake
    that handed back one shared channel would differ from the real adapter in
    exactly the way these tests care about, which is what the mail account is doing
    *at the moment of this send*.

    ``calls`` records the arguments rather than ignoring them, so a wiring mistake
    in ``_channel_for`` - the port, the sender, a username swapped with a password
    - is a failing assertion somewhere instead of a silent difference between the
    fake and the real adapter.
    """

    def __init__(self, build_channel):
        self._build = build_channel
        self.calls: list = []
        self.channels: list = []
        self._armed: list = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        channel = self._build(failures=self._armed)
        # Consumed by the channel that was just built, so the *next* request finds a
        # working mail account - "the next send" rather than "every send from now on".
        self._armed = []
        self.channels.append(channel)
        return channel

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
def mailbox(build_channel, monkeypatch):
    """A mail account the requests really go through, and no socket anywhere.

    **The adapter is replaced rather than the builder**, and the seam is the one
    ``tests/test_composition_root.py`` already uses. What is substituted is the
    thing that would open a connection, so everything above it - ``_channel_for``'s
    choice, the settings it reads, the service's use of the result, the whole of
    both use cases - is the real code.
    """
    box = Mailbox(build_channel)
    monkeypatch.setattr(composition_root, "SmtpNotificationChannel", box)
    return box


@pytest.fixture
def mail_client(db_path, password_hasher, mailbox):
    """The application as an installation *with* a mail account.

    **A third installation, and it belongs in this file rather than the conftest**
    for the reason ``unconfigured_app`` belongs in that one: ``app`` is the ordinary
    test application and ``unconfigured_app`` is the one with no payments, and
    neither of them is this. The difference is not decoration - with no SMTP
    settings ``POST /password-resets`` is a 503 rather than a mailed code - so every
    test of the mailing path needs this application and every test of the refusal
    needs the ordinary one.

    **It shares ``db_path`` with ``client``, and the two must not be used in one
    test.** They are one store seen through two configurations, so a test that
    registered through one and asked through the other would be relying on that
    rather than saying it.
    """
    with TestClient(
        create_app(
            unit_of_work_factory=SqliteUnitOfWorkFactory(db_path),
            settings=MAIL,
            password_hasher=password_hasher,
        )
    ) as client:
        yield client


def register(client, email: str) -> None:
    """Create the account, asserting it worked."""
    created = client.post(
        "/users", json={"email": email, "password": TEST_USER_PASSWORD}
    )
    assert created.status_code == 201, created.text


def sign_in(client, email: str, password: str = TEST_USER_PASSWORD) -> str:
    """Exchange credentials for a token, asserting it worked."""
    response = client.post("/sessions", json={"email": email, "password": password})
    assert response.status_code == 201, response.text
    return response.json()["token"]


@pytest.fixture
def as_mail_user(mail_client):
    """Authorization headers on the mail-configured application.

        as_mail_user()        # alice, registered and signed in
        as_mail_user(BOB)     # bob, the same way

    Uncached, like ``as_existing_user``: it is called once or twice in a test that
    is about something else, and a cache would be a second place a token lives for
    no gain. The token it returns is the one the revocation tests assert *against*.
    """

    def _headers(email: str = ALICE) -> dict:
        register(mail_client, email)
        return {"Authorization": f"Bearer {sign_in(mail_client, email)}"}

    return _headers


@pytest.fixture
def seed_reset(db_path):
    """Write a pending reset straight into the store, for the state HTTP cannot reach.

    **Expiry is the reason.** A request's window is fifteen minutes of wall clock
    and no test may wait for one, so an expired code is produced the way time
    produces one: a request issued sixteen minutes ago. That is not a workaround
    for a missing seam - it *is* the state, written by the same ``PasswordReset.issue``
    the use case calls and read back by the same claim, and it is honest about what
    an expired code is: a perfectly valid row that the window has moved past.

    The account id is read from ``/users/me`` rather than passed in, because that is
    where a *client* would get it - and because it keeps the one thing this fixture
    must not do out of reach: naming an account some other way.
    """

    def _seed(client, headers, age: timedelta = timedelta(0)) -> str:
        me = client.get("/users/me", headers=headers)
        assert me.status_code == 200, me.text

        reset, token = PasswordReset.issue(
            user_id=UUID(me.json()["user_id"]),
            now=datetime.now() - age,
        )

        factory = SqliteUnitOfWorkFactory(db_path)
        uow = factory.start()
        try:
            uow.password_resets.save(reset)
            uow.commit()
        finally:
            uow.rollback()
        return token

    return _seed


def ask(client, email=ALICE):
    """``POST /password-resets``, spelled once.

    Deliberately *not* asserting the status: a 503 is a perfectly good outcome for
    some of the calls below, and a helper that asserted success would have to be
    bypassed by exactly the tests that matter most.
    """
    return client.post("/password-resets", json={"email": email})


def confirm(client, token, password=NEW_PASSWORD, headers=None):
    """``POST /password-resets/confirm``, with or without a session.

    ``headers=None`` is the ordinary case and is passed as *no argument at all* -
    ``post(..., headers=None)`` and ``post(...)`` are the same request, and the
    default is written so that a test asserting the token-only property reads
    exactly like a client that has no session to send.
    """
    return client.post(
        "/password-resets/confirm",
        json={"token": token, "password": password},
        headers=headers,
    )


class TestAskingForAResetCode:
    """The request route on an installation that can send mail."""

    def test_it_answers_accepted(self, mail_client):
        """202, and the one word it carries.

        ``accepted`` rather than ``pending``, and the difference from the address
        change's word is the point: there a request definitely exists afterwards,
        here one exists only if the address names an account. A word promising a
        pending request would be false half the time it was returned.
        """
        register(mail_client, ALICE)

        response = ask(mail_client)

        assert response.status_code == 202, response.text
        assert response.json() == {"status": "accepted"}

    def test_a_stranger_can_ask(self, mail_client):
        """No ``Authorization`` header, and nothing in the body that could stand in for one.

        This is the API's fifth unauthenticated write and the first aimed at an
        account its caller has no claim on, which is what the flow is: the person is
        asking precisely because they cannot authenticate. The other four either
        create an account, exchange a secret the caller holds, or spend a code that
        arrived in a mailbox - see ``test_boundary.py``, where the ranking is spelled
        out, and decision 86.

        The account is registered so that the request has something to find: the
        claim is that a *stranger* reaches it, and an empty database would prove
        only that nothing was found.
        """
        register(mail_client, ALICE)

        response = mail_client.post("/password-resets", json={"email": ALICE})

        assert response.status_code == 202, response.text

    def test_the_code_is_mailed_to_the_account(self, mail_client, mailbox):
        """The envelope, and the only place the code exists after this call.

        Addressed to the account's own address, which is where a reset code can go:
        proving control of it is what the code is for, and sending it anywhere else
        would prove control of something that is not the account.
        """
        register(mail_client, ALICE)

        ask(mail_client)

        assert [message.recipient for message in mailbox.messages] == [ALICE]

    def test_the_code_is_nowhere_in_the_answer(self, mail_client, mailbox):
        """**The property ``PasswordResetAcceptedOut`` is shaped around, at the wire.**

        A response body is the one place a client keeps things: into devtools, into a
        proxy's log, into a snapshot in somebody's test suite. The assert is on the
        literal code rather than on a field name, because a body that carried it
        under some other key would pass a name check and leak exactly as much.
        """
        register(mail_client, ALICE)

        response = ask(mail_client)

        assert code_in(mailbox.latest, RESET_CODE_LABEL) not in response.text
        assert "token" not in response.json()

    def test_an_unknown_address_gets_the_same_bytes(self, mail_client, mailbox):
        """**The enumeration oracle, closed as tightly as this design can close it.**

        Byte-identical bodies, and the strongest form available: the raw response
        text of the two calls is compared, so a future "we have emailed ..." added to
        one arm fails here rather than in production. Note what this does *not* claim
        - the two calls do not take the same time, and the route's docstring says so.
        """
        register(mail_client, ALICE)

        known = ask(mail_client, ALICE)
        unknown = ask(mail_client, "nobody@unknown.invalid")

        assert known.status_code == unknown.status_code == 202
        assert known.text == unknown.text
        assert known.content == unknown.content

    def test_an_unknown_address_is_mailed_nothing(self, mail_client, mailbox):
        """Nothing at all, and not merely "no code".

        A courtesy "no account here" mail would leak the answer to whoever typed the
        address *and* turn this endpoint into a way to put mail into any mailbox in
        the world - which is the abuse a rate limiter is for.
        """
        register(mail_client, ALICE)
        before = mailbox.attempts

        ask(mail_client, "nobody@unknown.invalid")

        assert mailbox.attempts == before

    def test_asking_twice_mails_twice_and_the_first_code_dies(
        self, mail_client, mailbox
    ):
        """**The remedy for a mail that never arrived, over the wire.**

        The second request supersedes the first in the store, so the earlier code is
        dead the moment the second commits - and this asserts both halves in the
        order a person would meet them: two envelopes, and only the later one works.
        """
        register(mail_client, ALICE)

        ask(mail_client)
        first = code_in(mailbox.messages[0], RESET_CODE_LABEL)
        ask(mail_client)
        second = code_in(mailbox.messages[1], RESET_CODE_LABEL)

        assert first != second
        assert confirm(mail_client, first).status_code == 401
        assert confirm(mail_client, second).status_code == 201

    def test_a_mail_account_that_fails_answers_500(
        self, db_path, password_hasher, mailbox
    ):
        """**The request mail carries a credential, so a failed send raises.**

        The other half of the asymmetry that runs through this flow: the notice is
        caught into the result, and this one is not. A person who is already locked
        out, watching a mailbox that will stay empty, is the worst thing this
        endpoint could leave behind - so the failure is loud, and it is the catch-all
        ``unexpected_error_handler`` that turns it into a 500 rather than a bare
        Starlette string.

        **This test builds its own client rather than using ``mail_client``, and the
        reason is one flag.** Starlette re-raises after an ``Exception`` handler has
        produced its response - the handler's job is the *client's* body and the
        re-raise is for the server's log - so ``TestClient`` propagates the
        ``OSError`` by default and these assertions would never be reached. See
        ``test_errors.py``, where the same ``raise_server_exceptions=False`` is
        argued at length; every other test in this file wants the opposite, so the
        fixture keeps the default and this one case departs from it.
        """
        broken = create_app(
            unit_of_work_factory=SqliteUnitOfWorkFactory(db_path),
            settings=MAIL,
            password_hasher=password_hasher,
        )
        with TestClient(broken, raise_server_exceptions=False) as client:
            register(client, ALICE)
            mailbox.break_next_send(OSError("connection refused"))

            response = ask(client)

        assert response.status_code == 500, response.text
        assert response.json()["error"] == "InternalServerError"


class TestTheInstallWithoutMail:
    """Where this flow differs from the address change rather than resembling it."""

    def test_the_request_is_refused(self, client):
        """503, and it is about the installation rather than about the caller.

        An address change falls back to applying on the password proof alone, because
        it *has* a proof. A forgotten password has none - that is the premise - so an
        install with no SMTP is an install where this operation is impossible, and the
        only honest answer is to say so.
        """
        register(client, ALICE)

        response = ask(client)

        assert response.status_code == 503, response.text
        assert response.json()["error"] == "NoMailAccountError"

    def test_the_refusal_names_the_missing_variable(self, client):
        """**``describe_configuration``'s whole purpose, reaching a client unchanged.**

        The failure mode of a misconfigured notifier is silence, and "SMTP_HOST is
        not set" is the difference between a two-minute fix and an afternoon of
        guessing. The sentence is composed once by the composition root - so the CLI
        says the same words - and this asserts it survives the trip to the wire.
        """
        register(client, ALICE)

        response = ask(client)

        assert "SMTP_HOST is not set" in response.json()["detail"]

    def test_nothing_is_written(self, client, db_path):
        """Refused before a unit is opened, so there is no row to find.

        The refusal depends on nothing about the address, so getting past it would
        teach a caller nothing - but the row that is *not* written is what makes this
        a clean refusal rather than a request nobody can answer.

        Counted from a connection of its own over the file the application is using,
        rather than through the app's factory: what is being asserted is what is on
        disk, which is a fact about the file and not about any object's opinion of it.
        """
        register(client, ALICE)

        ask(client)

        connection = open_sqlite_connection(db_path)
        try:
            assert (
                connection.execute("SELECT COUNT(*) FROM password_resets").fetchone()[0]
                == 0
            )
        finally:
            connection.close()

    def test_a_stranger_address_is_refused_the_same_way(self, client):
        """**Before the lookup, so the refusal cannot become an oracle.**

        The check is first in the use case precisely so that it depends on nothing
        about the address. A version that looked the address up first and refused only
        when an account was found would answer 503 for a real address and 202 for an
        unknown one - an enumeration oracle built out of an *error*, which is the more
        conspicuous of the two answers.
        """
        response = ask(client, "nobody@unknown.invalid")

        assert response.status_code == 503


class TestAnsweringAResetCode:
    """The confirm route, and the state it leaves the account in."""

    def test_it_answers_201_with_the_account(self, mail_client, as_mail_user, mailbox):
        """201, because a credential that did not exist has been created.

        ``user`` is embedded rather than left to a second call, and here that matters
        more than it does for an address change: this request was authorised by a code
        rather than a session, so the client may hold no usable token at all - every
        one of them has just been deleted.
        """
        headers = as_mail_user()
        ask(mail_client)

        response = confirm(mail_client, code_in(mailbox.latest, RESET_CODE_LABEL))

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["user"]["email"] == ALICE
        assert body["sessions_revoked"] == 1
        assert body["notice_sent"] is True

    def test_the_old_bearer_token_is_refused(
        self, mail_client, as_mail_user, mailbox
    ):
        """**The headline claim of the whole feature, at the door.**

        A token minted before the reset must stop working, and the assertion is the
        one a *client* would make: the same header that succeeded a moment ago now
        gets a 401. "The sessions were deleted" is only worth anything if the
        dependency that reads them actually refuses one - a row left behind under a
        different key would pass every test that counted rows in the table.
        """
        headers = as_mail_user()
        assert mail_client.get("/users/me", headers=headers).status_code == 200

        ask(mail_client)
        confirm(mail_client, code_in(mailbox.latest, RESET_CODE_LABEL))

        assert mail_client.get("/users/me", headers=headers).status_code == 401

    def test_the_new_password_logs_in(self, mail_client, as_mail_user, mailbox):
        """The other half, and the one the person actually needs.

        A revocation that left nobody able to sign in would satisfy every test above
        and be the worst possible outcome - so the new secret is proved at the login
        endpoint rather than by reading the credential row, which is the layer a
        client is entitled to depend on.
        """
        as_mail_user()
        ask(mail_client)

        confirm(mail_client, code_in(mailbox.latest, RESET_CODE_LABEL))

        response = mail_client.post(
            "/sessions", json={"email": ALICE, "password": NEW_PASSWORD}
        )
        assert response.status_code == 201, response.text

    def test_the_old_password_stops_working(self, mail_client, as_mail_user, mailbox):
        """The counterpart, so the pair cannot both pass for the wrong reason."""
        as_mail_user()
        ask(mail_client)

        confirm(mail_client, code_in(mailbox.latest, RESET_CODE_LABEL))

        response = mail_client.post(
            "/sessions", json={"email": ALICE, "password": TEST_USER_PASSWORD}
        )
        assert response.status_code == 401, response.text

    def test_it_needs_no_session(self, mail_client, as_mail_user, mailbox):
        """**The confirm is authorised by the code and by nothing else.**

        Sent with no ``Authorization`` header at all, which is the whole point: the
        person asking is locked out. Requiring a session would refuse exactly the
        person who asked at a desk and opened the mail on a phone.
        """
        as_mail_user()
        ask(mail_client)

        response = confirm(mail_client, code_in(mailbox.latest, RESET_CODE_LABEL))

        assert response.status_code == 201, response.text

    def test_a_nonsense_session_is_ignored_rather_than_refused(
        self, mail_client, as_mail_user, mailbox
    ):
        """**Which rules out a dependency that reads a session when it is offered.**

        A client that happens to be holding a stale token - a browser with a header
        set from another account - must not be refused for it, and the code must still
        work. This is the test that distinguishes "no authentication is required"
        from "authentication is optional", and the second would be a hole: the account
        acted on would depend on which of the two the server believed.
        """
        as_mail_user()
        ask(mail_client)

        response = confirm(
            mail_client,
            code_in(mailbox.latest, RESET_CODE_LABEL),
            headers={"Authorization": "Bearer not-a-real-token"},
        )

        assert response.status_code == 201, response.text

    def test_the_code_is_not_echoed_back(self, mail_client, as_mail_user, mailbox):
        """The response describes the account and never the credential that reached it."""
        as_mail_user()
        ask(mail_client)
        code = code_in(mailbox.latest, RESET_CODE_LABEL)

        response = confirm(mail_client, code)

        assert code not in response.text
        assert "token" not in response.json()

    def test_the_notice_is_mailed_to_the_account(self, mail_client, as_mail_user, mailbox):
        """Two messages by the end: the code, and the warning that it was used.

        The second is the only warning a person gets that somebody else set their
        password, which is what makes it the more important of the two rather than a
        courtesy.
        """
        as_mail_user()
        ask(mail_client)

        confirm(mail_client, code_in(mailbox.latest, RESET_CODE_LABEL))

        assert [message.recipient for message in mailbox.messages] == [ALICE, ALICE]
        assert "Your password was changed" in mailbox.latest.subject

    def test_a_bounced_notice_is_reported_and_the_reset_stands(
        self, mail_client, as_mail_user, mailbox
    ):
        """**The asymmetry, at the wire: 201 with a footnote rather than a 500.**

        Refusing to apply a reset because the warning about it bounced would leave the
        person locked out with the password unchanged and the code that would have
        changed it spent - the worst state this feature can produce. So the failure is
        a field on a success rather than a status.
        """
        as_mail_user()
        ask(mail_client)
        code = code_in(mailbox.latest, RESET_CODE_LABEL)
        mailbox.break_next_send(OSError("mailbox full"))

        response = confirm(mail_client, code)

        assert response.status_code == 201, response.text
        assert response.json()["notice_sent"] is False
        assert "mailbox full" in response.json()["notice_error"]

    def test_a_second_account_is_untouched_by_the_revocation(
        self, mail_client, as_mail_user, mailbox
    ):
        """**The account-scoped deletion with the widest blast radius, controlled.**

        Two accounts, both signed in, one reset - and the other account is still signed
        in afterwards. ``delete_by_user_id`` is the only method on the session port
        whose scope is an *account* rather than an actor, and this is the test that
        would fail if the id it takes ever came from a request rather than from the
        claimed row.
        """
        alice = as_mail_user()
        bob = as_mail_user(BOB)

        ask(mail_client, ALICE)
        confirm(mail_client, code_in(mailbox.latest, RESET_CODE_LABEL))

        assert mail_client.get("/users/me", headers=alice).status_code == 401
        assert mail_client.get("/users/me", headers=bob).status_code == 200


class TestTheThreeRefusals:
    """Three classes and three statuses, and the remedies they send a person to."""

    def test_a_code_that_names_nothing_is_401(self, mail_client):
        response = confirm(mail_client, "never-minted")

        assert response.status_code == 401
        assert response.json()["error"] == "InvalidPasswordResetTokenError"

    def test_a_code_already_answered_is_409(self, mail_client, as_mail_user, mailbox):
        """*Ask again* rather than *ask again and answer it sooner*, and the split matters.

        Somebody who has already reset their password and presents the same code again
        has, in all likelihood, forgotten which mail they answered - and this status
        tells them their code was real and was used, which is the fact they need in
        order to go looking for the next one.
        """
        as_mail_user()
        ask(mail_client)
        code = code_in(mailbox.latest, RESET_CODE_LABEL)
        assert confirm(mail_client, code).status_code == 201

        response = confirm(mail_client, code)

        assert response.status_code == 409
        assert response.json()["error"] == "PasswordResetAlreadyUsedError"

    def test_a_code_past_its_window_is_409(self, mail_client, as_mail_user, seed_reset):
        """The refusal that sends them to the form rather than to their mail.

        Produced the way time produces one - a request issued sixteen minutes ago -
        because no test may wait a window out, and because that *is* the state: a
        perfectly valid row that the clock has moved past.
        """
        headers = as_mail_user()
        code = seed_reset(mail_client, headers, age=PASSWORD_RESET_LIFETIME + timedelta(minutes=1))

        response = confirm(mail_client, code)

        assert response.status_code == 409
        assert response.json()["error"] == "PasswordResetExpiredError"

    def test_a_weak_password_is_400_and_does_not_spend_the_code(
        self, mail_client, as_mail_user, mailbox
    ):
        """**The one behaviour a reader will assume the opposite of.**

        Every other refusal in this route is *after* the claim; this one is before it,
        so the 400 leaves the code usable and the same code answers successfully with a
        longer password. The alternative - spending a code on a typo of the password,
        and making somebody go and find another mail - is the flow being strict about
        the wrong thing.
        """
        as_mail_user()
        ask(mail_client)
        code = code_in(mailbox.latest, RESET_CODE_LABEL)

        refused = confirm(mail_client, code, password=WEAK_PASSWORD)

        assert refused.status_code == 400, refused.text
        assert refused.json()["error"] == "WeakPasswordError"

        accepted = confirm(mail_client, code, password=NEW_PASSWORD)

        assert accepted.status_code == 201, accepted.text

    def test_a_refusal_leaves_the_account_signed_in(
        self, mail_client, as_mail_user, mailbox
    ):
        """Nothing happened, and the strongest available form of "nothing" is that the
        session a client is already holding still works.

        A refusal that had already deleted the sessions and then failed would sign
        somebody out of an account whose password did not change - which is the kind of
        half-state that reads as a bug in an unrelated feature.
        """
        headers = as_mail_user()
        ask(mail_client)

        refused = confirm(mail_client, "never-minted")

        assert refused.status_code == 401
        assert mail_client.get("/users/me", headers=headers).status_code == 200
