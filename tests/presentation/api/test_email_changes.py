"""Changing an address over HTTP, including the write that has no session at all.

The two routes are one feature and this file follows them in order, but three
things about the wire are worth naming before the tests that pin them:

**The two installations are two different applications.** With SMTP settings,
``POST /users/me/email-changes`` mails a code and answers ``pending``; without
them it moves the account and answers ``applied``. That is one route with two
behaviours, so most of the tests below choose their client deliberately - and the
fixture says which is which rather than leaving it to be worked out from the
request.

**The code exists in the mail and nowhere else.** Not in the response body, not
in a header, not in the outcome the use case returns. So a test that wants to
answer a request reads it out of the envelope, exactly as the person does - which
is why ``code_in`` is imported rather than reinvented, and why one test asserts
the absence in the strongest available form: the *literal code* is not a substring
of the response text.

**The confirm is authorised by that code and by nothing else.** It is the API's
second unauthenticated write, so this file asserts the property from both sides -
a confirm with no ``Authorization`` header succeeds, and one carrying a nonsense
bearer token also succeeds, which is what rules out a dependency that reads a
session when it is offered.
"""

from datetime import datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app import composition_root
from app.domain.identity.emailChange import EMAIL_CHANGE_LIFETIME, EmailChange
from app.infrastructure.persistence.sqlite_unit_of_work import SqliteUnitOfWorkFactory
from app.infrastructure.settings import EmailSettings
from app.presentation.api.app import create_app
from tests.conftest import TEST_USER_PASSWORD, code_in
from tests.presentation.api.conftest import ALICE, BOB

#: The address the mail-configured application moves accounts *to*. A real domain,
#: because the entry rule refuses anything else at the request - which is the rule
#: this whole slice is about, and a constant that broke it would fail in a test
#: about something else.
NEW_ADDRESS = "alice.new@example.com"

#: An address ``POST /users`` refuses today and an account can still hold: written
#: before the rule existed, read back on every load, and the trap the rescue test
#: is about. See ``legacy_account`` in the conftest.
STRANDED = "nobody@localhost"

#: A complete mail account, so ``_channel_for`` takes the branch under test. The
#: values are never dialled - the adapter itself is replaced below - but they must
#: be *complete*, or ``from_environment``'s absence is what the fake would stand in
#: for and the pending path would never be reached.
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
    what stands in for it here is a factory rather than a single channel. That is
    not a detail. A fake that handed back one shared channel would differ from the
    real adapter in exactly the way these tests care about, which is what the mail
    account is doing *at the moment of this send*; ``break_next_send`` is that
    difference made expressible in a line, and it is how "the mail account stopped
    working between the request and the answer" becomes a test rather than a
    comment about something untestable.

    ``calls`` records the arguments rather than ignoring them, so a wiring mistake
    in ``_channel_for`` - the port, the sender, a username swapped with a password -
    is a failing assertion somewhere instead of a silent difference between the
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
        # working mail account - which is what makes this "the next send" rather than
        # "every send from now on".
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


@pytest.fixture
def mailbox(build_channel, monkeypatch):
    """A mail account the requests really go through, and no socket anywhere.

    **The adapter is replaced rather than the builder**, and the seam is the one
    ``tests/test_composition_root.py`` already uses. What is substituted is the
    thing that would open a connection, so everything above it - ``_channel_for``'s
    choice, the settings it reads, the service's use of the result, the whole of
    both use cases - is the real code, which is the property that makes a test here
    worth anything.
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
    neither of them is this. The difference here is not decoration - with no SMTP
    settings the request route *applies* the change instead of mailing a code - so
    every test of the pending path needs this application and every test of the
    fallback needs the ordinary one.

    **It shares ``db_path`` with ``client``, and the two must not be used in one
    test.** They are one store seen through two configurations, so a test that
    registered through one and asked through the other would be relying on that
    rather than saying it; a test that needs both says so by seeding.

    No payment provider, unlike ``app``: nothing here spends money, and an
    application assembled with one it never uses would suggest otherwise.
    """
    with TestClient(
        create_app(
            unit_of_work_factory=SqliteUnitOfWorkFactory(db_path),
            settings=MAIL,
            password_hasher=password_hasher,
        )
    ) as client:
        yield client


