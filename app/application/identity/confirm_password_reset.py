"""Answering a reset: the mailed code comes back, and the account gets a new password.

The second half of the flow, and the only operation in this system that replaces
the secret an account is proved by. It is authorised by the token alone - the same
shape ``ConfirmEmailChange`` has, and for the same reason: the code exists only
because somebody could read the account's mail, and requiring a live session on top
would not add a check, it would add a way to fail.
"""

from dataclasses import dataclass
from datetime import datetime

from app.application.unit_of_work import UnitOfWorkFactory
from app.domain.identity.exception import (
    InvalidPasswordResetTokenError,
    UserNotFoundError,
)
from app.domain.identity.password import PlainPassword
from app.domain.identity.password_credential import PasswordCredential
from app.domain.identity.password_hasher import PasswordHasher
from app.domain.identity.passwordReset import PasswordReset
from app.domain.identity.passwordResetMessage import password_changed_notice
from app.domain.identity.session import hash_session_token
from app.domain.identity.user import User
from app.domain.notifications.notificationChannel import NotificationChannel


@dataclass(frozen=True)
class ConfirmedPasswordReset:
    """What answering produced: the account, how many sessions ended, how the notice went.

    ``user`` is the account *after* the change, which is the only version there is
    - unlike an address change, nothing about the account's own row moved, so this
    is the same user that was loaded to find the address to write to. It is carried
    because the presentations report who was affected, not because anything in it
    changed.

    ``sessions_revoked`` is the count of sessions this call actually deleted, and
    it is reported rather than kept internal because it is the fact that makes the
    revocation visible. A person who reset a password they had forgotten is told
    that every device was signed out; a person who did *not* reset it and gets the
    notice has just learned their sessions ended, which is the warning this whole
    flow exists to produce. It is also, honestly, a number that can be ``0`` - an
    account with no live sessions, or one whose sessions were already ended - and
    ``0`` is a true answer rather than a failure.

    **``notice_sent`` and ``notice_error`` are three states in two fields**, cloned
    from ``ConfirmedEmailChange`` and true for the same reasons:

    - ``True``, ``None`` - the account's address was told that the password changed.
    - ``False``, a reason - a channel exists and refused. The reset still happened;
      that is the whole point of catching it here rather than letting it raise.
    - ``False``, ``None`` - there is no mail account on this installation, so
      nothing was attempted. Unreachable for a *successful* confirm today, because
      ``RequestPasswordReset`` refuses without a channel and no row can exist
      without it - but reachable for an install whose SMTP was unset between the
      request and the answer, which is precisely the state that must not read as a
      failure.

    ``True`` with a reason cannot be constructed honestly and is left unguarded for
    the reason the sibling value gives: it would only ever be a wrong *report* of a
    courtesy, never a wrong *state* of the account.
    """

    user: User
    reset: PasswordReset
    sessions_revoked: int
    notice_sent: bool
    notice_error: str | None = None


