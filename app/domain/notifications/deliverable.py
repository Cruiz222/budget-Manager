"""What a delivery channel needs from a message - and nothing more.

A channel reads exactly three things: who to send to, the subject line, and the
words. It has never read anything else, and it must never need to: a channel that
knew about plan ids or attempt counts would have an opinion about *what* it is
delivering, when its whole job is to put bytes on a wire.

Both message aggregates happen to have those three fields, so this is the shape
of the seam between them and the outside world, written down once.

**A Protocol, and this is the one place in the codebase where that is the right
tool.** Everywhere else the domain defines an interface with an ABC and the
implementations inherit from it - because there, the interface *is* a
relationship: a ``SqliteWalletRepository`` genuinely is a ``WalletRepository``,
and saying so in its bases is information. Here it would be a lie of a different
kind. ``OutboundMessage`` and ``Notification`` are not two implementations of one
thing; they are two unrelated records that happen to be deliverable, and forcing
them to share a base class would invent an inheritance relationship to describe a
coincidence. Worse, it would tie two independently evolving aggregates to one
parent, so that adding a field for a delivery reason to the parent would change
both.

So the port asks for the *shape* rather than for a family. Anything with a
recipient, a subject and a body can be sent, and neither aggregate has to know
this file exists. Python was already doing this at runtime - the SMTP adapter
would have accepted a ``Notification`` all along, and the only thing that said
otherwise was an annotation - so this makes the existing truth legible instead of
changing it.

The cost is worth naming, because it is real: a typo'd attribute name is not
caught until the send, where an ABC would have caught it at import. The
compensating control is that only two classes are ever passed to a channel, and
both are tested against the real adapter's field usage.
"""

from typing import Protocol


class Deliverable(Protocol):
    """One composed message, as a delivery channel sees it.

    All three are plain strings, and the reason is the same for each: the words
    are frozen at enqueue, the recipient is captured then too, and what a channel
    does with them is entirely mechanical. No channel needs to parse a recipient,
    reformat a subject or hold the body as anything but text.
    """

    recipient: str
    subject: str
    body: str
