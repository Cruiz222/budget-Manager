"""What an SMS channel must do - the second outbound port.

Read with ``notificationChannel``, because the two are one design stated twice for
two media. Everything that port says about *direction* - a repository is something
the application asks for data, a channel is something it tells to act - and
everything it says about **raising being the interface rather than an error path**
applies here without amendment, and is not repeated here on purpose: two copies of
a rule are two things to keep in agreement, which is a cost this project has
already paid once.

**A second port rather than the first one reused**, and that is the question this
file exists to answer. ``Deliverable`` requires three fields - recipient, subject,
body - and an SMS has no subject. Handing a mail port ``subject=""`` to satisfy the
annotation would be inventing a field to fit a shape, which is exactly the failure
mode that Protocol was built to avoid: it asks for what a channel *needs*, and what
a text message has is two fields. Widening ``Deliverable`` to make ``subject``
optional would instead loosen the mail port for a reason that has nothing to do
with mail, and would put a ``None`` check in the SMTP adapter to handle a case a
letter cannot produce.

**An ABC here, where ``Deliverable`` is a ``Protocol``, and the pair is not an
inconsistency.** ``Deliverable`` is a Protocol because the records that get mailed
are unrelated to each other and sharing a base class would invent a relationship
they do not have. A channel is the opposite case: every implementation of this is
an SMS channel, and ``TermiiSmsChannel`` saying so in its bases is information -
the same reason ``NotificationChannel`` is an ABC, and every repository beside it.

**What the medium does not change is how failure is reported, and what it changes
is what the caller owes.** A send that raises is a send that did not happen. A
queued warning treats that as ordinary - the failure is recorded and the next tick
retries - and a one-time code cannot, because a code nobody received is a request
nobody can answer, so its caller refuses the request outright. Both are the
caller's decision and neither is this port's; what the port owes both is that
returning normally means the text was accepted for delivery.
"""

from abc import ABC, abstractmethod

from app.domain.notifications.smsMessage import SmsMessage


class SmsChannel(ABC):
    """Defines what an SMS channel must do - the outbound port for texts."""

    @abstractmethod
    def send(self, message: SmsMessage) -> None:
        """Deliver one text, or raise.

        Returning normally means the channel accepted responsibility for the
        message; raising means it did not, and never means the message is lost.
        See ``NotificationChannel.send``, whose contract this is word for word -
        deliberately, because a caller that had to remember two conventions would
        eventually apply the wrong one, and the difference between them is
        whether a failure is recorded or refused.
        """
        pass
