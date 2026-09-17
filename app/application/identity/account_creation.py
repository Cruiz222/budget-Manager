"""What "there is now an account" is written down as, once per way in.

**A module rather than a private method on any signup, and the reason is that no
signup owns it.** ``SignUp``, ``ConfirmPhoneSignUp`` and ``SignUpWithGoogle`` are
different operations - one takes an address and a password, one spends a texted
code, one verifies a token Google signed - and they agree on exactly one thing:
what an account *is*, the moment it comes into being. Putting this on ``SignUp``
would make the other two call *the email signup's* helper, which reads as one
depending on the other when in fact all three depend on the same fact. A shared
module names that fact instead, and it is the same shape ``passwordResetMessage``
uses for the sentences several flows share.

**There are two writers here and not one with a branch, because the rows genuinely
differ.** Two of the three ways in write a user *and* a password credential;
Google sign-in writes a user and no credential at all, deliberately, because the
way in is the Google subject. The next paragraphs are about what those two have in
common, which is the invariant the split exposed.

**It writes into a unit the caller already opened and does not commit.** That is
deliberate and it is the one thing a caller can get wrong with it: an account is
only ever half of a transaction here. ``SignUp`` commits it with nothing else;
``ConfirmPhoneSignUp`` commits it together with the claimed verification, which is
the pairing ``UnitOfWork.phone_verifications`` argues for at length - a claim that
landed without its account holds the ``UNIQUE`` slot on a number nobody has, so
the person who does hold the handset cannot sign up with it until the request
expires; ``SignUpWithGoogle`` commits it alone. So neither function here may take
a unit of its own; a helper that opened one would make that bug reachable by
writing the obvious call.
"""

from datetime import datetime

from app.application.unit_of_work import UnitOfWork
from app.domain.identity.exception import InvalidUserGoogleSubjectError
from app.domain.identity.password_credential import PasswordCredential
from app.domain.identity.user import User


def record_new_account(
    uow: UnitOfWork, *, user: User, password_hash: str, now: datetime
) -> None:
    """Write ``user`` and the credential that proves it, in the caller's unit.

    The pair is one fact, which is ``SignUp``'s original argument for putting
    ``password_credentials`` on the ``UnitOfWork`` at all: an account written
    without its credential is one nobody can ever log into, and it fails
    *invisibly*, because the signup would have reported success and the identifier
    would now be taken - so the person can neither use what they made nor make it
    again.

    **That sentence used to read "a user with no credential is an account nobody
    can ever log into", and that is now false in a way worth reading rather than
    deleting.** ``SignUpWithGoogle`` below deliberately writes exactly that state,
    because for a Google account the credential is not what gets you in - the
    subject is. So the invariant this function enforces was never "every account
    has a credential". It is **"every account has a way in"**, and there are two
    ways in: a password credential, and a Google subject. This function is the
    first; the one below is the second. An account with neither is still the
    unreachable, invisible state this whole module exists to prevent, and it is
    now prevented by there being two guards rather than one.

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


def record_new_google_account(uow: UnitOfWork, *, user: User) -> None:
    """Write ``user`` and nothing else, in the caller's unit.

    **One row rather than two, and the missing row is the design.** A Google
    account has no password - that is what makes it a Google account - so writing
    a credential here would mean inventing a hash for a password nobody chose and
    nobody knows, which is strictly worse than the absence: the account would
    *look* password-protected to anything that counts credentials. What it has
    instead is ``user.google_subject``, and ``LogInWithGoogle`` finds it by exactly
    that. A password can be added to such an account later, through the flow the
    reset path was designed for - see ``RequestPasswordReset``, which explains why
    it deliberately has no credential check.

    **There is no ``now`` parameter, and the absence is worth a sentence.** The
    other writer needs one because it stamps a ``PasswordCredential.updated_at``,
    and the whole reason to thread a clock through is to stop two rows naming two
    different moments for one act. There is one row here, and its moment is
    ``user.created_at`` - already set by the caller, already the caller's single
    reading. Accepting a ``now`` this function had nowhere to put would be an
    argument that looks like it does something.

    **The guard is an exception rather than an ``assert``**, following the rule
    that ``app/`` carries no assertions: an ``assert`` is removed by ``-O``, and a
    guard that disappears under an optimisation flag is not a guard. It refuses a
    user with no subject because for *this* kind of account the subject is the way
    in - so a user row written by this function without one would be the
    unreachable account the module docstring describes, arrived at from the other
    direction. The caller cannot produce one: ``SignUpWithGoogle`` builds the
    ``User`` with a subject a few lines earlier, from a token it has already
    verified. This is the second lock on a door that is already shut, and it is
    here because the cost of being wrong is an account nobody can reach and
    nobody can see.
    """
    if user.google_subject is None:
        raise InvalidUserGoogleSubjectError(
            "a Google account needs a google subject: it is the only way in that "
            "this account has"
        )

    uow.users.save(user)
