"""Handing out a session to somebody holding a Google identity that checks out.

The second operation in the system that hands out a session. Read with ``log_in``,
because the two are the same act reached by different proofs, and read that
module's docstring for why they are two modules rather than one with a branch.

**What is shared with ``LogIn`` is the refusal and nothing else.** The sentence an
unknown account gets is imported from there rather than written again - see
``REFUSAL`` - and the value a success returns is the same ``LoggedIn``, so
``translate.session_out`` and the CLI's session-file writer both work unchanged.

**What is *not* shared is the timing work, and the reason is worth stating rather
than leaving to be noticed.** ``LogIn._settle`` compares against ``DUMMY_HASH`` on
the path where no credential was found, because otherwise an unknown identifier
would answer in microseconds and a wrong password in tens of milliseconds. There
is no equivalent here and there cannot be one: this operation never compares a
password, so there is no branch that skips a comparison. Verification is a
signature check that happens once, before the lookup, and it costs the same
whether or not the subject turns out to be known. The oracle that path closes is
one this path never opens.
"""

from datetime import datetime

from app.application.identity.log_in import REFUSAL, LoggedIn
from app.application.unit_of_work import UnitOfWorkFactory
from app.domain.identity.exception import (
    InvalidCredentialsError,
    NoGoogleAccountError,
    UnverifiedGoogleEmailError,
)
from app.domain.identity.googleIdentityVerifier import GoogleIdentityVerifier
from app.domain.identity.session import Session


class LogInWithGoogle:
    """Exchange a verified Google identity for a session and its token.

    **It never creates an account.** A token whose subject names no account here
    is refused, not registered - which is the decision that keeps this operation
    from being the find-or-create ``ResolveUserByEmail`` was replaced for
    implementing. A caller who wants an account calls ``SignUpWithGoogle``; a
    caller who has one calls this. The two refusals a person can meet are
    therefore "no account" (here) and "already an account" (there), and neither
    operation has to guess which the caller meant.

    **It refuses an unverified address as well**, which looks redundant for a
    login and is not. An account's address is what ``RequestPasswordReset`` mails
    to, so a login that accepted an identity whose address Google had merely
    *claimed* would let somebody reach an account by presenting a Google login
    whose address had since changed hands. The check is the same one
    ``SignUpWithGoogle`` makes and it is made here for a different consequence -
    there it prevents writing a hijackable address, here it prevents using one.
    """

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        verifier: GoogleIdentityVerifier | None = None,
        unconfigured_reason: str | None = None,
    ):
        """Bind a way to verify a token, and what to say if this install has none.

        ``SignUpWithGoogle``'s constructor word for word, and the duplication is
        deliberate rather than a shared base class: two use cases that both refuse
        for the same reason are still two use cases, and a mixin holding the
        refusal would be a third place for "this installation has no Google client
        id" to be written down. The sentence itself is composed once, in the
        composition root, and handed to whichever of these was built.
        """
        self._unit_of_work_factory = unit_of_work_factory
        self._verifier = verifier
        self._unconfigured_reason = unconfigured_reason

    def execute(self, id_token: str, now: datetime) -> LoggedIn:
        """Return the account this identity belongs to, and a session.

        Raises ``NoGoogleAccountError`` if this installation has no client id,
        ``InvalidGoogleTokenError`` if the token is not one Google signed for this
        application, ``UnverifiedGoogleEmailError`` if Google has not proved the
        identity's address, and ``InvalidCredentialsError`` - with ``LogIn``'s own
        words - if the subject names no account.

        **The subject is the only thing looked up**, and the address is not
        consulted at all. That is the point of having a subject: it is stable for
        the life of the Google account, whereas the address on it can change, so
        an account found by address would be unreachable the moment its owner
        changed their Gmail. The address on a token is used by the *sign-up* and
        then never again, which is what makes a Google account's stored address a
        record of what it was rather than a key to it.

        ``now`` is passed in for the reason ``Session.issue`` takes it.
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
                "Google has not verified that address, so it is not proof of an "
                "identity"
            )

        uow = self._unit_of_work_factory.start()
        try:
            user = uow.users.find_by_google_subject(identity.subject)
            if user is None:
                # ``LogIn``'s sentence, imported rather than re-worded. "No
                # account matches what you showed me" is what both mean, and a
                # second copy of the string is a second thing to keep in step.
                raise InvalidCredentialsError(REFUSAL)

            session, token = Session.issue(user.user_id, now)
            uow.sessions.save(session)
            uow.commit()
            return LoggedIn(user=user, session=session, token=token)
        finally:
            uow.rollback()
