"""Asking to prove a number, so an account can be created on it.

The first half of the phone signup, and the second operation in this system that
a stranger can start, write a row, and not be a sign-up path itself - the other
being ``RequestPasswordReset``, whose shape this file follows deliberately and at
every step. Where the two argue the same thing the argument is not repeated here;
where they differ, the difference is named, because a reader who knows one of
these files should be able to see exactly what the other is not.
"""

from datetime import datetime

from app.application.unit_of_work import UnitOfWorkFactory
from app.domain.identity.exception import NoSmsAccountError
from app.domain.identity.phoneVerification import PhoneVerification
from app.domain.identity.phoneVerificationMessage import verification_code_message
from app.domain.notifications.smsChannel import SmsChannel


class RequestPhoneVerification:
    """Record a verification for a number, and text it a code.

    **No actor and no proof of any kind**, which puts it in ``RequestPasswordReset``'s
    class rather than ``RequestEmailChange``'s: the caller is somebody who may not
    have an account yet, and the whole point of the operation is that they cannot
    prove anything until the text arrives. Being exact about what that hands out,
    in the same terms that file uses - it cannot read anything, it cannot name an
    account, it cannot spend anything except a row the number itself will
    supersede. Its one new power is that **it costs money per call**, which is why
    rate limiting is a sharper item after this slice than before it; see the
    plan's ``Recorded as open``.

    **The difference from the reset flow is what the number is *for* here.** That
    flow takes an identifier and asks the store whether it names an account, and
    the refusal for an unknown one is the same answer as success. This one does not
    ask anything: the number is not a key being looked up, it is an address being
    *dialled*, and the only question about it is whether a text can be sent to it.
    So there is no ``PhoneVerificationOutcome`` with a ``requested`` flag - every
    valid number produces a row and a text, and the two ways to fail (a number that
    is not a number, an install with no SMS account) both **raise**, because both
    are statements about the request rather than answers to it. A one-field wrapper
    around the aggregate would exist only to be unwrapped.

    **It does not check whether the number already has an account, and this is the
    decision the whole two-step shape rests on.** Telling somebody "this number is
    taken" would be free for an attacker who is going to try to claim it anyway,
    and *asking* would turn an unauthenticated endpoint into a cheap enumeration
    oracle over a space that is small, structured and guessable - the exact
    argument ``LogIn`` makes about answering differently for an unknown identifier.
    The duplicate is refused one step later, at the confirm, where the person has
    already proved they hold the handset. That moves the refusal to the moment it
    is both safe to give and useful to receive.

    **The row is durable before the text is sent**, which is
    ``RequestPasswordReset``'s order and its argument: a code that left before its
    row was durable would be a code for a request that does not exist, and the
    person would present it and be told it means nothing. The other direction is
    survivable - a row whose text never arrived expires on its own in ten minutes
    and is superseded by the next attempt. That is also why the send is outside the
    unit, and why the token leaves this method by exactly one route, into one
    envelope.

    **A failed send raises**, and here the argument is at its strongest. That text
    carries the credential that *creates* the account; letting the failure pass
    quietly would leave the person waiting on a handset for a message that is not
    coming, with no account and no clue which of the two halves broke.
    """

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        channel: SmsChannel | None = None,
        unconfigured_reason: str | None = None,
    ):
        """Bind a way to send a text, and what to say if this install has none.

        ``channel`` is ``None`` when the installation has no SMS account, and
        ``unconfigured_reason`` is the sentence to refuse with - composed once by
        the composition root from ``describe_termii_configuration``, because that
        function reads the environment and this use case is not allowed to. Read
        ``RequestPasswordReset.__init__`` for the whole of that argument; it is
        identical here and the reason is the same one, one channel over.

        The fallback sentence is for the one caller that could forget to pass one -
        a test constructing this by hand - and it exists so that a missing reason
        degrades into a worse message rather than into a ``TypeError``.
        """

        self._unit_of_work_factory = unit_of_work_factory
        self._channel = channel
        self._unconfigured_reason = unconfigured_reason

    def execute(self, phone: str, now: datetime) -> PhoneVerification:
        """Record a verification for ``phone`` and text the code to it.

        The order of the two refusals and the one answer is the design:

        - **No SMS account - ``NoSmsAccountError``, first.** It depends on nothing
          about the input, so nothing is learned by getting past it, and it is
          raised before a unit is even opened. It names the setting that is
          missing, and it is raised *here* rather than by a presentation dependency
          so that the CLI and the API render one identical sentence. Note there is
          no fallback the way mail's refusal has one: a number has no second proof,
          because the reason to believe somebody holds a handset *is* a message
          arriving on it.
        - **Not a number - the aggregate's own refusal.** A malformed value folds to
          nothing or to something too short, and ``PhoneVerification`` refuses it at
          construction through ``checked_phone``. **This file deliberately does not
          apply that rule a second time**, which is a departure from ``SignUp`` and
          worth stating: ``SignUp`` folds and checks up front because it needs the
          folded spelling *before* the aggregate exists, for a duplicate lookup. No
          lookup happens here, so an explicit call would be a copy of a rule the
          aggregate already owns, free to drift from it. Refusing outright rather
          than answering "no account" is also right on its own terms - unlike an
          address, there is no registered-or-not question a malformed number could
          be hiding, and telling somebody their typo is a typo leaks nothing.

        Asking twice is not refused and does not leave two codes: the row is keyed
        on the number, so the second request supersedes the first and the earlier
        code dies the moment this commits. That is the remedy for a text that never
        arrived, and it is why there is no "already requested" branch here.

        ``now`` is passed in for the reason ``Session.issue`` takes it: it is the
        single moment this request was made, and both the deadline printed in the
        text and the deadline enforced by the claim are counted from it. One clock
        reading, so the two cannot disagree.
        """
        if self._channel is None:
            raise NoSmsAccountError(
                self._unconfigured_reason
                or "this installation has no SMS account, so a number cannot be "
                "verified"
            )

        uow = self._unit_of_work_factory.start()
        try:
            verification, token = PhoneVerification.issue(phone=phone, now=now)
            # The supersede, and it is the unique key doing it rather than a
            # delete-then-insert: asking twice leaves one row, the second code live
            # and the first dead. See ``PhoneVerificationRepository.save``.
            uow.phone_verifications.save(verification)
            uow.commit()
        finally:
            uow.rollback()

        # Outside the unit, on purpose and in this order: the code must be durable
        # before it is put on a wire, so that a send which succeeds and a commit
        # which then failed is not a state that can exist. A failure here
        # propagates - see the class docstring - and leaves only the row above,
        # which holds a hash of a code nobody has.
        #
        # The recipient is ``verification.phone`` and not the argument, which
        # matters if the fold changed anything: the stored spelling is what the
        # provider is handed, and the same folded value is what the ``UNIQUE`` slot
        # is held by, so the two cannot disagree about which handset this is. It is
        # also the shape Termii wants - international, no leading ``+``, no national
        # trunk ``0`` - by construction rather than by a second normalisation.
        self._channel.send(verification_code_message(verification, token))
        return verification
