from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.identity.emailChange import EmailChange
from app.domain.identity.emailChangeMessage import (
    address_changed_notice,
    verification_message,
)
from app.domain.identity.emailChangeStatus import EmailChangeStatus
from app.domain.notifications.outboundMessage import OutboundMessage
from app.infrastructure.notifications import smtp_notification_channel
from app.infrastructure.notifications.smtp_notification_channel import (
    DEFAULT_TIMEOUT,
    SmtpNotificationChannel,
)

NOON = datetime(2026, 3, 2, 12, 0)
HALF_PAST_ELEVEN = datetime(2026, 3, 2, 11, 30)


class RecordingSmtp:
    """Stands in for ``smtplib.SMTP`` and writes down everything it was asked to do.

    The alternative to this is a real SMTP server, and the whole point of the
    ``NotificationChannel`` port is that no test needs one. What is being checked
    here is not that email works - Python's ``smtplib`` does email - but that
    this adapter composes the right message and performs its steps in an order
    that does not put a password on the wire in the clear.
    """

    instances: list["RecordingSmtp"] = []

    #: Set by a test to make every connection fail at send time.
    fail_on_send = False

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.events: list[str] = []
        self.message = None
        self.credentials = None
        self.closed = False
        RecordingSmtp.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.closed = True
        return False

    def starttls(self):
        self.events.append("starttls")

    def login(self, username, password):
        self.events.append("login")
        self.credentials = (username, password)

    def send_message(self, message):
        self.events.append("send_message")
        if self.fail_on_send:
            raise OSError("connection reset by peer")
        self.message = message


@pytest.fixture
def smtp(monkeypatch):
    """Replace ``smtplib.SMTP`` for the duration of one test, and record the calls."""
    RecordingSmtp.instances = []
    RecordingSmtp.fail_on_send = False
    monkeypatch.setattr(smtp_notification_channel.smtplib, "SMTP", RecordingSmtp)
    return RecordingSmtp


def build_message(**overrides):
    kwargs = dict(
        plan_id=uuid4(),
        due_at=NOON,
        recipient="chinedu@example.com",
        subject="Payout of 2000.00 NGN in 30 minutes",
        body="The plan 'salary' pays 2000.00 NGN at 2026-03-02T12:00.",
        created_at=HALF_PAST_ELEVEN,
    )
    kwargs.update(overrides)
    return OutboundMessage(**kwargs)


def build_channel(**overrides):
    kwargs = dict(
        host="smtp.example.com",
        port=587,
        sender="me@example.com",
        username="me@example.com",
        password="hunter2",
    )
    kwargs.update(overrides)
    return SmtpNotificationChannel(**kwargs)


class TestWhatGoesOnTheWire:
    def test_it_sends_from_the_configured_sender_to_the_messages_recipient(self, smtp):
        """The recipient comes from the message, not from the channel.

        That is what the recipient being stored on the row buys: a message
        queued under one address is delivered to that address, even if the
        configuration has moved on since.
        """
        build_channel(sender="alerts@example.com").send(
            build_message(recipient="chinedu@example.com")
        )

        message = smtp.instances[0].message
        assert message["From"] == "alerts@example.com"
        assert message["To"] == "chinedu@example.com"

    def test_it_carries_the_stored_subject_and_body_unchanged(self, smtp):
        build_channel().send(build_message(subject="s", body="b"))

        message = smtp.instances[0].message
        assert message["Subject"] == "s"
        assert message.get_content().strip() == "b"


class TestHowItConnects:
    def test_it_connects_to_the_configured_host_and_port(self, smtp):
        build_channel(host="mail.example.com", port=2525).send(build_message())

        assert (smtp.instances[0].host, smtp.instances[0].port) == (
            "mail.example.com",
            2525,
        )

    def test_it_always_sets_a_timeout(self, smtp):
        """Not optional, and this is the test that says so.

        Without one, a mail server that accepts the connection and then says
        nothing blocks the tick until the operating system's TCP timeout - which
        is measured in minutes and cannot be interrupted. The tick delivers
        synchronously, so that is a tick that hangs.
        """
        build_channel().send(build_message())

        assert smtp.instances[0].timeout == DEFAULT_TIMEOUT
        assert DEFAULT_TIMEOUT > 0

    def test_it_upgrades_the_connection_before_authenticating(self, smtp):
        """The ordering is the security property, so it is asserted directly.

        Logging in first and upgrading second would put the password on the wire
        in the clear, which is the exact thing STARTTLS exists to prevent.
        """
        build_channel().send(build_message())

        assert smtp.instances[0].events == ["starttls", "login", "send_message"]

    def test_it_skips_the_upgrade_when_told_to(self, smtp):
        """For a local test server with no certificate - a deliberate act."""
        build_channel(starttls=False).send(build_message())

        assert smtp.instances[0].events == ["login", "send_message"]

    def test_it_does_not_log_in_when_there_are_no_credentials(self, smtp):
        build_channel(username=None, password=None).send(build_message())

        assert smtp.instances[0].events == ["starttls", "send_message"]
        assert smtp.instances[0].credentials is None

    def test_it_closes_the_connection_when_it_is_done(self, smtp):
        build_channel().send(build_message())

        assert smtp.instances[0].closed is True


