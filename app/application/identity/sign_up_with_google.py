"""Creating an account from an identity Google has already proved.

The second way to make an account from a proven identifier, and the third way to
make one at all. Read with ``sign_up``, because the two are the same act with
different evidence: that one is handed an address and a password and has to take
the caller's word for nothing except the password, and this one is handed a token
and has to take the caller's word for nothing at all.

**Two rows become one, and that is the whole of the difference below.** An
account arriving this way has no password, so ``record_new_google_account`` writes
the user and stops. What it has instead is the Google subject, which is what
``LogInWithGoogle`` looks it up by - see ``account_creation``, whose docstring was
rewritten when this flow landed because the invariant it stated ("a user with no
credential is an account nobody can ever log into") stopped being true in a way
that mattered.
"""

import uuid
from datetime import datetime

from app.application.identity.account_creation import record_new_google_account
from app.application.unit_of_work import UnitOfWorkFactory
from app.domain.identity.emailAddress import refuse_unusable_email
from app.domain.identity.exception import (
    DuplicateEmailError,
    DuplicateGoogleSubjectError,
    NoGoogleAccountError,
    UnverifiedGoogleEmailError,
)
from app.domain.identity.googleIdentityVerifier import GoogleIdentityVerifier
from app.domain.identity.user import User, checked_email


class SignUpWithGoogle:
    """Register the account a verified Google identity belongs to.

    **It creates, and it never logs in.** There is a separate ``LogInWithGoogle``
    and the two are not merged, which is ``SignUp``'s decision word for word:
    registering and proving are separate acts, and a single endpoint that did
    both would be the find-or-create ``ResolveUserByEmail`` was replaced for
    implementing. The cost is that a first-time client makes two calls with the
    same token, and the token is reusable inside its lifetime, so the cost is
    two round trips and no second credential.

    **It refuses an address Google has not proved**, and the refusal is not
    caution - it is the closure of a path that already exists. ``GoogleIdentity``
    carries the argument in full; the short version is that
    ``RequestPasswordReset`` mails a code to whatever address an account holds and
    deliberately has no credential check, so an account holding an unproved
    address would have a working reset code sent to whoever controls that address.

    **The refusal happens before a unit is opened**, along with the token
    verification itself, and both are about not doing work under a transaction.
    Verification is a network call to Google - it must not hold a database
    transaction open for its duration, and the API already runs this whole
    operation off the event loop for that reason.
    """

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        verifier: GoogleIdentityVerifier | None = None,
        unconfigured_reason: str | None = None,
    ):
        """Bind a way to verify a token, and what to say if this install has none.

        ``verifier`` is ``None`` when the installation has no Google client id, and
        ``unconfigured_reason`` is the sentence to refuse with - composed once by
        the composition root from ``describe_google_configuration``, because that
        function is the only thing in the system that reads the environment and
        this use case is not allowed to.

        **The shape is ``RequestPasswordReset``'s**, and it is worth reading the two
        together rather than treating this as a coincidence: the same optional
        collaborator, the same separately-passed sentence, the same "a missing
        configuration is a refusal naming the variable rather than a degraded
        service". The difference is only in what a missing one *means* - there, a
        password cannot be reset; here, a token cannot be judged at all. Note what
        does **not** appear on either side: an ``if settings is None`` anywhere in
        the application layer, or a second copy of the sentence in a presentation.
        """

        self._unit_of_work_factory = unit_of_work_factory
        self._verifier = verifier
        self._unconfigured_reason = unconfigured_reason

    def execute(self, id_token: str, now: datetime) -> User:
        """Create the account this token's identity owns.

        Returns the ``User`` and no session, which is ``SignUp``'s contract
        exactly: the caller gets an account it can now log in to, and nothing it
        could mistake for being logged in already.

        Five refusals are reachable here, and the order they are reached in is the
        part worth reading:

        0. ``NoGoogleAccountError`` if this installation has no client id. It is
           first because it depends on nothing the caller sent, so nothing is
           learned by getting past it, and because it is the only one of the five
           that no change to the request can avoid.
        1. ``InvalidGoogleTokenError``, from the verifier, if the token is not one
           Google signed for this application. Nothing below runs.
        2. ``UnverifiedGoogleEmailError`` if Google has not proved the address.
           Also before the unit opens, because it needs no store to decide.
        3. ``InvalidUserEmailError`` for an address with no ``@``, and
           ``UnusableEmailError`` for one whose domain has no dot - the same pair,
           in the same order, for the same reason as ``SignUp``: shape before
           usability, so ``not-an-address`` is not reported as an unusable one.
           This flow *mints* an address onto an account, which is what puts it on
           the stricter side of that rule - see ``emailAddress``.
        4. ``DuplicateGoogleSubjectError`` if this Google account already has one
           here, and ``DuplicateEmailError`` if the address is held by an account
           of any kind.

        **The subject is checked before the address**, which is a decision about
        which sentence a person reads rather than about correctness - both are
        conflicts. The subject collision means "you already signed up with this
        Google account, log in", and the address collision means "that address is
        taken, use another one". The first is what a client that called the wrong
        route needs to be told, and it is also the one that cannot be worked
        around; the second is advice a person can act on. A caller who is told to
        use another address by a person who already has an account would go and
        make a second one.

        ``now`` is passed in rather than read, following ``SignUp`` and
        ``Session.issue``.
        """
        if self._verifier is None:
            raise NoGoogleAccountError(
                self._unconfigured_reason
                or "this installation has no Google client id, so no Google "
                "sign-in can be verified"
            )

        identity = self._verifier.verify(id_token)

        if not identity.email_verified:
            raise UnverifiedGoogleEmailError(
                "Google has not verified that address, so no account can be "
                "created from it"
            )

        uow = self._unit_of_work_factory.start()
        try:
            # The same two calls, in the same order, that ``SignUp`` makes - and
            # the same functions rather than a re-implementation of them, so there
            # is still exactly one definition of what an address may look like.
            email = checked_email(identity.email)
            refuse_unusable_email(email)

            if uow.users.find_by_google_subject(identity.subject) is not None:
                raise DuplicateGoogleSubjectError(
                    "that Google account already has an account here"
                )

            if uow.users.find_by_email(email) is not None:
                raise DuplicateEmailError(f"{email} is already registered")

            user = User(
                user_id=uuid.uuid4(),
                email=email,
                # No number, for ``SignUp``'s reason mirrored: this path registers
                # a Google identity, and an account created here was not signed up
                # with a handset.
                phone=None,
                # The subject, verbatim, exactly as the verifier reported it. Not
                # re-derived from the address and not normalised - it is an opaque
                # identifier Google issued, and it is the only way into this
                # account that exists.
                google_subject=identity.subject,
                created_at=now,
            )

            record_new_google_account(uow, user=user)
            uow.commit()
            return user
        finally:
            uow.rollback()
