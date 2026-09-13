"""Answering a change: the mailed token comes back, and the account moves.

The second half of the flow, and the only operation in this system whose whole
authorisation is a value this system posted to somebody.
"""

from dataclasses import dataclass
from datetime import datetime

from app.application.unit_of_work import UnitOfWorkFactory
from app.domain.identity.emailChange import EmailChange
from app.domain.identity.emailChangeMessage import address_changed_notice
from app.domain.identity.exception import (
    DuplicateEmailError,
    InvalidEmailChangeTokenError,
    UserNotFoundError,
)
from app.domain.identity.session import hash_session_token
from app.domain.identity.user import User
from app.domain.notifications.notificationChannel import NotificationChannel


@dataclass(frozen=True)
class ConfirmedEmailChange:
    """What answering produced: the account, and how telling the old address went.

    ``previous_email`` is carried rather than read off anything, because by the
    time this exists the account already holds the new address and the row
    deliberately has no ``previous_email`` column - see ``address_changed_notice``
    for why the notice can be composed without one, and why a column written and
    never read is the speculative state this codebase refuses.

    **``notice_sent`` and ``notice_error`` are three states in two fields**, and
    the third is worth naming because it is the one a reader will misread:

    - ``True``, ``None`` - the address that was left behind was told.
    - ``False``, a reason - a channel exists and refused. The change still
      happened; that is the whole point of catching it here.
    - ``False``, ``None`` - there is no mail account on this installation, so
      nothing was attempted.

    ``True`` with a reason cannot be constructed honestly, and it is the one
    combination that would matter - "we told them, and here is why we could not" is
    not a thing that can happen. It is left unguarded because it is only ever a
    wrong *report*, never a wrong *state*: the address moved, and this field is
    commentary on a courtesy. The pair that decides what a client believes about
    its account is ``EmailChangeOutcome``'s, and that one is structural.
    """

    user: User
    change: EmailChange
    previous_email: str
    notice_sent: bool
    notice_error: str | None = None


