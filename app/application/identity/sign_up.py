"""Creating an account: an identity, and the credential that proves it."""

import uuid
from datetime import datetime

from app.application.unit_of_work import UnitOfWorkFactory
from app.domain.identity.exception import DuplicateEmailError
from app.domain.identity.password import PlainPassword
from app.domain.identity.password_credential import PasswordCredential
from app.domain.identity.password_hasher import PasswordHasher
from app.domain.identity.user import User


class SignUp:
    """Register an address, and give it a password.

    The replacement for ``ResolveUserByEmail``'s find-or-create, and the
    difference between the two is the whole of Phase 2a. That one created an
    account as a *side effect* of a caller naming it - you were whoever you said
    you were, and if the address was new the account appeared. This one creates
    an account only for somebody who is asking to, and hands back nothing that
    says who they are: registering and proving are separate acts, which is what
    makes proof mean anything.

    **It writes two rows, in one unit**, and that is the reason
    ``password_credentials`` is on the ``UnitOfWork`` at all rather than being
    reached for separately. A user with no credential is an account nobody can
    ever log into - and it fails invisibly, because the sign-up would have
    reported success and the address would now be taken, so the person can
    neither use what they made nor make it again. Committing the pair together
    makes that state unreachable rather than merely unlikely.

    **This is one of only two writes in the system nobody has to authenticate
    for**, the other being the login it feeds. That is inherent rather than a
    hole: they are how you come to have credentials. It is also why rate limiting
    is a real item in 2c and not a nicety - an unauthenticated write that runs
    argon2 and grows a table is a thing that can be done to you in bulk.
    """

    def __init__(
        self, unit_of_work_factory: UnitOfWorkFactory, password_hasher: PasswordHasher
    ):
        self._unit_of_work_factory = unit_of_work_factory
        self._password_hasher = password_hasher

    def execute(self, email: str, password: str, now: datetime) -> User:
        """Create the account holding ``email``, with ``password`` as its secret.

        Returns the ``User`` - the identity, with no credential attached, exactly
        as ``User`` is defined to be. The caller gets an account it can now log
        in to, and nothing it could mistake for being logged in already.

        **The address is validated by ``User`` and the password by
        ``PlainPassword``, and neither check is repeated here.** Both aggregates
        are what decide their own shape, and this method's job is only to hand
        them the raw values and store what they accept - so there is one rule per
        value, in the place a later reader would look for it.

        ``now`` is passed in rather than read, following
        ``Session.issue``/``is_expired``: a caller that wants to test what an
        account created at a particular moment looks like should be able to say
        which moment without freezing the process clock.
        """
        uow = self._unit_of_work_factory.start()
        try:
            if uow.users.find_by_email(email) is not None:
                raise DuplicateEmailError(f"{email} is already registered")

            user = User(
                user_id=uuid.uuid4(),
                email=email,
                # No Google identity, the same ``None`` ``ResolveUserByEmail``
                # wrote and for the same reason: this account was not created by
                # a Google sign-in, which is a fact rather than a missing value.
                google_subject=None,
                created_at=now,
            )
            # Constructed before anything is written, so a password that fails
            # the policy refuses the sign-up before a user row exists to be
            # rolled back. The unit makes that ordering a nicety rather than a
            # requirement - but a hash is the expensive part of this method, and
            # there is no reason to pay for one to discover the password was
            # three characters long.
            hashed = self._password_hasher.hash(PlainPassword(password))

            uow.users.save(user)
            uow.password_credentials.save(
                PasswordCredential(
                    user_id=user.user_id, password_hash=hashed, updated_at=now
                )
            )
            uow.commit()
            return user
        finally:
            uow.rollback()
