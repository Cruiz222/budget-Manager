"""Asking to move an account's address, and proving the password to be allowed to.

The request half of the change flow. Nothing about the account moves here - what
leaves is either a finished change (on an install with no mail account, see
below) or a token in somebody's mailbox.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.application.unit_of_work import UnitOfWorkFactory
from app.domain.identity.emailAddress import refuse_unusable_email
from app.domain.identity.emailChange import EmailChange
from app.domain.identity.emailChangeMessage import verification_message
from app.domain.identity.exception import (
    DuplicateEmailError,
    EmailUnchangedError,
    InvalidCredentialsError,
)
from app.domain.identity.password import PlainPassword
from app.domain.identity.password_hasher import PasswordHasher
from app.domain.identity.user import User, checked_email
from app.domain.notifications.notificationChannel import NotificationChannel


@dataclass(frozen=True)
class EmailChangeOutcome:
    """What asking produced: either a finished change or a request awaiting proof.

    **The two outcomes are one field apart, and the coupling is structural rather
    than checked.** A change either happened or it did not, and the only other
    fact worth reporting hangs off which: a pending change has a deadline, and an
    applied one has nothing to wait for. So ``change`` is present exactly when the
    change is *pending*, and ``applied`` and ``expires_at`` are both read off it -
    which makes "applied, and here is your deadline" unrepresentable rather than
    refused. ``ProviderAnswer`` enforces the same sort of pairing with a
    ``__post_init__``; it has to, because its two fields are genuinely
    independent facts a caller assembles. Here one fact causes the other, so
    there is nothing to assemble and nothing to get wrong.

    ``user`` is the account as it stands *after* this call, which for a pending
    change is still the old address - the account does not move until the token
    comes back. See ``email`` for which of the two addresses that makes this.
    """

    user: User
    change: EmailChange | None = None

    @property
    def applied(self) -> bool:
        """Whether the account now holds the new address.

        ``True`` only on an install with no mail account, where the password proof
        is the whole of the authorisation because there is no mailbox to prove
        anything with. See ``RequestEmailChange``.
        """
        return self.change is None

    @property
    def email(self) -> str:
        """The address the account holds now, or will hold once confirmed.

        One property rather than two fields, because the caller that reports this
        means the same thing either way: "the address this request is about". A
        client that has to know *which* of the two it is asking about reads
        ``applied`` and asks the right question.
        """
        return self.user.email if self.change is None else self.change.new_email

    @property
    def expires_at(self) -> datetime | None:
        """When the token stops working, or ``None`` if there is no token."""
        return None if self.change is None else self.change.expires_at


class RequestEmailChange:
    """Move an account to a new address, once its password has been proved again.

    **The authorisation is a session *and* the current password**, and requiring
    both is the point rather than belt-and-braces. ``ConfirmEmailChange`` is
    authorised by the mailed token alone - it has to be, or a person who asked on
    a laptop could not answer from a phone - so the token is the *only* thing
    standing between a stolen session and an account being walked off with. Asking
    for the password here means a token that was minted was minted by somebody who
    knew the secret, and not merely by somebody who was holding a session.

    **The wrong-password refusal is ``LogIn``'s error, deliberately.** A password
    checked in two places should fail in one way, or the two places will be
    distinguishable from outside by a caller who should not be able to tell them
    apart - and the sentence a person sees ("those details did not match an
    account") is the one they already know from the login form. A password that
    could not be one at all still gets ``PlainPassword``'s own refusal, which is a
    400 naming the length policy, exactly as at sign-up.

    **With no mail account configured, the change is applied here and now.** This
    is the fallback the whole slice depends on: the endpoint exists to rescue an
    account stranded at an address no provider will bill, and an install with no
    SMTP configured - which is every install before somebody sets one up - must
    still be able to fix one. Refusing instead would move the trap down a layer
    and tell the person to check a mailbox that no code in this system can post
    to. The outcome says ``applied`` in so many words, so an install without mail
    is never *silently* less safe than one with it.

    **The token is minted, stored and committed before it is mailed**, and the
    order is the whole of the safety here. A message that left before its row was
    durable would be a token for a request that does not exist - the person
    presents it, the claim finds nothing, and the request they made has vanished
    with no way to tell that from having typed the wrong code. Storing first means
    the worst case is the opposite one: a row nobody has the token for, which is a
    request that simply expires. That is also why the send is *outside* the unit
    rather than inside it, and why the token never appears in the outcome: it
    leaves this method by exactly one route, into one envelope.

    **A failed send is a refusal, not a partial success.** The mail carries the
    credential, so a request whose mail did not go out is a request nobody can
    answer - the person would sit waiting for something that is not coming. So the
    channel's exception propagates, the caller sees a failure, and the harmless
    leftover row is superseded the next time they ask. Contrast
    ``ConfirmEmailChange``, where the mail that *can* fail is one nobody is waiting
    for.
    """

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        password_hasher: PasswordHasher,
        actor: UUID,
        channel: NotificationChannel | None = None,
    ):
        """Bind the acting account, the hasher that checks its password, and a way out.

        ``channel`` is the *resolved* channel rather than mail settings, which is
        the arrangement ``DeliverPendingMessages`` has and for its reason: the
        composition root is the only place that knows how to build an SMTP
        adapter, and a use case that did its own construction would be a second
        place the adapter's arguments could be got wrong. ``None`` is a state
        rather than a failure - it is how "this installation has no mail account"
        reaches this method as something it can act on.
        """
        self._unit_of_work_factory = unit_of_work_factory
        self._password_hasher = password_hasher
        self._actor = actor
        self._channel = channel

    def execute(self, new_email: str, password: str, now: datetime) -> EmailChangeOutcome:
        """Ask to move this account to ``new_email``, proved by ``password``.

        The refusals, in the order they are reachable, and each order is a
        decision:

        - **``InvalidCredentialsError``** - no credential, or a password that does
          not match. First, because nothing else here should be answerable by
          somebody who cannot prove the account is theirs: a caller who fails this
          learns nothing about which addresses are taken.
        - **``InvalidUserEmailError``** - the new address has no ``@``, or is
          empty. The aggregate's own shape rule, applied through the same function
          ``User`` calls. First of the two address rules so that
          ``not-an-address`` is refused as *not an address* rather than as
          unusable; see ``refuse_unusable_email``.
        - **``EmailUnchangedError``** - the account already holds this address.
          Checked against the *folded* value, which is why it comes after the
          shape rule rather than before it: ``Ada@Example.com`` with a trailing
          space is the same address as ``ada@example.com``, and comparing the raw
          string would report a change to itself as a change.
        - **``UnusableEmailError``** - the address has no dot in its domain, so no
          provider will bill it. This is the entry rule, and the one case where it
          is *not* checked is this account's own address, because the account
          already holds it - which is exactly what the unchanged refusal above
          catches first. A stranded account cannot ask to stay stranded.
        - **``DuplicateEmailError``** - somebody else holds the address. Last,
          because it is the only one of the five that answers a question about the
          *store* rather than about the value that was typed.

        ``now`` is passed in for the reason ``Session.issue`` takes it, and it is
        the single moment this request is made: both the row it writes and the
        deadline it reports are counted from it.
        """
        uow = self._unit_of_work_factory.start()
        try:
            credential = uow.password_credentials.find_by_user_id(self._actor)
            if credential is None or not self._password_hasher.verify(
                PlainPassword(password), credential.password_hash
            ):
                raise InvalidCredentialsError("those details did not match an account")

            user = uow.users.get_by_id(self._actor)

            email = checked_email(new_email)

            if email == user.email:
                raise EmailUnchangedError(
                    f"{email} is already this account's address"
                )

            refuse_unusable_email(email)

            holder = uow.users.find_by_email(email)
            if holder is not None and holder.user_id != user.user_id:
                raise DuplicateEmailError(f"{email} is already registered")

            if self._channel is None:
                # No mailbox to prove anything with, so the password proof above is
                # the whole of the authorisation. See the class docstring.
                user.change_email(email)
                uow.users.save(user)
                uow.commit()
                return EmailChangeOutcome(user=user)

            change, token = EmailChange.issue(
                user_id=self._actor, new_email=email, now=now
            )
            # The supersede, and it is the primary key doing it rather than a
            # delete-then-insert: asking twice leaves one row, the second token
            # live and the first dead. See ``EmailChangeRepository.save``.
            uow.email_changes.save(change)
            uow.commit()
        finally:
            uow.rollback()

        # Outside the unit, on purpose and in this order: the token must be
        # durable before it is put on a wire, so that a send which succeeds and a
        # commit which then failed is not a state that can exist. A failure here
        # propagates - see the class docstring - and leaves only the row above,
        # which holds a hash of a token nobody has.
        self._channel.send(verification_message(change, token))
        return EmailChangeOutcome(user=user, change=change)
