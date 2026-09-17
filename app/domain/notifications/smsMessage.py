"""What an SMS channel needs from a message - which is one field fewer than mail.

A text message has a destination and words. It has no subject line, because there
is no such field in the medium and no provider takes one. So this is
``Deliverable`` with the middle field removed, and the removal is the whole reason
this file exists rather than a ``subject=""`` being passed to the mail port.

**Why a second ``Protocol`` rather than a widened first one.** ``Deliverable``
asks for three fields because a mail channel needs three, and it is a ``Protocol``
precisely so that it can ask for the *shape* it needs rather than impose a family
on record types with nothing else in common - see its docstring, which argues that
at length and names ``EmailChangeMail`` as the third case it predicted. The same
argument run one channel over gives the same answer: asking an SMS for a subject
would be inventing a field to satisfy an annotation, and it would make the SMTP
adapter's subject handling read as a rule about messages rather than as a fact
about mail. Loosening ``Deliverable`` instead would push a ``None`` check into
every mail channel to handle a case a letter cannot produce.

**A ``Protocol`` here, and the ABC is next door.** ``SmsChannel`` is an ABC
because every implementation of it genuinely *is* an SMS channel - that is a real
family, and inheriting from the port is information. This is not a family: the
records that get texted are as unrelated to one another as ``OutboundMessage``
and ``Notification`` are, and the one this slice adds - a one-time code - is
neither an aggregate nor stored. Same split as the mail pair, for the same
reasons, which is why the two ports read as deliberate parallels rather than as
two designs that happened to land near each other.

The cost is the one ``Deliverable`` names for itself: a typo'd attribute name is
not caught until the send, where an ABC would have caught it at import.
"""

from typing import Protocol


class SmsMessage(Protocol):
    """One composed text, as a delivery channel sees it.

    Both fields are plain strings, and the reason is the same for each: the words
    are frozen when the message is composed and the destination is captured then
    too. A channel does not parse a destination, wrap a body or hold either as
    anything but text - it types two values into a request.

    ``recipient`` is expected to be **already folded** - digits, international,
    with no leading ``+`` and no national trunk ``0`` - because that is what every
    SMS API accepts and what ``app.domain.identity.phoneNumber.fold_phone``
    produces. It is a plain ``str`` rather than a type of its own, deliberately: a
    "folded recipient" type would have to be built by whatever composes a message,
    which would either duplicate the fold or force a cast at the one call site that
    has the right value. The agreement is written down by the producer and enforced
    where it can be, by the channel that would otherwise be the one to send a text
    to the wrong person - see ``TermiiSmsChannel.send``.
    """

    recipient: str
    body: str