class TestWhenItFails:
    def test_the_failure_propagates(self, smtp):
        """Raising is the contract of the port, not an error path.

        The caller records the exception and retries, so the exception *is* the
        error report. A channel that swallowed its failures would leave the queue
        unable to tell "sent" from "not sent", and the row would be marked SENT
        by a message that never left.
        """
        smtp.fail_on_send = True

        with pytest.raises(OSError):
            build_channel().send(build_message())

    def test_the_connection_is_closed_even_when_the_send_raises(self, smtp):
        """``with`` is what guarantees this, and a leaked socket is worth avoiding."""
        smtp.fail_on_send = True

        with pytest.raises(OSError):
            build_channel().send(build_message())

        assert smtp.instances[0].closed is True


class TestTheThirdDeliverableShape:
    """A *composed* message through the same adapter, which is the control this file owes.

    ``Deliverable`` is a ``Protocol`` and deliberately not an ABC, so nothing at all
    enforces its three attributes - a channel that reached for a fourth would not
    fail at import, or at the point the class was written, but the first time the
    second *kind* of message was sent. ``deliverable.py`` names a test as the
    compensating control for that choice, and this class is it.

    What is being checked is not the wording of either message - ``compose``'s
    output is pinned beside it and ``EmailChangeMail``'s own module is where its
    text is argued. It is that the adapter, driven by a message that is not an
    aggregate at all, produces an envelope: the first two ``Deliverable`` classes
    are aggregates that happen to have these fields, and this one is a frozen value
    that has nothing else. If the port's contract really is three attributes, the
    two cases must be indistinguishable from in here.
    """

    def test_a_composed_message_goes_through_the_same_adapter(self, smtp):
        change, token = EmailChange.issue(
            user_id=uuid4(), new_email="ada.new@example.com", now=NOON
        )

        build_channel().send(verification_message(change, token))

        message = smtp.instances[0].message
        assert message["From"] == "me@example.com"
        assert message["To"] == "ada.new@example.com"
        assert message["Subject"] == "Confirm your new email address"
        # The credential really is in the body, which is the one thing about this
        # message that is not text: a composed message that reached the adapter
        # without its token would be a request nobody could answer.
        assert token in message.get_content()
        assert smtp.instances[0].closed is True

    def test_the_notice_goes_to_the_address_being_left(self, smtp):
        """The second message, and the direction is the whole of its purpose.

        Sent to the address the account is *leaving*, so the recipient a channel
        reads comes from the composed message rather than from the account - which
        is precisely the sort of thing a channel reaching for the wrong attribute
        would get wrong, and the reason both builders are driven through here.

        **The change is built settled rather than issued**, and that is the
        dependency this message has that the verification one does not: the notice
        prints the moment the change was answered, so it reads ``settled_at`` off
        the aggregate. A row only ever holds that field set once the claim's
        ``UPDATE`` has written it - ``EmailChange`` refuses a ``CONFIRMED`` row
        without one - so a notice is only ever composed for a change that a claim
        produced. Composing one from an issued request is not a state a caller can
        reach, and the field is the reason rather than an incidental detail of the
        text.
        """
        answered_at = NOON + timedelta(minutes=1)
        change = EmailChange(
            email_change_id=uuid4(),
            user_id=uuid4(),
            new_email="ada.new@example.com",
            token_hash="a hash, never read by this builder",
            status=EmailChangeStatus.CONFIRMED,
            requested_at=NOON,
            expires_at=NOON + timedelta(minutes=15),
            settled_at=answered_at,
        )

        build_channel().send(address_changed_notice(change, "ada@example.com"))

        message = smtp.instances[0].message
        assert message["To"] == "ada@example.com"
        assert "ada.new@example.com" in message.get_content()
        assert answered_at.isoformat(timespec="minutes") in message.get_content()

    def test_the_protocol_is_three_attributes_and_this_is_what_that_means(self, smtp):
        """**The absence, asserted, because the absence is the claim.**

        ``EmailChangeMail`` has no ``plan_id``, no ``due_at`` and no ``created_at``
        - an ``OutboundMessage`` is a queued row with a due date and this is a value
        that is sent the moment it is composed. A channel that had grown a
        dependency on any of the three would work for every test above this class
        and fail here, which is exactly the failure this file is meant to catch.
        """
        change, token = EmailChange.issue(
            user_id=uuid4(), new_email="ada.new@example.com", now=NOON
        )
        mail = verification_message(change, token)

        assert not hasattr(mail, "plan_id")
        assert not hasattr(mail, "due_at")
        assert not hasattr(mail, "created_at")

        build_channel().send(mail)

        assert smtp.instances[0].message["To"] == "ada.new@example.com"