class ConfirmEmailChange:
    """Apply a requested address change, authorised by the token alone.

    **No actor, and this is the second operation in the system with none.** The
    first is ``LogOut``, and the resemblance is not a coincidence: both are
    authorised by *the thing they act on*, because possession is the proof. Here
    that is stronger than it sounds. The token exists only because somebody
    already presented the account's password to ``RequestEmailChange``, so the
    password proof is already spent on this change - requiring a live session on
    top would not add a check, it would add a way to fail. It would refuse exactly
    the person who asked at a desk and opened the mail on a phone, and it would
    leave the link dead if their session lapsed inside the fifteen minutes.

    **This is not a hole in the "no privileged actor" rule**, and the distinction
    is worth making precisely because it looks like one. Nothing here is
    *privileged*: the token names one account and can move that one account, and
    it was minted by that account's own password. What the rule forbids is a code
    path that reaches a wallet without naming whose it is; this path names whose
    it is by proving it, and takes nothing else on trust. The user id it finally
    reads is one a claimed row carried - never one a request supplied.

    **The change and the spend commit together.** ``email_changes`` is on the
    ``UnitOfWork`` for this exact reason: a crash between the two writes would
    leave either a token that still works after it has moved an account, or an
    account moved by a request that still reads ``AWAITING`` so the same token can
    be presented again. One commit, one fact.

    **What is re-checked here is only what can have changed.** The address may have
    been taken by somebody else during the window, so the duplicate lookup runs
    again at apply time. The *usability* rule is not re-checked, because it cannot
    change inside one run of one version of this code - and a second check would
    only be a second place for the two to disagree.
    """

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        channel: NotificationChannel | None = None,
    ):
        """Bind a way to tell the old address, if this install has one.

        No hasher and no actor, following ``LogOut``: the token is hashed with
        ``hash_session_token``, which is a plain function in the domain rather than
        a port with anything to configure, and there is nobody to be told about
        until a token has named them.
        """
        self._unit_of_work_factory = unit_of_work_factory
        self._channel = channel

    def execute(self, token: str, now: datetime) -> ConfirmedEmailChange:
        """Apply the change ``token`` names, and tell the address being left.

        Raises whatever the claim raises - ``InvalidEmailChangeTokenError``,
        ``EmailChangeAlreadyUsedError``, ``EmailChangeExpiredError`` - and
        ``DuplicateEmailError`` if the address was taken in the meantime. The
        duplicate case is the interesting one: **the request is spent by the
        attempt, not by the success**, so that refusal commits the claim before it
        raises. A token that survived a failed attempt would be a live credential
        whose whole purpose is to be usable once, and the remedy for the person is
        the same either way - ask again.

        ``now`` is passed in for the reason ``Session.issue`` takes it, and it is
        the single instant this is decided against: the claim's window test, the
        moment recorded on the row, and the moment printed in the notice all come
        from it, so a change cannot be recorded as answered before it was allowed
        to be.
        """
        uow = self._unit_of_work_factory.start()
        try:
            # The gate. Everything below this line is reached only by a token that
            # was mailed to the address being moved to, and this is the statement
            # that spends it - so a second confirm arriving in the same instant
            # finds nothing to change rather than applying twice.
            change = uow.email_changes.claim_by_token_hash(
                hash_session_token(token), now
            )

            try:
                user = uow.users.get_by_id(change.user_id)
            except UserNotFoundError:
                # The account was deleted between asking and answering, so there is
                # no change to apply and nothing a retry would do differently. It is
                # reported as the token's own refusal rather than as a missing user
                # for the reason ``InvalidSessionError`` gives: a caller who could
                # tell "no such token" from "that account is gone" has an oracle,
                # and the remedy is identical.
                raise InvalidEmailChangeTokenError(
                    "that confirmation code cannot be used"
                )

            holder = uow.users.find_by_email(change.new_email)
            if holder is not None and holder.user_id != user.user_id:
                # The address went while this request waited, which is the one thing
                # that genuinely can change inside the window. The claim above is
                # committed before the refusal so that the token really is spent -
                # see the docstring. ``rollback`` in the ``finally`` below would
                # otherwise undo it, and "spent by the attempt" would be a sentence
                # in a docstring rather than something the store does.
                uow.commit()
                raise DuplicateEmailError(
                    f"{change.new_email} was registered while this change was "
                    f"pending; ask again with another address"
                )

            # Remembered before the account moves, because afterwards there is
            # nothing left to remember it from.
            previous_email = user.email
            user.change_email(change.new_email)
            uow.users.save(user)
            uow.commit()
        finally:
            uow.rollback()

        return ConfirmedEmailChange(
            user=user,
            change=change,
            previous_email=previous_email,
            **self._tell_previous_address(change, previous_email),
        )

    def _tell_previous_address(
        self, change: EmailChange, previous_email: str
    ) -> dict:
        """Send the notice, and turn a failure into something to report.

        **Best-effort on purpose, and the asymmetry with the request's mail is the
        whole argument.** That one carries the credential, so a failure means
        nobody can answer and it raises. This one announces something that has
        already happened and that nothing in this system can undo - so refusing
        the change because a farewell bounced would strand the very account the
        endpoint exists to rescue. A notice to a stranded address is *expected* to
        bounce; that is what stranded means.

        Returns the two fields rather than the dataclass, so this method has one
        caller and one job, and so the caller's ``return`` reads as the single
        place a ``ConfirmedEmailChange`` is built.
        """
        if self._channel is None:
            return {"notice_sent": False, "notice_error": None}

        try:
            self._channel.send(address_changed_notice(change, previous_email))
        except Exception as error:
            # Broad on purpose, and this is the one place in the codebase that
            # catches one. A channel is third-party code talking to a network, and
            # every failure mode it has - refused connection, authentication, a
            # mailbox that does not exist - means the same thing here: the notice
            # did not arrive. Narrowing this to the adapter's declared exception
            # would let an undeclared one become a 500 *after* the address had
            # already moved, which reports a change that happened as a failure.
            return {
                "notice_sent": False,
                "notice_error": str(error) or type(error).__name__,
            }

        return {"notice_sent": True, "notice_error": None}
