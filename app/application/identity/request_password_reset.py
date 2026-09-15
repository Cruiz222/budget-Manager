"""Asking to set a new password for an account whose password has been lost.

The first half of the flow, and **the only operation in this system that a
stranger can start, that writes a row, and that is not a sign-up.** Everything
about the file follows from that: the caller is unidentified, the only input is an
address, and the fact that it can send mail to that address is the whole of the
power being handed out.
"""

from dataclasses import dataclass
from datetime import datetime

from app.application.unit_of_work import UnitOfWorkFactory
from app.domain.identity.exception import NoMailAccountError
from app.domain.identity.passwordReset import PasswordReset
from app.domain.identity.passwordResetMessage import reset_message
from app.domain.notifications.notificationChannel import NotificationChannel


@dataclass(frozen=True)
class PasswordResetOutcome:
    """What asking produced: either a request awaiting a code, or nothing at all.

    **One field, and the second fact is a property of it rather than beside it.**
    An address either names an account or does not, and a request exists exactly
    when it does - so ``reset`` is present exactly when something was sent, and
    ``requested`` and ``expires_at`` are both read off it. That makes "nothing was
    sent, and here is your deadline" unrepresentable rather than refused, the same
    structural pairing ``EmailChangeOutcome`` uses for its two outcomes.

    **No ``User`` is carried, and that is a departure from the sibling outcome
    worth stating.** ``EmailChangeOutcome`` carries one because the account is the
    subject of the operation and its address is the thing being moved. Here the
    account is not the caller's to see: this value is built on a path that a
    stranger reaches, and the one thing that path must never hand back is a
    confirmed account. The recipient address is not lost by its absence - the
    caller supplied it, and the mail was addressed with it.

    ``requested`` being readable at all is the interesting part. **The HTTP
    presentation ignores it** on purpose and answers identically either way; the
    CLI prints it, because a terminal with read access to the database file is not
    a public surface and its operator could `SELECT` the answer anyway. One value,
    two honest presentations, and each says in its own file which of the two it is.
    """

    reset: PasswordReset | None = None

    @property
    def requested(self) -> bool:
        """Whether an account was found and a code sent to it.

        ``False`` is not an error and not a refusal: it is the answer to half of
        all legitimate requests, from somebody who mistyped their own address.
        """
        return self.reset is not None

    @property
    def expires_at(self) -> datetime | None:
        """When the code stops working, or ``None`` if no code was sent."""
        return None if self.reset is None else self.reset.expires_at


