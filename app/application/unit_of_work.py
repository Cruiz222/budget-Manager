from abc import ABC, abstractmethod

from app.domain.repositories.confirmation_repository import ConfirmationRepository
from app.domain.repositories.email_change_repository import EmailChangeRepository
from app.domain.repositories.notification_repository import NotificationRepository
from app.domain.repositories.virtual_account_repository import (
    VirtualAccountRepository,
)
from app.domain.repositories.outbound_message_repository import (
    OutboundMessageRepository,
)
from app.domain.repositories.plan_notice_repository import PlanNoticeRepository
from app.domain.repositories.password_credential_repository import (
    PasswordCredentialRepository,
)
from app.domain.repositories.password_reset_repository import PasswordResetRepository
from app.domain.repositories.phone_verification_repository import (
    PhoneVerificationRepository,
)
from app.domain.repositories.plan_run_repository import PlanRunRepository
from app.domain.repositories.profile_repository import ProfileRepository
from app.domain.repositories.savings_plan_repository import SavingsPlanRepository
from app.domain.repositories.session_repository import SessionRepository
from app.domain.repositories.transaction_repository import TransactionRepository
from app.domain.repositories.user_repository import UserRepository
from app.domain.repositories.wallet_repository import WalletRepository


class UnitOfWork(ABC):

    wallets: WalletRepository
    virtual_accounts: VirtualAccountRepository
    transactions: TransactionRepository
    plans: SavingsPlanRepository
    plan_runs: PlanRunRepository
    notices: PlanNoticeRepository
    outbound_messages: OutboundMessageRepository
    notifications: NotificationRepository
    users: UserRepository
    #: Correctness, not convenience, and the newest member of that small group:
    #: signing up writes a user *and* this row, and they must land together. A
    #: user with no credential is an account nobody can ever log into, and the
    #: failure would be invisible - the sign-up would have reported success, and
    #: the address would already be taken, so the person could neither log in nor
    #: try again. See ``PasswordCredentialRepository``.
    password_credentials: PasswordCredentialRepository
    #: Convenience, and the second of the two unscoped repositories above.
    #: ``sessions`` is unscoped for the same reason ``users`` is, which is worth
    #: stating because it is the thing that makes both look like mistakes: a
    #: session is looked up *before* the caller is known, because looking it up
    #: is how they become known. Asking a session who it belongs to cannot
    #: require already knowing the answer. See ``SessionRepository``.
    sessions: SessionRepository
    #: Correctness, not convenience, and the third member of that small group
    #: after ``wallets``/``transactions``/``plan_runs`` and ``notifications``.
    #:
    #: A second-level confirmation makes two things true at once: the request was
    #: answered, and the money moved. Those must land together, and a crash
    #: between them is worse than either alone - a request recorded as carried
    #: out with nothing to show for it, or money moved by a request that still
    #: looks unanswerable. The pairing is what makes "this confirmation was
    #: spent" imply "and here is the ledger row it bought".
    #:
    #: Note it is *not* here because the request has to be read in the same
    #: snapshot - a confirmation's fields are immutable once written, so reading
    #: one outside the money's transaction decides nothing. It is here for the
    #: write, and the write is the spend. See ``ConfirmationRepository.claim``.
    confirmations: ConfirmationRepository
    #: Correctness, not convenience, and the fourth member of that small group
    #: after ``wallets``/``transactions``/``plan_runs``, ``notifications`` and
    #: ``confirmations``.
    #:
    #: Confirming an address change makes two things true at once: the mailed token
    #: was spent, and the account holds a different address. Those must land
    #: together, and a crash between them is worse than either alone - a token that
    #: still works after it has been used, or an account moved to an address whose
    #: request still reads ``AWAITING`` so the same token can be presented a second
    #: time. The pairing is what makes "this request was spent" imply "and this is
    #: the address it bought".
    #:
    #: The request half is written by a *different* repository from the account
    #: half - ``email_changes`` here and ``users`` above - which is the reason this
    #: declaration exists at all rather than being left to the use case to
    #: remember. A use case that opened two units would commit the spend and the
    #: move separately, and nothing about either write would look wrong.
    #:
    #: See ``EmailChangeRepository.claim_by_token_hash`` for the statement that
    #: spends one.
    email_changes: EmailChangeRepository
    #: Correctness, not convenience, and the fifth member of that small group
    #: after ``wallets``/``transactions``/``plan_runs``, ``notifications``,
    #: ``confirmations`` and ``email_changes`` - and the one that pairs the most
    #: things at once.
    #:
    #: Answering a password reset makes **three** things true together: the mailed
    #: token was spent, the account holds a new password, and every session the
    #: account held has been deleted. Those must land together, and a crash between
    #: any two of them is worse than either alone. A spent token with the old
    #: password still in place is a reset the person believes happened and that did
    #: not. A new password with the old sessions still live is a revocation that
    #: silently did not happen, which is the failure mode that matters most here:
    #: the premise of a reset is that somebody else may hold the old password, so a
    #: session opened with it and still working is exactly the hole the whole
    #: operation exists to close. And a new password with nothing recording that the
    #: token was spent leaves a code that can change the password a second time.
    #:
    #: The three halves are written by three different repositories -
    #: ``password_resets`` here, ``password_credentials`` and ``sessions`` above -
    #: which is the reason this declaration exists at all rather than being left to
    #: the use case to remember. A use case that opened separate units would commit
    #: the spend and the replacement separately, and nothing about either write
    #: would look wrong.
    #:
    #: Note what is **not** on this list: the account's address. A reset does not
    #: move it, and ``password_changed_notice`` is sent *after* the commit rather
    #: than queued inside it - a warning that a reset happened cannot be a condition
    #: of the reset happening. Contrast ``email_changes`` above, where the address
    #: *is* the thing the request authorises.
    #:
    #: See ``PasswordResetRepository.save`` for the statement that records one and
    #: ``PasswordResetRepository.claim_by_token_hash`` for the one that spends it.
    password_resets: PasswordResetRepository
    #: Correctness, not convenience, and the sixth member of that small group after
    #: ``wallets``/``transactions``/``plan_runs``, ``notifications``,
    #: ``confirmations``, ``email_changes`` and ``password_resets`` - and the first
    #: one that creates an account rather than moving something on one.
    #:
    #: Answering a phone verification makes two things true together: the texted code
    #: was spent, and an account now holds the number it was sent to. Those must land
    #: together, and a crash between them is worse than either alone in a way neither
    #: of the other pairings is - it is not a stale credential and not a lost address,
    #: it is a number that is *taken*. ``UNIQUE(phone)`` is what stops two people
    #: signing up with one handset, so a claim that committed without its account
    #: leaves the row holding the slot, and the person who actually holds that phone
    #: cannot sign up with it at all until the request expires. The pairing is what
    #: makes "this code was spent" imply "and here is the account it bought".
    #:
    #: The two halves are written by three different repositories -
    #: ``phone_verifications`` here, ``users`` and ``password_credentials`` above -
    #: which is the reason this declaration exists at all rather than being left to
    #: the use case to remember. A use case that opened separate units would commit the
    #: spend and the account separately, and nothing about either write would look
    #: wrong.
    #:
    #: **The credential is here for a second and independent reason**, worth naming
    #: because it is the one write in this pairing that has nothing to do with the
    #: number: an account with no credential is one nobody can ever log into, and it
    #: fails invisibly, since the signup would have reported success and the number
    #: would already be taken. So three writes are one fact for two different reasons
    #: at once, which is exactly the kind of thing a use case left to remember gets
    #: wrong.
    #:
    #: See ``PhoneVerificationRepository.save`` for the statement that records one and
    #: ``PhoneVerificationRepository.claim_by_token_hash`` for the one that spends it.
    phone_verifications: PhoneVerificationRepository
    #: **Convenience, and deliberately so - it is the one repository here whose
    #: absence from the correctness group is worth arguing rather than
    #: noticing.**
    #:
    #: It is tempting to reach for the pattern by analogy: a tier is derived from
    #: this row, a tier decides how much money may move, so surely a profile write
    #: has to land with something money-shaped. It does not, and the reason is
    #: that **the profile is never read as part of a money movement's decision
    #: from outside that movement's own unit.** ``WalletService._run`` loads the
    #: profile *inside* the unit that moves the money, through this same unit, so
    #: the tier it enforces and the balance it debits were read at one moment.
    #: There is no second unit holding a stale tier, because no other code path
    #: carries one.
    #:
    #: What that buys is the honest failures rather than the atomic ones. A
    #: profile write that commits while a concurrent withdrawal is mid-flight
    #: means the withdrawal is judged against whichever tier it read - and both
    #: readings are *correct*, because a tier is not a state that has to change
    #: atomically with anything. There is no half-applied version of "this person
    #: gave us their name": the row is written whole by ``Profile.revise`` or not
    #: at all.
    #:
    #: The pressure to put it in the correctness group would come from wanting the
    #: tier *raised* before a movement is judged. That is a want, not an
    #: invariant - the safe direction is for a movement to be judged harshly by a
    #: stale tier, and that is the direction a lost race falls in.
    profiles: ProfileRepository

    @abstractmethod
    def commit(self) -> None:
        """Make all writes of this unit durable, atomically."""
        pass

    @abstractmethod
    def rollback(self) -> None:
        """Discard every write made since the unit started."""
        pass


class UnitOfWorkFactory(ABC):
    """Creates a fresh Unit of Work on demand.

    Each business operation should run in its own unit (its own connection and
    transaction), so callers depend on a factory rather than a shared instance.
    """

    @abstractmethod
    def start(self) -> UnitOfWork:
        pass