@pytest.fixture
def as_mail_user(mail_client):
    """Authorization headers on the mail-configured application.

        as_mail_user()                          # alice, registered and signed in
        as_mail_user(BOB)                       # bob, the same way
        as_mail_user(STRANDED, register=False)  # an account already in the store

    The ``register`` dial exists for the reason ``as_existing_user`` exists on the
    other client, and it is reached by one test: an account this system would refuse
    to create cannot be registered through ``POST /users``, so it is seeded by
    ``legacy_account`` and only the sign-in half happens here. That half *does*
    work, which is the "an entry rule, not a lookup rule" decision made visible at
    the wire - the account that cannot be made can still be logged into, which is
    precisely the state the rescue is for.

    Uncached, unlike ``as_user``: it is called once or twice in a test that is about
    something else, and a cache would be a second place a token lives for no gain.
    """

    def _headers(email: str = ALICE, register: bool = True) -> dict:
        if register:
            created = mail_client.post(
                "/users", json={"email": email, "password": TEST_USER_PASSWORD}
            )
            assert created.status_code == 201, created.text

        response = mail_client.post(
            "/sessions", json={"email": email, "password": TEST_USER_PASSWORD}
        )
        assert response.status_code == 201, response.text
        return {"Authorization": f"Bearer {response.json()['token']}"}

    return _headers


@pytest.fixture
def seed_change(db_path):
    """Write a pending change straight into the store, for the state HTTP cannot reach.

    **Expiry is the reason.** A request's window is fifteen minutes of wall clock
    and no test may wait for one, so an expired code is produced the way time
    produces one: a request issued sixteen minutes ago. That is not a workaround for
    a missing seam - it *is* the state, written by the same ``EmailChange.issue`` the
    use case calls and read back by the same claim, and it is honest about what an
    expired code is: a perfectly valid row that the window has moved past.

    The client is an argument rather than a fixture because two tests need this on
    two different applications: the mail-configured one, for a code that has expired,
    and the ordinary one, for an installation whose mail account is gone.

    The account id is read from ``/users/me`` rather than passed in, because that is
    where a *client* would get it - and because it keeps the one thing this fixture
    must not do out of reach: naming an account some other way.
    """

    def _seed(client, headers, new_email: str, age: timedelta = timedelta(0)) -> str:
        me = client.get("/users/me", headers=headers)
        assert me.status_code == 200, me.text

        change, token = EmailChange.issue(
            user_id=UUID(me.json()["user_id"]),
            new_email=new_email,
            now=datetime.now() - age,
        )

        factory = SqliteUnitOfWorkFactory(db_path)
        uow = factory.start()
        try:
            uow.email_changes.save(change)
            uow.commit()
        finally:
            uow.rollback()
        return token

    return _seed


def ask(client, headers, email=NEW_ADDRESS, password=TEST_USER_PASSWORD):
    """``POST /users/me/email-changes``, spelled once.

    Every test in the first half of this file posts the same body and only the
    expectation differs, so the request lives here and each test says what it
    expects of the answer. Deliberately *not* asserting the status: a refusal is a
    perfectly good outcome for many of the calls below, and a helper that asserted
    success would have to be bypassed by exactly the tests that matter most.
    """
    return client.post(
        "/users/me/email-changes", json={"email": email, "password": password}, headers=headers
    )


def confirm(client, token, headers=None):
    """``POST /email-changes/confirm``, with or without a session.

    ``headers=None`` is the ordinary case and is passed as *no argument at all* -
    ``post(..., headers=None)`` and ``post(...)`` are the same request, and the
    default here is written so that a test asserting the token-only property reads
    exactly like a client that has no session to send.
    """
    return client.post("/email-changes/confirm", json={"token": token}, headers=headers)


