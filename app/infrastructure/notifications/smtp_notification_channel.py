"""SMTP delivery: the one adapter that puts a message on the wire.

Standard library only - ``smtplib`` and ``email.message`` - so this feature adds
no dependency to a project whose ``requirements.txt`` is still a single line.
That is not a coincidence of the choice of email; it is most of the reason email
was the right first channel.
"""

import smtplib
from email.message import EmailMessage

from app.domain.notifications.notificationChannel import NotificationChannel
from app.domain.notifications.outboundMessage import OutboundMessage

#: How long any single socket operation may take, in seconds.
#:
#: **Not optional, and not a tuning knob.** Without a timeout a mail server that
#: accepts the connection and then says nothing leaves ``send`` blocked until the
#: operating system's TCP timeout - which is measured in minutes, and which the
#: process cannot interrupt. Since the tick delivers synchronously, that would
#: mean a tick that hangs for minutes on a wedged server. With a timeout the
#: failure is an ordinary exception, it is recorded, and the next tick tries
#: again.
#:
#: Ten seconds is chosen against the deadline it can eat into. Delivery runs in
#: the 30-minute window between the warning and the payout, so even several
#: consecutive timeouts leave the window intact by a wide margin.
DEFAULT_TIMEOUT = 10


class SmtpNotificationChannel(NotificationChannel):
    """Deliver messages over SMTP.

    Raises on every failure rather than returning a status, because that is the
    contract the port states: the caller records the exception and retries, so
    the exception *is* the error report. There is no partially-successful send to
    model - a message is either accepted by the server or it is not.
    """

    def __init__(
        self,
        host: str,
        port: int,
        sender: str,
        username: str | None = None,
        password: str | None = None,
        *,
        starttls: bool = True,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self._host = host
        self._port = port
        self._sender = sender
        self._username = username
        self._password = password
        self._starttls = starttls
        self._timeout = timeout

    def send(self, message: OutboundMessage) -> None:
        """Hand one message to the mail server.

        The connection is opened and closed per message, which is a little
        wasteful and entirely deliberate. A long-lived connection would have to
        survive between ticks, which means a daemon, which is the thing the whole
        design avoids - the tick is short-lived on purpose, so that when it
        breaks there is nothing left running to be confused by.

        ``with`` closes the connection even when the send raises, so a failed
        delivery cannot leak a socket into the next one.
        """
        email = EmailMessage()
        email["From"] = self._sender
        email["To"] = message.recipient
        email["Subject"] = message.subject
        email.set_content(message.body)

        with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as smtp:
            if self._starttls:
                # Upgrade before anything sensitive is sent - in particular
                # before ``login``. Ordering matters: authenticating first and
                # upgrading second would put the password on the wire in the
                # clear, which is the exact thing STARTTLS exists to prevent.
                smtp.starttls()
            if self._username is not None:
                smtp.login(self._username, self._password)
            smtp.send_message(email)
