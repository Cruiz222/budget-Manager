"""Proving an identity with a password.

Handing out a session is no longer this module's alone. ``LogInWithGoogle`` issues
one from a Google id_token, and the shared half - building a ``Session``, saving
it, committing, returning a ``LoggedIn`` - is the same act reached by a different
proof. The two are separate use cases rather than one with a branch, for the reason
``sign_up`` gives about find-or-create: the proof a caller offers is the thing
that decides which operation they are performing, and a single entry point taking
"an identifier and a password, or a token" would be two operations wearing one
name.

What is *shared* between them is the refusal, and only the refusal is shared. The
timing oracle this module spends real effort closing does not exist on the Google
path at all: there is no stored hash to compare against, so there is no branch
that returns early when no credential is found, and the token's verification costs
the same whether or not the subject is known. That asymmetry is why these are two
modules instead of one.
"""

from dataclasses import dataclass
from datetime import datetime

from app.application.unit_of_work import UnitOfWorkFactory
from app.domain.identity.exception import InvalidCredentialsError
from app.domain.identity.password import PlainPassword
from app.domain.identity.password_hasher import DUMMY_HASH, PasswordHasher
from app.domain.identity.session import Session
from app.domain.identity.user import User

#: The one sentence every refusal on this path uses, kept in one place rather than
#: written out at each branch.
#:
#: **A constant because the property is about the words themselves.** "An unknown
#: identifier and a wrong password are indistinguishable" - decision 55 - is true
#: only for as long as the two answers match exactly, and a rule enforced by
#: repetition is one edit away from being false: the third branch grows a
#: clarifying clause, no test that reads a single message notices, and the oracle
#: is back. There are four paths to it below - two entry points, each able to find
#: no account or the wrong password - and a fifth is the kind of change somebody
#: makes while adding a feature.
#:
#: **It is public, and it became public with Google sign-in.** ``LogInWithGoogle``
#: raises ``InvalidCredentialsError`` with these same words for a different fact -
#: a verified Google identity that names no account here - and it *imports* them
#: rather than composing its own. That is a deliberate reversal of the leading
#: underscore: a name imported across a module boundary is part of what that
#: module offers, and leaving it private while reaching for it from elsewhere is a
#: comment saying "do not do this" that the code then does anyway.
#:
#: What did **not** change is the test's copy of it.
#: ``tests/application/identity/test_log_in.py`` still writes the sentence out by
#: hand, and should: a test that imported this would agree with it by
#: construction, so the thing worth pinning - that the *value* does not drift -
#: would stop being checked at all. Production shares it because drift there is a
#: bug; the suite duplicates it because drift there is the signal.
REFUSAL = "those details did not match an account"


@dataclass(frozen=True)
class LoggedIn:
    """What a successful login hands back: who, until when, and the token.

    A named value rather than a three-tuple, and the reason is that its three
    fields are three different *kinds* of thing sitting in a fixed order - an
    identity, an expiry, and the one secret in the system that is not a hash.
    ``user, session, token = log_in.execute(...)`` is one transposition away from
    unpacking to "wrong but the right types" in a way nothing would catch, and a
    reader of the call site has to hold the order in their head to know which is
    which. Here the names are the order.

    It is also the boundary between the two halves of what a login produces:
    ``session`` and ``token`` are the same fact in its storable and its
    presentable form, and ``user`` is the identity that fact is about. Only the
    caller knows which of the two forms it wants - the API sends the token and
    drops the session, the CLI writes the token to a file and drops both - and
    this value lets each say so by name.

    Frozen, so a caller cannot reach in and swap the session for another one
    while keeping the token that matches it.
    """

    user: User
    session: Session
    token: str