class TestAskingForAChange:
    """The request route on an installation that can send mail."""

    def test_it_answers_pending_with_a_deadline(self, mail_client, as_mail_user):
        """201, and the two fields that say a code is in the post rather than applied.

        The status is a word rather than a boolean because there are two things this
        call can do, and a client that showed "we have emailed you" to somebody whose
        address had already moved would be lying about the one fact that matters.
        """
        response = ask(mail_client, as_mail_user())

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["status"] == "pending"
        assert body["email"] == NEW_ADDRESS
        assert datetime.fromisoformat(body["expires_at"]) > datetime.now()

    def test_the_deadline_is_the_window_this_system_mails_for(self, mail_client, as_mail_user):
        """Fifteen minutes, near enough to assert without being flaky about it.

        A range rather than an equality: the request reads the clock somewhere inside
        itself, so the deadline is a lifetime after *that* moment and not after this
        test's. What is being pinned is that the route does not invent a window of
        its own - the constant is the domain's, and an endpoint that quietly shortened
        it would send people a code that had already expired.
        """
        response = ask(mail_client, as_mail_user())

        remaining = datetime.fromisoformat(response.json()["expires_at"]) - datetime.now()

        assert timedelta(0) < remaining <= EMAIL_CHANGE_LIFETIME

    def test_the_code_is_mailed_to_the_address_being_moved_to(self, mail_client, as_mail_user, mailbox):
        """The envelope, and the only place the code exists after this call.

        Addressed to the *new* address rather than to the account, which is the whole
        proof: a code sent to the address being left would prove nothing about the one
        being moved to.
        """
        ask(mail_client, as_mail_user())

        assert [message.recipient for message in mailbox.messages] == [NEW_ADDRESS]

    def test_the_code_is_nowhere_in_the_answer(self, mail_client, as_mail_user, mailbox):
        """**The property ``EmailChangeOut`` is shaped around, asserted at the wire.**

        A response body is the one place a client keeps things: into devtools, into a
        proxy's log, into a snapshot in somebody's test suite. The assert is on the
        literal code rather than on a field name, because a body that carried it under
        some other key would pass a name check and leak exactly as much.

        ``"token"`` is asserted separately and for a different reason: it is the one
        word a future field would be called, and this is the test that would notice.
        """
        response = ask(mail_client, as_mail_user())

        assert code_in(mailbox.latest) not in response.text
        assert "token" not in response.json()

    def test_no_session_is_refused_at_the_door(self, mail_client, mailbox):
        """401 ``MissingCredentialsError``, and the mail account is never reached.

        Both halves matter. The status is what a client acts on; the empty mailbox is
        what says this was refused by the *dependency* rather than by a use case that
        looked something up first - a request that cannot name the account it is about
        must not get as far as composing a message.
        """
        response = ask(mail_client, headers=None)

        assert response.status_code == 401
        assert response.json()["error"] == "MissingCredentialsError"
        assert mailbox.messages == []


class TestTheInstallWithoutMail:
    """The same route on an installation with no SMTP account, which is every fresh one."""

    def test_the_change_applies_at_once(self, client, as_user):
        """``applied``, and ``expires_at`` is *absent* rather than null.

        That absence is a decision made by ``response_model_exclude_none``, and it is
        the difference between a field a client must check and a field that cannot
        mislead: "here is your deadline" is not a sentence this response can say,
        because there is no request and therefore no deadline.
        """
        response = ask(client, as_user(ALICE))

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["status"] == "applied"
        assert body["email"] == NEW_ADDRESS
        assert "expires_at" not in body

    def test_the_address_really_moved(self, client, as_user):
        """The fallback is a real change, not a 201 that reassures and does nothing.

        Asserted by signing in, which is the only proof that counts: the new address
        resolves to the account and the old one no longer does. ``InvalidCredentialsError``
        rather than a 404 on the old address, because ``POST /sessions`` refuses an
        address it does not hold the same way it refuses a wrong password - the
        decision ``LogIn`` already made, unchanged by this feature.
        """
        ask(client, as_user(ALICE))

        assert client.post(
            "/sessions", json={"email": NEW_ADDRESS, "password": TEST_USER_PASSWORD}
        ).status_code == 201
        assert client.post(
            "/sessions", json={"email": ALICE, "password": TEST_USER_PASSWORD}
        ).status_code == 401

    def test_the_password_is_still_required(self, client, as_user):
        """**The fallback drops the mail proof and nothing else.**

        This is the test that keeps "an install without mail is less safe" from
        becoming "an install without mail has no door". The password is checked before
        the channel is consulted, so the branch that skips the mail is reached only by
        somebody who has already proved the account - and a wrong one is refused here
        exactly as it is on the configured application.
        """
        response = ask(client, as_user(ALICE), password="not-the-password")

        assert response.status_code == 401
        assert response.json()["error"] == "InvalidCredentialsError"