class ConfirmPasswordReset:
    """Replace the account's password, end its sessions, and say so.

    **Three writes in one transaction, which is what this class is really about.**
    The pending request is spent, the credential is replaced, and every session the
    account holds is deleted - and all three are one fact: *this account's secret
    has changed.* A crash between any two of them is a state that must not exist. A
    spent code with the old password still in place is a reset the person believes
    happened and that did not. A new password with the old sessions still live is
    the specific failure this whole operation exists to prevent, since the premise
    of a reset is that somebody else may hold the old password - so a session opened
    with it and still working is a way in that survived the lock being changed. And
    an unspent code alongside a replaced password leaves a credential that can
    replace it again.

    **No actor**, joining ``LogOut`` ``ConfirmEmailChange`` and the request half of
    this flow. The token names one account and can change that one account's
    password; the account it acts on is read off the claimed row and never off a
    request, so there is no id for a caller to substitute. It is worth stating that
    this is the most consequential thing a bearer of a token can do in this system
    and that it is still not *privileged*: the token was mailed to an address the
    account already held, and getting one required nothing but knowing that address.

    **A new password that fails the policy does not spend the code**, and this is
    the one place this class deliberately diverges from ``ConfirmEmailChange`` - see
    the docstring of ``execute``.
    """

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        password_hasher: PasswordHasher,
        channel: NotificationChannel | None = None,
    ):
        """Bind a way to hash the new password, and a way to say the password changed.

        The hasher is not optional and not defaulted: without it there is nothing to
        store, so an install that could not hash could not confirm at all. The
        channel is optional in the same sense ``ConfirmEmailChange``'s is optional -
        ``None`` means no notice is attempted, and the result says so.
        """
        self._unit_of_work_factory = unit_of_work_factory
        self._password_hasher = password_hasher
        self._channel = channel

    def execute(
        self, token: str, new_password: str, now: datetime
    ) -> ConfirmedPasswordReset:
        """Set the password ``token`` authorises, and end every session on the account.

        The order of the steps is the design:

        1. **``PlainPassword(new_password)``, before anything else.** A password
           outside the length policy raises here and the code is *not* spent, which
           is the deliberate divergence from decision 170's "spent by the attempt".
           That decision spends a request on the attempt because the case it decides
           is a change in the *world* - the address was taken by somebody else while
           the request waited, so a retry with the same code could not succeed and
           keeping the code alive would only hold a dead credential. A password that
           fails the policy is a statement about the value the caller just typed,
           and a retry with a longer one succeeds. So the remedy is "type a longer
           password", not "ask for a new code", and refusing without spending is the
           kinder and the more accurate answer. The password is also never touched by
           the store before this point, so nothing half-written can exist.
        2. **The claim.** One atomic statement; a second confirm arriving in the same
           instant finds nothing to change. Everything below is reached only by a
           code that was mailed to this account's address.
        3. **The account.** ``get_by_id`` on the id the *claimed row* names. Absence
           is reported as the token's own refusal rather than as a missing user, for
           the reason ``InvalidSessionError`` gives: a caller who can tell "no such
           code" from "that account is gone" has an oracle and the remedy is
           identical. Note this refusal unwinds the claim with it - the row goes back
           to ``AWAITING`` rather than being committed as spent - which is safe
           because there is no longer an account for any retry to reach.
        4. **The credential.** A whole new ``PasswordCredential``, not an edit: the
           hash is replaced along with ``updated_at``, and the old hash is gone the
           moment this commits. Nothing here re-hashes the old password, and the
           plaintext exists only as the local ``PlainPassword`` for the length of
           this call.
        5. **The sessions.** Every one the account holds, deleted. Not a flag and not
           a subset - see ``SessionRepository.delete_by_user_id`` for why deletion is
           the whole mechanism and why this is the one operation where revocation is
           the point rather than a courtesy.

        Steps 2 to 5 are one transaction and one ``commit``. The notice comes after,
        outside the unit, and cannot fail the reset.

        ``now`` is passed in for the reason ``Session.issue`` takes it: it is the
        instant the claim tests the window against, the moment written to
        ``settled_at``, the ``updated_at`` on the new credential, and the moment
        printed in the notice. One reading, so four facts cannot disagree.

        The new password is **not compared against the old one**. Replacing a
        forgotten password with the same string is a legitimate thing to do - the
        person proved the mailbox and chose a secret - and refusing it would tell
        the bearer of the code something about the account's current password.
        """
        # Before the unit and before the claim, on purpose - see step 1 above. This
        # is the only check in this method that can run before anything is read.
        plain = PlainPassword(new_password)

        uow = self._unit_of_work_factory.start()
        try:
            reset = uow.password_resets.claim_by_token_hash(
                hash_session_token(token), now
            )

            try:
                user = uow.users.get_by_id(reset.user_id)
            except UserNotFoundError:
                raise InvalidPasswordResetTokenError(
                    "that reset code cannot be used"
                )

            uow.password_credentials.save(
                PasswordCredential(
                    user_id=reset.user_id,
                    password_hash=self._password_hasher.hash(plain),
                    updated_at=now,
                )
            )
            revoked = uow.sessions.delete_by_user_id(reset.user_id)
            uow.commit()
        finally:
            uow.rollback()

        return ConfirmedPasswordReset(
            user=user,
            reset=reset,
            sessions_revoked=revoked,
            **self._warn_the_account(reset, user.email),
        )

    def _warn_the_account(self, reset: PasswordReset, email: str) -> dict:
        """Send the notice, and turn a failure into something to report.

        **Best-effort, and here the asymmetry with the reset mail is even wider
        than it is for an address change.** That mail carries the code, so a failure
        means nobody can answer and it raises. This one announces something already
        done and impossible to undo - and refusing to apply a reset because the
        warning about it bounced would be the worst possible trade: the person the
        endpoint exists for would be left locked out with the password unchanged and
        the code that would have changed it spent.

        Note this is the *only* warning a person gets that somebody else set their
        password. It is caught rather than raised precisely so that it cannot become
        a reason for the reset not to have happened - but a bounce here is worth
        reporting to the caller, which is why the failure is captured into the
        result instead of being swallowed.

        Returns the two fields rather than the dataclass, so this method has one job
        and the caller's ``return`` reads as the single place a
        ``ConfirmedPasswordReset`` is built.
        """
        if self._channel is None:
            return {"notice_sent": False, "notice_error": None}

        try:
            self._channel.send(password_changed_notice(reset, email))
        except Exception as error:
            # Broad on purpose, matching ``ConfirmEmailChange``: a channel is
            # third-party code talking to a network, and every failure mode it has
            # means the same thing here. Narrowing this to the adapter's declared
            # exception would let an undeclared one become a 500 *after* the password
            # had already been replaced and the sessions already ended, which reports
            # a reset that happened as a failure.
            return {
                "notice_sent": False,
                "notice_error": str(error) or type(error).__name__,
            }

        return {"notice_sent": True, "notice_error": None}
