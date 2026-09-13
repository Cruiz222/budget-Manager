"""Creating an account: an identity, and the credential that proves it."""

import uuid
from datetime import datetime

from app.application.unit_of_work import UnitOfWorkFactory
from app.domain.identity.emailAddress import refuse_unusable_email
from app.domain.identity.exception import DuplicateEmailError
from app.domain.identity.password import PlainPassword
from app.domain.identity.password_credential import PasswordCredential
from app.domain.identity.password_hasher import PasswordHasher
from app.domain.identity.user import User, checked_email


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

    **There are two rules about an address here, and they are different rules
    from different places.** The *shape* rule - fold it, it must not be empty, it
    must contain an ``@`` - is the aggregate's own, and it is applied by calling
    ``checked_email``, the same function ``User.__post_init__`` calls, so there is
    still exactly one definition of it. The *usability* rule - the domain must
    have a dot in it - is a policy about **minting** an address, and ``User``
    deliberately does not hold it: that class refuses nothing on load, because a
    rule enforced at construction would make every account already stranded at an
    unusable address unreadable rather than rescuable. See
    ``app.domain.identity.emailAddress`` for the whole of that argument, and
    ``User.change_email`` for the other minting site.

    So this is the one place a fresh address enters the system, and the check
    that it can be *billed to* belongs here, at the door, which is what "refused
    at entry point" means. A sign-up that got past it would create an account
    whose first deposit is refused by a payment provider.
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

        **The address is checked twice by two rules and the password once by
        ``PlainPassword``.** The shape rule is applied here by calling the
        aggregate's own ``checked_email`` rather than by letting the constructor
        below make the same test a few lines later, and that is an ordering
        decision rather than a duplication: shape has to be settled *before*
        usability, or ``not-an-address`` would be refused as unusable - a
        sentence about the domain rule - when what is wrong with it is that it is
        not an address at all. The two errors name different things and send the
        reader to different files, so the order they can be reached in is
        load-bearing.

        **Usability is checked before the duplicate lookup, and both reasons
        point the same way.** The first is what the person can act on: an address
        that is already taken and *also* unusable would be reported as taken,
        which invites somebody to log in to an account no provider will bill -
        true, and useless. The second is that usability is a property of the
        string that was typed and the duplicate answer is a property of the
        store, so asking the cheap local question first means an unauthenticated
        caller learns nothing about which addresses are registered until they
        have named one that could be.

        ``now`` is passed in rather than read, following
        ``Session.issue``/``is_expired``: a caller that wants to test what an
        account created at a particular moment looks like should be able to say
        which moment without freezing the process clock.
        """
        uow = self._unit_of_work_factory.start()
        try:
            # Folded once, here, and the folded value is what everything below
            # uses - so the duplicate lookup and the row that is written are
            # comparing and holding the same string. ``User`` applies the same
            # function again on construction, which is idempotent by design.
            email = checked_email(email)
            refuse_unusable_email(email)

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