class TestWhatTheRequestRefuses:
    """Five refusals, five classes, and no mail sent for any of them."""

    def test_a_wrong_password_is_refused(self, mail_client, as_mail_user, mailbox):
        """The ``LogIn`` error, deliberately - one refusal for one mistake.

        A client that has already handled a failed login has handled this, which is
        the reason the class is shared rather than a new one being minted. The empty
        mailbox is the second half: the credential is checked before anything is
        composed, so a wrong password cannot send mail to an address the caller does
        not control.
        """
        response = ask(mail_client, as_mail_user(), password="not-the-password")

        assert response.status_code == 401
        assert response.json()["error"] == "InvalidCredentialsError"
        assert mailbox.messages == []

    def test_the_address_the_account_already_holds_is_refused(self, mail_client, as_mail_user):
        """409 ``EmailUnchangedError``, and it is not the same as unusable.

        The account here is at a perfectly *good* address, so the layer that answers
        is the one about the *change* being pointless rather than the one about the
        address being unusable. A stranded account asking for its own address gets
        this too, which is the distinction the application tests pin - the two
        refusals have different remedies and must not be collapsed.
        """
        response = ask(mail_client, as_mail_user(), email=ALICE)

        assert response.status_code == 409
        assert response.json()["error"] == "EmailUnchangedError"

    def test_somebody_elses_address_is_refused(self, mail_client, as_mail_user):
        """409 ``DuplicateEmailError``, the same class sign-up uses for the same clash."""
        as_mail_user(BOB)

        response = ask(mail_client, as_mail_user(), email=BOB)

        assert response.status_code == 409
        assert response.json()["error"] == "DuplicateEmailError"

    def test_an_address_no_provider_will_bill_is_refused(self, mail_client, as_mail_user, mailbox):
        """400 ``UnusableEmailError``, the entry rule this slice exists for.

        The status comes from the fall-through in ``errors._grade`` rather than from
        a list, and the argument for that is the pair it shares the grade with: "that
        is not an address" and "that address cannot work" are the same *kind* of
        answer - both are about the value that was typed - so they must not be two
        different statuses a client has to learn.

        Nothing is mailed, which is the half that would be easy to lose: an address
        that no provider will bill must not be handed a code, or the person would
        prove an address the system has already decided it cannot use.
        """
        response = ask(mail_client, as_mail_user(), email=STRANDED)

        assert response.status_code == 400
        assert response.json()["error"] == "UnusableEmailError"
        assert mailbox.messages == []

    def test_something_that_is_not_an_address_is_refused_as_a_shape_problem(self, mail_client, as_mail_user):
        """**The ordering assertion at the wire.**

        ``not-an-address`` breaks both rules at once - no ``@`` for the shape rule,
        and no dot after an ``@`` that is not there for the usability rule - so which
        one answers is decided by the order the use case asks them in. The order is
        load-bearing: this answer names something the person can act on, where
        ``UnusableEmailError`` names a provider's rule they did not break.
        """
        response = ask(mail_client, as_mail_user(), email="not-an-address")

        assert response.status_code == 400
        assert response.json()["error"] == "InvalidUserEmailError"

    def test_a_refusal_is_prose_as_well_as_a_name(self, mail_client, as_mail_user):
        """``detail`` is for a human, and it is never empty.

        ``errors._body`` sends both fields on every failure so a client never parses
        two shapes, and ``errors._detail`` falls back to the class name when the
        domain raised bare - which ``InvalidEmailChangeTokenError`` does on purpose.
        This is the one place that contract is asserted from outside.
        """
        body = ask(mail_client, as_mail_user(), email=STRANDED).json()

        assert set(body) == {"error", "detail"}
        assert body["detail"]


