"""The two messages an address change produces, and neither of them is queued.

**Why this is not in ``app.application.notifications.compose``**, which is where
every other message in the system is written. That module composes the
wallet-boundary notifications - a receipt for something that happened, a warning
about something about to - and every one of those is *queued*: it becomes a row,
it is drained by a tick, and the queue's whole premise is that late is acceptable.
A verification message is not that. It carries a credential that expires in fifteen
minutes, so it has to arrive now, and a queue that might deliver it after the
window closed would be a queue delivering a dead token. The two are the same shape
of text and the opposite shape of promise, so they live apart.

It is in the domain rather than beside the use case that sends it because it is
pure, and it is pure for ``compose.py``'s reason: no clock is read and no store is
opened, so the words are decided by the facts that caused them. The moment comes
from the ``EmailChange`` - its ``settled_at`` for the notice, its ``expires_at``
for the deadline - rather than from a second reading of the clock that could
disagree with the row.

**Neither message carries a link, and that is a decision rather than an
unfinished feature.** This system has no setting for its own public address, so any
URL here would be invented - right on one installation and wrong on every other,
and a promise no code keeps. What travels instead is the token and an instruction
for where to present it, which is true on every install including one that is not
on the internet at all.

**The verification message deliberately does not name the account's current
address.** It is sent to an address that has *not* been proven to belong to
anybody yet - proving that is the whole point of sending it - so putting the old
address in the body would hand it to whoever controls the new mailbox, whether or
not they are the account's owner. The person who asked already knows which account
they asked about; a stranger reading it learns only that somebody typed their
address into a form, which is the least this message can tell them.
"""

from dataclasses import dataclass

from .emailChange import EmailChange


@dataclass(frozen=True)
class EmailChangeMail:
    """One composed message, in the three fields a channel reads.

    Frozen because it is a *value*: composed once, handed to a channel, never
    edited. It satisfies ``Deliverable`` structurally rather than by inheriting
    from it - which is that Protocol's whole argument, and this is the third class
    to be passed to a channel. The first two are aggregates that happen to have
    these fields; this one is not an aggregate at all, which is what the Protocol's
    own reasoning predicted a third case would look like.
    """

    recipient: str
    subject: str
    body: str


def verification_message(change: EmailChange, token: str) -> EmailChangeMail:
    """The message that proves the new address is reachable, carrying ``token``.

    The token is the whole of the authorisation to apply the change, so this mail
    is a credential in transit - which is why the caller sends it synchronously and
    lets a failure propagate. A message that might not arrive is a request that
    might never be answerable, and the person is left waiting for a mail that is
    not coming.

    The deadline is printed from ``change.expires_at`` rather than from a constant,
    because what the reader needs is not "fifteen minutes" but *when* - they are
    holding a mailbox, not a stopwatch.
    """
    deadline = change.expires_at.isoformat(timespec="minutes")
    return EmailChangeMail(
        recipient=change.new_email,
        subject="Confirm your new email address",
        body=(
            f"An account asked to change its email address to this one.\n\n"
            f"If that was you, present this code to confirm it:\n\n"
            f"    {token}\n\n"
            f"The code can be used once, and expires at {deadline}.\n\n"
            f"At a terminal:\n"
            f"    budget-manager confirm-email\n\n"
            f"If you did not ask for this, nothing has happened and nothing will: "
            f"the change is not applied until the code above is presented. You can "
            f"ignore this message.\n"
        ),
    )


def address_changed_notice(change: EmailChange, previous_email: str) -> EmailChangeMail:
    """The message to the address the account is leaving behind.

    **This one is sent to an address that may already be unreachable**, and that is
    the case rather than an edge: the endpoint this exists beside was built to
    rescue an account stranded at an address no provider will bill, and a stranded
    address is exactly one that bounces. So the caller treats a failure here as a
    fact to report and never as a reason to refuse the change - refusing to move an
    account because a farewell could not be delivered would strand the person the
    feature is for.

    It goes to ``previous_email`` rather than to the account, and the direction is
    the point: the person who needs to know their address was moved is the one who
    can still read mail at the old one and not the new. If the change was not
    theirs, this is the only warning they will get.

    ``previous_email`` is passed in rather than read off the account, because by
    the time this is composed the account already holds the *new* address - see
    ``ConfirmEmailChange``, which remembers the old one before the change and this
    is the reason. It is also why the row carries no ``previous_email`` column:
    nothing else would read it, and the notice can be composed from what the caller
    already had in hand.
    """
    changed_at = change.settled_at
    return EmailChangeMail(
        recipient=previous_email,
        subject="Your account's email address was changed",
        body=(
            f"The email address on the account at {previous_email} was changed to "
            f"{change.new_email}"
            # ``settled_at`` is set by the claim's single UPDATE and the aggregate
            # refuses a settled row without it, so this is a fact rather than a
            # hopeful read - the same trust ``compose.payout_blocked`` places in
            # ``run.reason``.
            f" at {changed_at.isoformat(timespec='minutes')}.\n\n"
            f"If that was you, this is the last message this address will receive "
            f"about this account, and nothing needs doing.\n\n"
            f"If it was not you, then somebody else knew this account's password. "
            f"The change has already been applied and nothing in this system can "
            f"undo it for you - this address no longer names the account.\n"
        ),
    )
