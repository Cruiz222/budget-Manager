"""The two rows that make a new account, written once for both ways in.

**A module rather than a private method on either signup, and the reason is that
neither signup owns it.** ``SignUp`` and ``ConfirmPhoneSignUp`` are different
operations - one takes an address and a password, the other spends a texted code -
and they agree on exactly one thing: what "there is now an account" is written
down as. Putting this on ``SignUp`` would make the phone signup call *the email
signup's* helper, which reads as one depending on the other when in fact both
depend on the same fact. A shared module names that fact instead, and it is the
same shape ``passwordResetMessage`` uses for the sentences two flows share.

**It writes into a unit the caller already opened and does not commit.** That is
deliberate and it is the one thing a caller can get wrong with it: an account is
only ever half of a transaction here. ``SignUp`` commits it with nothing else;
``ConfirmPhoneSignUp`` commits it together with the claimed verification, which is
the pairing ``UnitOfWork.phone_verifications`` argues for at length - a claim that
landed without its account holds the ``UNIQUE`` slot on a number nobody has, so
the person who does hold the handset cannot sign up with it until the request
expires. So this function must never take a unit of its own; a helper that opened
one would make that bug reachable by writing the obvious call.
"""

from datetime import datetime

from app.application.unit_of_work import UnitOfWork
from app.domain.identity.password_credential import PasswordCredential
from app.domain.identity.user import User


def record_new_account(
    uow: UnitOfWork, *, user: User, password_hash: str, now: datetime
) -> None:
    """Write ``user`` and the credential that proves it, in the caller's unit.

    The pair is one fact, which is ``SignUp``'s original argument for putting
    ``password_credentials`` on the ``UnitOfWork`` at all: a user with no
    credential is an account nobody can ever log into, and it fails *invisibly*,
    because the signup would have reported success and the identifier would now be
    taken - so the person can neither use what they made nor make it again.

    ``password_hash`` is passed in already hashed rather than the plaintext being
    hashed here, and the ordering that buys is the point: both callers construct
    ``PlainPassword`` and pay for the hash **before** anything is written, so a
    password that fails the policy refuses before a user row exists to be rolled
    back. It also keeps this function free of the hasher, which means it cannot
    become a second place where the policy is applied.

    ``now`` is the caller's single clock reading, threaded through so the
    credential's ``updated_at`` and the account's ``created_at`` cannot name two
    different moments for one act.
    """
    uow.users.save(user)
    uow.password_credentials.save(
        PasswordCredential(
            user_id=user.user_id, password_hash=password_hash, updated_at=now
        )
    )