class TestAnsweringWithTheCode:
    """The confirm route, and the three refusals that have three different remedies."""

    def test_the_mailed_code_moves_the_account(self, mail_client, as_mail_user, mailbox):
        """**The whole loop over the wire, with the code read out of the envelope.**

        No headers on the confirm, and that is the point rather than an omission: the
        person who asked at a desk and opened the mail on a phone has the code and no
        session, and this is the request they make. The notice is asserted to have
        gone to the address that was *left*, which is the only warning that address
        will get if the change was not theirs.
        """
        ask(mail_client, as_mail_user())
        code = code_in(mailbox.latest)

        response = confirm(mail_client, code)

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["user"]["email"] == NEW_ADDRESS
        assert body["previous_email"] == ALICE
        assert body["notice_sent"] is True
        assert body["notice_error"] is None
        assert mailbox.latest.recipient == ALICE
        assert NEW_ADDRESS in mailbox.latest.body

    def test_a_nonsense_session_header_is_not_even_read(self, mail_client, as_mail_user, mailbox):
        """The other half of the token-only property, and the half that is easy to lose.

        "No session required" and "no session read" are different claims, and only the
        second survives a later change that adds a ``current_actor`` dependency for
        some plausible reason. A route that consulted one would answer 401 here, with
        a code that is perfectly good - so this is the test that would fail, and it
        would fail with a message about credentials rather than about tokens, which
        is what makes the mistake findable.
        """
        ask(mail_client, as_mail_user())
        code = code_in(mailbox.latest)

        response = confirm(mail_client, code, headers={"Authorization": "Bearer nonsense"})

        assert response.status_code == 201, response.text
        assert response.json()["user"]["email"] == NEW_ADDRESS

    def test_a_code_that_means_nothing_is_refused(self, mail_client):
        """401 ``InvalidEmailChangeTokenError``, which is the ``UNAUTHORIZED`` grade.

        The same status the missing header gets above, and the two are one answer to
        a client: you did not present something that identifies a change. The
        difference is in the ``error`` field, and the reason it is not a 404 is that
        nothing about this request is a URL that could be missing - the code is in the
        body, and a wrong one is a credential that did not work.
        """
        response = confirm(mail_client, "not-a-code")

        assert response.status_code == 401
        assert response.json()["error"] == "InvalidEmailChangeTokenError"

    def test_an_expired_code_is_refused(self, mail_client, as_mail_user, seed_change):
        """409 ``EmailChangeExpiredError``, from a row the window has moved past.

        Seeded rather than waited for - fifteen minutes of wall clock is not a thing a
        test does - and the seeded row is a *valid* row: issued by the same factory the
        use case uses, refused only because the claim compares its deadline against
        the clock. Which is what expiry is, and why nothing has to write the word
        ``expired`` anywhere.
        """
        headers = as_mail_user()
        token = seed_change(mail_client, headers, NEW_ADDRESS, age=timedelta(minutes=16))

        response = confirm(mail_client, token)

        assert response.status_code == 409
        assert response.json()["error"] == "EmailChangeExpiredError"

    def test_a_spent_code_is_refused_as_already_used(self, mail_client, as_mail_user, mailbox):
        """409 ``EmailChangeAlreadyUsedError``, which is reachable only because the row survives.

        The refusal is distinguishable from "no such code" precisely because the row
        is still there wearing ``CONFIRMED``, and the two have different remedies: a
        client told "already used" knows the change it is asking about has happened,
        and one told "unknown" knows nothing. Deleting the row on use would collapse
        them, which is ``ConfirmationAlreadyUsedError``'s argument for money.
        """
        headers = as_mail_user()
        ask(mail_client, headers)
        code = code_in(mailbox.latest)
        assert confirm(mail_client, code).status_code == 201

        response = confirm(mail_client, code)

        assert response.status_code == 409
        assert response.json()["error"] == "EmailChangeAlreadyUsedError"

    def test_an_address_taken_between_the_request_and_the_answer(self, mail_client, as_mail_user, mailbox):
        """**Spent by the attempt, not by the success** - the whole of it, twice.

        Fifteen minutes is long enough for somebody else to register the address, and
        when they do the change is refused and the code dies with it. The second
        request in this test is the half that is easy to get wrong: it must answer
        "already used" rather than the duplicate again, or a client retrying in a loop
        would be told the same true thing forever and never learn to ask for a
        different address. So the spend is committed *before* the refusal is raised -
        see ``ConfirmEmailChange`` - and this is where that shows.
        """
        headers = as_mail_user()
        ask(mail_client, headers)
        code = code_in(mailbox.latest)

        taken = mail_client.post(
            "/users", json={"email": NEW_ADDRESS, "password": TEST_USER_PASSWORD}
        )
        assert taken.status_code == 201, taken.text

        refused = confirm(mail_client, code)

        assert refused.status_code == 409
        assert refused.json()["error"] == "DuplicateEmailError"

        again = confirm(mail_client, code)
        assert again.status_code == 409
        assert again.json()["error"] == "EmailChangeAlreadyUsedError"


