from datetime import datetime
from uuid import uuid4

import pytest

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