class LogIn:
    """Exchange an identifier and a password for a session and its token.

    **This is the last moment the password exists in plaintext anywhere in the
    system.** It arrives as a string, is wrapped so the policy can judge it, is
    handed to the hasher to compare, and is never written, logged or returned.
    What leaves is a token, which is not a password and cannot be turned into
    one.

    **An identifier is an address or a number, and which one it is is stated
    rather than guessed.** The two have separate entry points - ``execute`` and
    ``execute_for_phone`` - and neither sniffs its argument for an ``@``. A guess
    is wrong in both directions: an address is not obliged to contain one, so the
    guess would refuse an account that exists, and on the day a number arrived
    with an ``@`` in it the question "which kind of identifier is this" would have
    become a question about the *shape of a string* instead of about what the
    caller holds. ``POST /sessions`` says the kind in its body and the CLI says it
    in the command name; here it is the method name, and a caller cannot reach the
    wrong lookup without having written down which one it wanted.

    **It answers one refusal for four situations.** An identifier with no account
    and an identifier whose password is wrong both raise ``InvalidCredentialsError``
    with the same words - decision 55, a foreign wallet reporting as a missing
    one, applied to the thing that hands out identities. Distinguishing them
    would let anyone with a list of addresses learn which ones are registered
    without ever guessing a password, and there is nothing a legitimate client
    would do differently with the two answers, since both mean "check what you
    typed". **The cross-identifier case is one of those situations and not a
    fifth**: an address presented for an account whose identifier is a number is
    simply an identifier with no account, and it gets that sentence. A distinct
    answer there would be worse than a leak about one account - it would announce
    that the value is *half* registered, which is a fact about the system rather
    than about the guess.

    **That protection is on the words, and since this step it is on the clock as
    well.** For most of this system's life an unknown address returned without
    hashing anything, so it answered in microseconds where a wrong password took
    tens of milliseconds - and the difference was measurable from outside. That
    gap was named here as owed to 2c, and it is closed in ``_settle`` below rather
    than left owed, because **this step is what makes it cheap to measure.** A
    phone number is a small, structured, enumerable space: ten digits behind a
    known prefix, one handset each, and no free-text breadth to hide in - so the
    walk that was impractical against a list of mailboxes is a loop against a
    number range. What is *not* claimed is that the oracle is closed: the store
    lookup still differs by microseconds between the branches, and rate limiting
    remains the direct answer to enumeration, which is also the only thing that
    covers the sign-up path. The claim is the narrow one - an unknown identifier
    and a wrong password now cost the same order of time instead of two orders
    apart.
    """

    def __init__(
        self, unit_of_work_factory: UnitOfWorkFactory, password_hasher: PasswordHasher
    ):
        self._unit_of_work_factory = unit_of_work_factory
        self._password_hasher = password_hasher

    def execute(self, email: str | None, password: str, now: datetime) -> LoggedIn:
        """Return the account this address and password belong to, and a session.

        Raises ``InvalidCredentialsError`` if they do not match an account, and
        whatever ``PlainPassword`` raises for a password that could not be one -
        a value of the wrong type, or one outside the length policy. The second
        is worth being explicit about: a three-character password is refused as a
        *malformed* password rather than as a wrong one, and that leaks nothing
        because the length policy is public and every stored password satisfies
        it. Reporting "those details did not match" for a password that could
        never have matched would send somebody to reset a password they had typed
        correctly.

        **``None`` is accepted, and it is the honest type for this argument.** An
        absent address names no account - which is what ``find_by_email`` answers
        for ``None``, and what the shared refusal is for - so a caller that holds
        a number instead and takes the other branch is not one line away from a
        ``TypeError`` reported as a 500. This is the same guard, one layer down
        and for the same reason: the body of ``POST /sessions`` carries exactly
        one identifier, and a route built as "if it named a number, do that, else
        do this" reaches here with ``None`` on the path where its own invariant
        says it cannot. The invariant is enforced where it is stated; what
        arrives here if it is ever wrong is a refusal rather than a crash.

        **A new session every time, and the old ones are left alone.** Logging in
        twice from two terminals produces two rows, and this is deliberate rather
        than an oversight: the alternative is ending a session somebody is still
        using on another device, and "signing in logged me out somewhere else" is
        a worse surprise than a second row. Listing them and revoking one is a
        later feature; the store already supports it, since a session is a row
        and revocation is a deletion.

        ``now`` is passed in for the reason ``Session.issue`` takes it.
        """
        uow = self._unit_of_work_factory.start()
        try:
            return self._settle(uow, uow.users.find_by_email(email), password, now)
        finally:
            uow.rollback()

    def execute_for_phone(self, phone: str, password: str, now: datetime) -> LoggedIn:
        """Return the account this number and password belong to, and a session.

        ``execute``'s contract one identifier over, and the same refusal: this
        raises ``InvalidCredentialsError`` for a number with no account exactly as
        it does for a wrong password, and **a number presented for an account
        identified by an address gets that sentence too** - see the class
        docstring for why that case must not be distinguishable.

        **The number is folded by the lookup and not here.** ``find_by_phone``
        applies ``fold_phone`` - the same function ``User`` applies on
        construction - so ``0801 234 5678``, ``+2348012345678`` and
        ``2348012345678`` all find one account, which is the whole point of the
        fold and the reason a login typed in the form somebody happens to use
        cannot fail against an account that plainly exists.

        **A value that is not a number is not refused as malformed, and that is
        the same decision ``RequestPasswordReset`` records about a malformed
        address.** There is no shape check on this path, because ``checked_phone``
        belongs to ``User`` and every account that exists already passed through
        it; restating the rule here would be a second copy free to disagree with
        the first, and it would answer a *different* thing for a mistyped number
        than for an unknown one - which is a smaller version of the oracle the
        shared refusal exists to prevent. ``not-a-number`` names no account and is
        refused as one.

        A password that could not be one is still refused as malformed rather than
        as wrong, per ``execute``.
        """
        uow = self._unit_of_work_factory.start()
        try:
            return self._settle(uow, uow.users.find_by_phone(phone), password, now)
        finally:
            uow.rollback()

    def _settle(
        self, uow, user: User | None, password: str, now: datetime
    ) -> LoggedIn:
        """Turn a *looked-up* account into a session, or refuse in one sentence.

        **Both entry points reach this method with the account they found, which
        may be ``None``**, and that is why the lookup is the caller's and the
        refusal is this method's: there is exactly one place where a login can
        succeed and exactly one place where it can fail, so "the two refusals are
        indistinguishable" is a fact about the shape of the code rather than a
        property two branches have to be kept in agreement about.

        **The dummy hash is spent here, and the shape of the fix is worth
        reading.** When no credential is found - because no account matched, or
        because the account has none, which is what a Google sign-in produces -
        the presented password is still verified, against ``DUMMY_HASH``, and the
        answer is discarded. What that buys is the thing the class docstring
        argues: the two branches take the same time. What it costs is one hash on
        a path that used to skip it, which is the correct trade and is why the
        answer is thrown away rather than consulted: a password that happened to
        match the dummy's (unknown, discarded) preimage would return ``True``, and
        the branch below refuses on ``encoded is None`` regardless.

        **``PlainPassword`` is built before either lookup's answer is used**, and
        that is forced by the line above rather than chosen: the dummy comparison
        is a comparison, so the password has to be one. The visible consequence is
        that a too-short password is now refused as *malformed* even when the
        identifier names no account, where it used to be refused as *wrong*. That
        is the better answer of the two and it leaks nothing - the length policy is
        public, and a password failing it was never anybody's - so this is the
        behaviour ``execute``'s docstring already describes, arriving on the paths
        that had not reached it.
        """
        credential = (
            None
            if user is None
            else uow.password_credentials.find_by_user_id(user.user_id)
        )
        encoded = None if credential is None else credential.password_hash

        # One comparison on every path, which is the whole of the fix: with an
        # empty slot the presented password is checked against ``DUMMY_HASH`` and
        # the answer is discarded below. ``encoded is None`` rather than a falsy
        # test, so that a *corrupt* stored value still reaches the adapter and is
        # still the adapter's loud problem - see ``Argon2PasswordHasher.verify``.
        matched = self._password_hasher.verify(
            PlainPassword(password), DUMMY_HASH if encoded is None else encoded
        )
        # ``user is None`` is the first term although ``encoded is None`` already
        # covers it, and it is there to be read: it is what says the account below
        # exists, which the session's ``user.user_id`` depends on.
        if user is None or encoded is None or not matched:
            raise InvalidCredentialsError(REFUSAL)

        session, token = Session.issue(user.user_id, now)
        uow.sessions.save(session)
        uow.commit()
        return LoggedIn(user=user, session=session, token=token)
