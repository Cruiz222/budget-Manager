"""The two messages a password reset produces, and neither of them is queued.

**Why this is not in ``app.application.notifications.compose``**, which is where
every other message in the system is written. That module composes the
wallet-boundary notifications - a receipt for something that happened, a warning
about something about to - and every one of those is *queued*: it becomes a row,
it is drained by a tick, and the queue's whole premise is that late is acceptable.
A reset code is not that. It carries a credential that expires in fifteen minutes,
so it has to arrive now, and a queue that might deliver it after the window closed
would be a queue delivering a dead code.

It is in the domain rather than beside the use case that sends it because it is
pure, and it is pure for ``compose.py``'s reason: no clock is read and no store is
opened, so the words are decided by the facts that caused them. The moment comes
from the ``PasswordReset`` - its ``settled_at`` for the notice, its ``expires_at``
for the deadline - rather than from a second reading of the clock that could
disagree with the row.

**Neither message carries a link, and that is a decision rather than an unfinished
feature.** This system has no setting for its own public address, so any URL here
would be invented - right on one installation and wrong on every other, and a
promise no code keeps. What travels instead is the code and an instruction for
where to present it, which is true on every install including one that is not on
the internet at all.

**Neither message names the account's address, and here that is a weaker argument
than it is for a change.** ``verification_message`` is addressed to an address
that has *not* been proven to belong to anybody, so naming the account in it would
hand the old address to whoever controls the new mailbox. This one goes to the
address the account already holds, so there is nothing to protect from its reader -
they hold the account's mail. The reason it is still left out is narrower and
honest: an address that is forwarded, or a mailbox shared by a household, is not
the same thing as a reader who is the account's owner, and a message that names
the account tells that reader which account to attack rather than merely that
somebody typed an address into a form. The practice matches the sibling message;
the reasoning does not, which is worth writing down rather than assuming the two
agree for the same reason.
"""

from dataclasses import dataclass

from .passwordReset import PasswordReset


@dataclass(frozen=True)
class PasswordResetMail:
    """One composed message, in the three fields a channel reads.

    Frozen because it is a *value*: composed once, handed to a channel, never
    edited. It satisfies ``Deliverable`` structurally rather than by inheriting
    from it - which is that Protocol's whole argument, and this is the fourth class
    to be passed to a channel. The first two are aggregates that happen to have
    these fields, the third is ``EmailChangeMail``, and like that one this is not
    an aggregate at all.
    """

    recipient: str
    subject: str
    body: str


def reset_message(reset: PasswordReset, token: str, email: str) -> PasswordResetMail:
    """The message that proves control of the address, carrying ``token``.

    The token is the whole of the authorisation to replace the account's password,
    so this mail is a credential in transit - which is why the caller sends it
    synchronously and lets a failure propagate. A message that might not arrive is
    a reset that might never be answerable, and the person is left waiting for a
    mail that is not coming while their account stays locked.

    ``email`` is passed in because the row does not carry it and must not: the
    address is a fact about the *user*, and a copy of it on this table would be a
    second record free to disagree with ``users.email``. It is the recipient, so
    the caller has it in hand already - see ``RequestPasswordReset``.

    The deadline is printed from ``reset.expires_at`` rather than from a constant,
    because what the reader needs is not "fifteen minutes" but *when* - they are
    holding a mailbox, not a stopwatch.

    **The wording is careful about what has and has not happened**, and the
    distinction is not decoration. A reset code that is merely mailed changes
    nothing: the old password still works, the sessions are still live, and the
    account is exactly as it was. A person who reads this mail and concludes
    otherwise would report a compromise that has not occurred, so the last
    paragraph says so in the plainest terms available.
    """
    deadline = reset.expires_at.isoformat(timespec="minutes")
    return PasswordResetMail(
        recipient=email,
        subject="Reset your password",
        body=(
            f"Somebody asked to set a new password for an account that reads mail "
            f"at this address.\n\n"
            f"If that was you, present this code to choose a new password:\n\n"
            f"    {token}\n\n"
            f"The code can be used once, and expires at {deadline}.\n\n"
            f"At a terminal:\n"
            f"    budget-manager confirm-password-reset\n\n"
            f"If you did not ask for this, nothing has happened and nothing will: "
            f"the account's current password still works and no session has been "
            f"ended. You can ignore this message.\n"
        ),
    )


def password_changed_notice(
    reset: PasswordReset, email: str
) -> PasswordResetMail:
    """The message sent to the account once its password has actually been replaced.

    **This is the only warning a person gets that somebody else set their
    password**, which makes it the more important of the two mails rather than the
    courtesy the address change's notice is. The reset has already been applied and
    nothing in this system can undo it - so, exactly as with that notice, a failure
    here is a fact to report and never a reason to refuse. Refusing to apply a reset
    because the warning about it bounced would strand the person the endpoint exists
    for, and leave the password unchanged while the code that would have changed it
    was spent.

    It goes to ``email``, the address the account holds, because that is the only
    address there is - there is no "address left behind" in this flow, so this is
    not a notice *about* a move. It is addressed to the account whose secret
    changed, and the actionable fact is the one it states second: the sessions were
    ended, so anybody signed in as this account has just been signed out.

    ``settled_at`` is set by the claim's single UPDATE and the aggregate refuses a
    settled row without it, so the moment below is a fact rather than a hopeful
    read - the same trust ``address_changed_notice`` places in it.

    The count of ended sessions is deliberately not printed. It is reported to the
    client, where a person is looking at their own screen, and putting "three
    sessions were ended" in a mail would tell a reader who did not ask for this how
    many devices the account was signed in on.
    """
    changed_at = reset.settled_at
    return PasswordResetMail(
        recipient=email,
        subject="Your password was changed",
        body=(
            f"The password on this account was changed at "
            f"{changed_at.isoformat(timespec='minutes')}.\n\n"
            f"Every device that was signed in to this account has been signed out, "
            f"so anything that was holding a session will need the new password.\n\n"
            f"If that was you, nothing needs doing.\n\n"
            f"If it was not you, somebody else was able to read mail sent to this "
            f"address. The password has already been changed and the sessions are "
            f"already ended, so what is left is to reset it again yourself and to "
            f"look at whatever else can read this mailbox.\n"
        ),
    )
