"""Answering a phone signup: the texted code comes back, and an account exists.

The second half of the flow, and **the only operation in this system that creates
an account without being handed a password *and* an identifier together** - the
identifier is the one the claimed row carries, and the password is the only thing
the caller supplies. It is authorised by the token alone, joining ``LogOut``,
``ConfirmEmailChange`` and ``ConfirmPasswordReset``: possession of the thing the
row names is the proof, and requiring anything more would not add a check, it
would add a way to fail.
"""

import uuid
from datetime import datetime

from app.application.identity.account_creation import record_new_account
from app.application.unit_of_work import UnitOfWorkFactory
from app.domain.identity.exception import DuplicatePhoneError
from app.domain.identity.password import PlainPassword
from app.domain.identity.password_hasher import PasswordHasher
from app.domain.identity.session import hash_session_token
from app.domain.identity.user import User


class ConfirmPhoneSignUp:
    """Create the account the code authorises, holding the number it was sent to.

    **The number is read *out* of the claimed row rather than passed in, and that
    is the decision ``PhoneVerificationRepository.claim_by_token_hash`` argues at
    length.** The caller typed the number a moment ago and could hand it over, and
    scoping the claim by it was the tempting option - declined, because scoping
    would make a mistyped digit refuse as "that code means nothing" and would break
    the fall-through of the three refusals: a row matched by code but not by number
    would be reported as *expired* with an ``expires_at`` in the future. So
    ``execute`` takes no ``phone``, which is a departure from this slice's plan and
    is exactly the shape ``ConfirmPasswordReset.execute`` already has.

    **It creates the account, so it is the one confirm in the system that leaves
    something where before there was nothing.** The three siblings all apply
    something to an account that already exists; this one's premise is that no
    account exists, because the account is what answering creates. That is why it
    takes a password - there is no credential to replace - and why the row it spends
    carries no ``user_id``.

    **Two writes commit with the spend, and the reason is ``UnitOfWork``'s rather
    than this file's.** ``phone_verifications`` is on the unit for it: a claim that
    committed without its account would leave the ``UNIQUE`` slot on a number
    nobody has, and since ``UNIQUE(phone)`` is what stops two people signing up
    with one handset, the person who actually holds the phone could not sign up
    with it until the request expired. See ``record_new_account`` for the second
    write's own claim, which is older than the number.
    """

    def __init__(
        self, unit_of_work_factory: UnitOfWorkFactory, password_hasher: PasswordHasher
    ):
        """Bind a way to hash the password, which is not optional and not defaulted.

        There is nothing to store without it, so an install that could not hash
        could not confirm at all. No channel: nothing is sent from here, and the
        argument for that is in the module docstring of
        ``phoneVerificationMessage`` - there is no second message, because a text
        saying "your number is verified" would tell somebody a fact they learned by
        answering the code.
        """
        self._unit_of_work_factory = unit_of_work_factory
        self._password_hasher = password_hasher

    def execute(self, code: str, password: str, now: datetime) -> User:
        """Create the account ``code`` authorises, with ``password`` as its secret.

        Returns the ``User`` - the identity, with no credential attached, exactly
        as ``SignUp`` returns it and for the same reason: the caller gets an
        account it can now log in to, and nothing it could mistake for being logged
        in already.

        The order of the steps is the design:

        1. **``PlainPassword(password)``, before anything else.** A password
           outside the length policy raises here and the code is *not* spent, which
           is ``ConfirmPasswordReset``'s deliberate divergence from decision 170
           and it transfers unchanged: a password that fails the policy is a
           statement about the value the caller just typed, and a retry with a
           longer one succeeds - the remedy is "type a longer password", not "ask
           for a new text". The password is also never touched by the store before
           this point, so nothing half-written can exist.
        2. **The claim.** One atomic statement; a second confirm arriving in the
           same instant finds nothing to spend. Everything below is reached only by
           a code that was texted to the number this row names.
        3. **The duplicate lookup.** The number may have been claimed between the
           two steps, which is the one thing that genuinely can change inside the
           window. **This refusal spends the code**, so it commits before it raises
           - see the docstring note below.
        4. **The account and its credential**, through ``record_new_account``, so
           that this and ``SignUp`` cannot drift about what an account is. The hash
           is paid for *after* the duplicate lookup, so a number that is already
           taken refuses before an argon2 hash is computed for nothing.

        Steps 2 to 4 are one transaction and one ``commit``.

        **The duplicate refusal is spent by the attempt, not by the success**, which
        is ``ConfirmEmailChange``'s rule and its argument: a code that survived a
        failed attempt would be a live credential whose whole purpose is to be
        usable once, and the remedy for the person is the same either way - ask
        again. The two flows differ in what "ask again" means and the difference is
        worth naming, because it is the reason the rule is *right* here rather than
        merely inherited: an address change can be retried with a different
        address, whereas a number that is taken is taken, so a retry with this code
        could only ever produce this same refusal. Keeping it alive would hold a
        dead credential and, in the meantime, a ``UNIQUE`` slot against the very
        number somebody else now holds.

        ``now`` is passed in for the reason ``Session.issue`` takes it: it is the
        instant the claim tests the window against, the moment written to the row's
        ``settled_at``, and the account's ``created_at``. One reading, so the
        recorded facts cannot disagree about when this happened.
        """
        # Before the unit and before the claim, on purpose - see step 1 above.
        plain = PlainPassword(password)

        uow = self._unit_of_work_factory.start()
        try:
            verification = uow.phone_verifications.claim_by_token_hash(
                hash_session_token(code), now
            )

            if uow.users.find_by_phone(verification.phone) is not None:
                # The claim is committed before the refusal so that the code really
                # is spent - the ``rollback`` in the ``finally`` below would
                # otherwise undo it, and "spent by the attempt" would be a sentence
                # in a docstring rather than something the store does. Nothing else
                # has been written at this point, so the commit spends the claim and
                # nothing more.
                uow.commit()
                raise DuplicatePhoneError(
                    f"{verification.phone} is already registered"
                )

            user = User(
                user_id=uuid.uuid4(),
                # No email, which is the whole point of this path: an account made
                # from a text has an address only if its holder later sets one, and
                # ``RequestEmailChange`` handles the ``None`` it starts from. Until
                # then a deposit is refused with a sentence naming what is missing -
                # see ``initiate_deposit``.
                email=None,
                phone=verification.phone,
                # No Google identity, for ``SignUp``'s reason: this account was not
                # created by a Google sign-in, which is a fact rather than a missing
                # value.
                google_subject=None,
                created_at=now,
            )
            record_new_account(
                uow,
                user=user,
                password_hash=self._password_hasher.hash(plain),
                now=now,
            )
            uow.commit()
            return user
        finally:
            uow.rollback()