class RequestPasswordReset:
    """Record a reset for an account, and mail it a code.

    **No actor and no proof of any kind, which makes this the loosest entry point
    in the codebase** - so it is worth being exact about what it does *not* allow.
    It cannot read anything: it takes an address and answers, and the only
    influence a caller has over the system afterwards is that one address may
    receive one mail. It cannot name an account except by guessing an address. It
    cannot spend anything except a row that the account's own address will
    supersede. Compare ``RequestEmailChange``, which asks for a session *and* the
    current password: the difference is not that this one is careless, it is that
    there is no password to ask for - the premise of the flow is that the caller
    has lost it.

    **The equivalence with a mailbox is the entire security argument**, and it is
    the reason nothing here is silent: whoever can read the account's mail can take
    the account. That is accepted because it is what a forgotten-password flow
    *is*, and because the alternative - no way back in - is worse for the person
    the account belongs to. It is also the reason the code is 256 bits rather than
    six digits and why rate limiting is the next thing this feature needs; both are
    argued in the places they live.

    **The refusal for an unknown address is not an error and not a silence, it is
    the same answer.** An address that names no account writes no row, mails
    nothing, and returns the same outcome shape as one that does - with
    ``requested`` ``False``. The HTTP route then answers with the same status and
    the same bytes either way, because a different answer is an enumeration oracle
    for anybody with a list of addresses. Nothing is being hidden from the caller:
    the sentence "if that address names an account, a code has been sent" is true
    in both cases, and it is the only sentence a stranger is entitled to.

    **A malformed address is not refused here either, and that is a decision rather
    than an omission.** ``fold_email`` folds whatever arrives and
    ``find_by_email`` looks for it, so ``not-an-address`` is simply an address that
    names no account and gets the same answer as any other. Raising
    ``InvalidUserEmailError`` instead would be inventing a validation the store does
    not perform: the shape rule belongs to ``User``, every account that exists
    already passed through it, and restating it here would create a second copy
    free to disagree with the first. ``RequestEmailChange`` does check, because
    there the address is a *value the account would come to hold* and a malformed
    one must not be written down. Nothing is written down here.

    **There is no credential check, deliberately.** An account with no password - a
    Google sign-in account, once Phase 2c lands - still gets a code and may thereby
    set its first password. The alternative is refusing, and refusing would have to
    be silent to keep the response identical for an unknown address, which turns
    "this account signs in with Google" into a person staring at a form that
    accepts their address and does nothing. A silent dead end is worse than the
    mail, and the mail goes to an address the account already holds.

    **The row is durable before the mail is sent**, which is
    ``RequestEmailChange``'s order and its argument: a code that left before its
    row was durable would be a code for a request that does not exist, and the
    person would present it and be told it means nothing. The other direction is
    survivable - a row whose code never arrived expires on its own and is
    superseded by the next attempt. That is also why the send is outside the unit,
    and why the token leaves this method by exactly one route, into one envelope.

    **A failed send raises, and here that is even more clearly right than it is for
    an address change.** That mail carries a credential the person is *waiting
    for*; this one carries the only way back into an account they are currently
    locked out of. Letting the failure pass quietly would leave them watching a
    mailbox for a mail that is not coming while the account stays shut.
    """

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        channel: NotificationChannel | None = None,
        unconfigured_reason: str | None = None,
    ):
        """Bind a way to send mail, and what to say if this install has none.

        ``channel`` is ``None`` when the installation has no mail account, and
        ``unconfigured_reason`` is the sentence to refuse with - composed once by
        the composition root from ``describe_configuration``, because that function
        is the only thing in the system that reads the environment and this use case
        is not allowed to.

        **Passing the reason in rather than composing it here is what lets one
        sentence serve both presentations.** ``RequestEmailChange`` takes the same
        ``channel is None`` signal and treats it as an instruction to do without
        mail, which works there because a password proof can authorise the change
        on its own. A forgotten password has no equivalent proof, so the only
        correct answer is refusal - and a refusal that the CLI and the API must
        render identically or the two surfaces disagree about why the system is
        shut. A use case cannot import the environment reader, an API dependency
        cannot be seen by the CLI, so the reason is made once where the
        configuration is read and handed down.

        The fallback sentence is for the one caller that could forget to pass one -
        a test constructing this by hand - and it exists so that a missing reason
        degrades into a worse message rather than into a ``TypeError``.
        """

        self._unit_of_work_factory = unit_of_work_factory
        self._channel = channel
        self._unconfigured_reason = unconfigured_reason

    def execute(self, email: str, now: datetime) -> PasswordResetOutcome:
        """Record a reset for ``email`` and mail the code to it, if it has an account.

        The order of the two refusals and the two answers is the design:

        - **No mail account - ``NoMailAccountError``, first.** It depends on nothing
          about the input, so nothing is learned by getting past it, and it is
          raised before a unit is even opened. Refusing here rather than telling the
          person to check their mail is the whole of the third ruling recorded
          against this feature: an address change can be authorised by the password
          proof alone, and a forgotten password has no such proof, so an install
          with no SMTP is an install where this operation is impossible - and it
          should say so, naming the setting that is missing.
        - **No such account - an outcome with ``requested`` false.** No row, no
          mail, no error. See the class docstring for why the route must not be
          able to tell this apart from success.

        Asking twice is not refused and does not leave two codes: the row is keyed
        on the account, so the second request supersedes the first and the earlier
        code dies the moment this commits. That is the remedy for a mail that never
        arrived, and it is why there is no "already requested" branch here.

        ``now`` is passed in for the reason ``Session.issue`` takes it: it is the
        single moment this request was made, and both the deadline printed in the
        mail and the deadline enforced by the claim are counted from it. One clock
        reading, so the two cannot disagree.
        """
        if self._channel is None:
            raise NoMailAccountError(
                self._unconfigured_reason
                or "this installation has no mail account, so a password cannot "
                "be reset"
            )

        uow = self._unit_of_work_factory.start()
        try:
            user = uow.users.find_by_email(email)
            if user is None:
                # Nothing is written and nothing is sent. The unit is rolled back
                # below, which for this branch is a no-op - and deliberately still a
                # rollback rather than a skipped unit, because an early return that
                # committed nothing would be one more place to get the transaction
                # boundary wrong to save a statement.
                return PasswordResetOutcome()

            reset, token = PasswordReset.issue(user_id=user.user_id, now=now)
            # The supersede, and it is the unique key doing it rather than a
            # delete-then-insert: asking twice leaves one row, the second code live
            # and the first dead. See ``PasswordResetRepository.save``.
            uow.password_resets.save(reset)
            uow.commit()
        finally:
            uow.rollback()

        # Outside the unit, on purpose and in this order: the code must be durable
        # before it is put on a wire, so that a send which succeeds and a commit
        # which then failed is not a state that can exist. A failure here
        # propagates - see the class docstring - and leaves only the row above,
        # which holds a hash of a code nobody has.
        #
        # The recipient is ``user.email`` and not the argument, which matters if the
        # fold changed anything: the account's address is what its mail arrives at,
        # and the string that was typed is a candidate that has now been resolved.
        self._channel.send(reset_message(reset, token, user.email))
        return PasswordResetOutcome(reset=reset)