class TestTheNoticeIsReportedRatherThanRaised:
    """The one send in this system whose failure is a fact in the response."""

    def test_a_notice_that_cannot_be_sent_does_not_undo_the_change(
        self, mail_client, as_mail_user, mailbox
    ):
        """**The asymmetry with the request's mail, made visible.**

        The code-bearing message must arrive or the request is pointless, so a failure
        there fails the request. This one announces something already done, so
        refusing to move an account because a farewell bounced would strand the person
        the endpoint exists for - and a notice to a stranded address is *expected* to
        bounce. So the change stands, and ``notice_sent``/``notice_error`` report what
        happened instead of raising it.

        The channel is broken *after* the request, which is not a contrivance: the
        application builds a fresh channel per request, so this is one mail account
        failing later rather than a fake being rewired mid-test.
        """
        headers = as_mail_user()
        ask(mail_client, headers)
        code = code_in(mailbox.latest)
        mailbox.break_next_send(OSError("the mailbox is full"))

        response = confirm(mail_client, code)

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["user"]["email"] == NEW_ADDRESS
        assert body["notice_sent"] is False
        assert body["notice_error"] == "the mailbox is full"

    def test_an_install_without_mail_attempts_no_notice(self, client, as_user, seed_change):
        """``False`` with ``None``: the state that reads like a bug and is not one.

        Three states in two fields, and this is the third: no notice was *attempted*,
        because there is no mail account to attempt it with. The address moved either
        way - that is the best-effort decision - and a client that rendered this as
        "your old address could not be told" would be reporting a courtesy as a
        failure.

        Seeded rather than requested, and the reason is the fallback itself: with no
        mail account a request applies immediately and mints no row at all, so there
        is nothing to answer. This is the other route into the same state - a request
        made while the installation had mail, answered after the account was removed -
        and writing the row by hand is the only way to reach it.
        """
        token = seed_change(client, as_user(ALICE), NEW_ADDRESS)

        response = confirm(client, token)

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["user"]["email"] == NEW_ADDRESS
        assert body["previous_email"] == ALICE
        assert body["notice_sent"] is False
        assert body["notice_error"] is None


class TestTheRescue:
    """The account the whole slice was built for, end to end over HTTP."""

    def test_an_account_stranded_at_a_localhost_address_can_move(
        self, mail_client, legacy_account, as_mail_user, mailbox
    ):
        """**The finding, the rule, and the way out, in one test.**

        The account is at an address ``POST /users`` refuses today - so it is seeded
        below the rule, the way reality made it - and it is signed in to successfully,
        which is the "an entry rule, not a lookup rule" decision made visible. Before
        this endpoint existed its only remedy was a second account, and the address it
        held was taken forever by the ``UNIQUE`` on ``users.email``.

        The notice goes to the stranded address and is *sent* rather than bounced
        here, because the fake channel has no opinion about domains: what the test is
        pinning is that the system tries to tell the address it is leaving, not what a
        real mail server would do with it.
        """
        legacy_account(STRANDED)
        headers = as_mail_user(STRANDED, register=False)

        asked = ask(mail_client, headers)

        assert asked.status_code == 201, asked.text
        assert asked.json()["status"] == "pending"

        answered = confirm(mail_client, code_in(mailbox.latest))

        assert answered.status_code == 201, answered.text
        body = answered.json()
        assert body["user"]["email"] == NEW_ADDRESS
        assert body["previous_email"] == STRANDED
        assert body["notice_sent"] is True
        assert mailbox.latest.recipient == STRANDED

        # And the account is now reachable at an address a provider will bill, which
        # is the only outcome that matters: a rescue that left it unusable would be a
        # different trap wearing the same shape.
        assert mail_client.post(
            "/sessions", json={"email": NEW_ADDRESS, "password": TEST_USER_PASSWORD}
        ).status_code == 201
