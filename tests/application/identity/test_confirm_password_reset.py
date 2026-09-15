"""Answering a reset: the mailed code comes back, and the account's secret changes.

This operation replaces the thing an account is proved by, which makes it the most
consequential thing a mailed value does in this system. Four claims run through
the file:

**The id it acts on comes from the claimed row**, never from a request. That is
what keeps it out of the "no privileged actor" prohibition rather than inside it:
nothing here reaches an account without naming whose it is, and it names it by
proving it. A test below shows two accounts with two live codes and asserts that
the code decides which one moves - there is no parameter through which a caller
could substitute an account, because there is no account parameter.

**The claim, the credential and the revocations are one commit.** The tests that
pin it inject a failure between two of the three writes and then check that the
code is *still spendable* and the old password *still works* - the state a partial
write would have destroyed.

**A password the policy refuses does not spend the code**, and that is the one
place this flow deliberately diverges from the address change. The test is written
as a pair: the refusal, and then the same code succeeding with a longer password.

**The notice is best-effort.** It goes out after the reset is committed and its
failure is caught into the result, because refusing to change a password because
the warning about it bounced would strand the person the endpoint exists for.
"""

from datetime import datetime, timedelta

import pytest

from app.application.identity.confirm_password_reset import ConfirmPasswordReset
from app.application.identity.request_password_reset import RequestPasswordReset
from app.application.identity.sign_up import SignUp
from app.domain.identity.exception import (
    InvalidPasswordError,
    InvalidPasswordResetTokenError,
    PasswordResetAlreadyUsedError,
    PasswordResetExpiredError,
    UserNotFoundError,
    WeakPasswordError,
)
from app.domain.identity.password import PlainPassword
from app.domain.identity.passwordReset import PASSWORD_RESET_LIFETIME
from app.domain.identity.session import Session, hash_session_token
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
    open_sqlite_connection,
)
from app.infrastructure.repositories.sqlite_session_repository import (
    SqliteSessionRepository,
)
from app.infrastructure.repositories.sqlite_user_repository import SqliteUserRepository
from tests.conftest import RESET_CODE_LABEL, code_in

NOW = datetime(2026, 3, 2, 12, 0)
ANSWERED_AT = NOW + timedelta(minutes=1)
PASSWORD = "correct-horse-battery"
NEW_PASSWORD = "a-longer-and-different-one"
WEAK_PASSWORD = "short"
ADDRESS = "ada@example.com"
OTHER_ADDRESS = "grace@example.com"


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "identity.db")


@pytest.fixture
def factory(db_path):
    return SqliteUnitOfWorkFactory(db_path)


@pytest.fixture
def sign_up(factory, password_hasher):
    return SignUp(factory, password_hasher=password_hasher)


@pytest.fixture
def account(sign_up):
    """An ordinary account at a real address, registered the ordinary way.

    Registered through ``SignUp`` with the *same* hasher the confirm fixtures use,
    and that is load-bearing rather than tidy: the assertions about the old password
    still working are assertions about a stored hash, so a fixture that hashed with
    one object and verified with another would have to be checking something else.
    """
    return sign_up.execute(ADDRESS, PASSWORD, NOW)


@pytest.fixture
def requested(db_path, account, build_channel):
    """The account, a mailbox, and a live code sitting in it.

    The setup most tests below start from, in one place, so each test's body is the
    claim it is making. It depends on the ``account`` fixture rather than registering
    its own, because a second ``SignUp`` at the same address is a ``DuplicateEmail``
    - and the tests that want to name the account ask for both, which works because
    they are the same object.

    The request goes through the real ``RequestPasswordReset`` rather than writing a
    row by hand, so the code the tests answer is one that arrived the way a person's
    does: addressed by an address, hashed in the store, and readable only from the
    envelope.
    """
    channel = build_channel()
    RequestPasswordReset(
        SqliteUnitOfWorkFactory(db_path), channel=channel
    ).execute(ADDRESS, NOW)
    return account, channel


@pytest.fixture
def confirm(factory, password_hasher):
    """``ConfirmPasswordReset`` with an optional mailbox and **no actor**.

    The absence of an actor argument is the class's own design and this fixture makes
    it visible at every call site: there is nothing to say *who* is answering,
    because holding the code is the whole of the answer to that.
    """

    def _build(channel=None):
        return ConfirmPasswordReset(factory, password_hasher, channel=channel)

    return _build


def code_of(channel) -> str:
    """The reset code out of the one message a request sends.

    Its own helper rather than ``code_in(channel.sent[0], RESET_CODE_LABEL)`` written
    out at each call site, because the label is the reset's and *not* the address
    change's: the two bodies are otherwise alike enough that a shared prefix would
    find either, and a test that silently read a code out of the wrong message would
    not be told apart from a passing one.
    """
    return code_in(channel.sent[0], RESET_CODE_LABEL)


def sessions_of(db_path, user_id) -> int:
    """How many sessions the account holds, counted from a connection of its own."""
    connection = open_sqlite_connection(db_path)
    try:
        return connection.execute(
            "SELECT COUNT(*) FROM sessions WHERE user_id = ?", (str(user_id),)
        ).fetchone()[0]
    finally:
        connection.close()


def stored_status(db_path, user_id) -> str:
    """The raw status *text* of an account's request row.

    The stored spelling (``AWAITING``) rather than the enum's value (``awaiting``),
    because that is what ``enum_to_text`` writes and the whole point of asking the
    table is to see what is on disk rather than what a reader would derive. The
    derived ``EXPIRED`` never appears here, which is the property the repository's
    own tests pin.
    """
    connection = open_sqlite_connection(db_path)
    try:
        row = connection.execute(
            "SELECT status FROM password_resets WHERE user_id = ?", (str(user_id),)
        ).fetchone()
        return None if row is None else row["status"]
    finally:
        connection.close()


def credential_hash_for(db_path, user_id) -> str:
    connection = open_sqlite_connection(db_path)
    try:
        return connection.execute(
            "SELECT password_hash FROM password_credentials WHERE user_id = ?",
            (str(user_id),),
        ).fetchone()[0]
    finally:
        connection.close()


def start_sessions(db_path, user_id, count: int) -> list:
    """Mint ``count`` live sessions for an account, returning their tokens.

    Their tokens rather than their ids, because the point of these sessions is what a
    *bearer* can do - and the API-level test that follows a token to a 401 is the one
    that needs them. Here they are what makes "every one of them" countable from
    somewhere other than the count the use case reports.
    """
    factory = SqliteUnitOfWorkFactory(db_path)
    tokens = []
    uow = factory.start()
    try:
        for offset in range(count):
            session, token = Session.issue(user_id, NOW + timedelta(seconds=offset))
            uow.sessions.save(session)
            tokens.append(token)
        uow.commit()
    finally:
        uow.rollback()
    return tokens


class TestTheTokenIsTheWholeAuthorisation:
    """What the confirm does not ask for, and why that is not a hole."""

    def test_it_sets_the_new_password(
        self, db_path, account, confirm, password_hasher, requested
    ):
        confirm().execute(code_of(requested[1]), NEW_PASSWORD, ANSWERED_AT)

        assert password_hasher.verify(
            PlainPassword(NEW_PASSWORD),
            credential_hash_for(db_path, account.user_id),
        )

    def test_the_old_password_stops_working(
        self, db_path, account, confirm, password_hasher, requested
    ):
        """**The single observable fact the whole flow exists to produce.**

        Asserted beside the new one working rather than instead of it, because either
        alone passes for a wrong implementation: a credential row that was *deleted*
        would make the old password stop working and leave nobody able to log in, and
        an updated row is the only answer that satisfies both.
        """
        confirm().execute(code_of(requested[1]), NEW_PASSWORD, ANSWERED_AT)

        assert not password_hasher.verify(
            PlainPassword(PASSWORD), credential_hash_for(db_path, account.user_id)
        )

    def test_the_stored_hash_is_not_a_usable_credential(
        self, db_path, account, confirm, password_hasher, requested
    ):
        """**What makes a plain SHA-256 sufficient, and it is a property of *this* layer.**

        The table holds ``hash_session_token`` of the code that was mailed and never
        the code itself - pinned next door by the request suite - so a person who
        could read ``password_resets`` still cannot take an account. That works because
        the hash is always *derived from a value that was presented*: this use case
        hashes whatever arrives and looks *that* up, so handing it the stored value
        hashes the hash and matches no row.

        Pinned here rather than on the repository, and the placement is the assertion.
        The store's ``claim_by_token_hash`` takes a hash and hashes nothing - that is
        its contract, and the test beside it says so. The step that makes a stolen row
        useless is the ``hash_session_token`` call *above* it, so this is the only
        layer at which the claim can be made about the running system.
        """
        stored = hash_session_token(code_of(requested[1]))

        with pytest.raises(InvalidPasswordResetTokenError):
            confirm().execute(stored, NEW_PASSWORD, ANSWERED_AT)

        assert password_hasher.verify(
            PlainPassword(PASSWORD), credential_hash_for(db_path, account.user_id)
        )

    def test_it_needs_no_actor_and_no_old_password(self, account, confirm, requested):
        """**The signature is the assertion.**

        ``ConfirmPasswordReset`` is constructed with a unit of work, a hasher and
        nothing else - no ``actor``, no current password - and this test builds one
        that way and uses it. A change that grew either would have to come here and
        delete this, which is the conversation worth having: the code exists only
        because somebody could read the account's mail, so asking for a session on top
        would refuse exactly the person who asked at a desk and opened the mail on a
        phone. And the premise of the flow is that the old password is *lost*, so
        requiring it would be requiring the thing that is missing.
        """
        result = confirm().execute(code_of(requested[1]), NEW_PASSWORD, ANSWERED_AT)

        assert result.user.user_id == account.user_id

    def test_the_account_it_changes_is_the_one_the_row_names(
        self, db_path, account, build_channel, confirm, factory, password_hasher, sign_up, requested
    ):
        """**Two accounts, two live codes, and the presented code decides which moves.**

        There is no parameter through which a caller could name an account, and this is
        what that buys. A signature that took a ``user_id`` - which would look like the
        tidier interface, since every other use case here resolves an actor - would
        make naming somebody else's account a matter of passing the wrong value, and
        the thing being replaced is a password.
        """
        other = sign_up.execute(OTHER_ADDRESS, PASSWORD, NOW)
        RequestPasswordReset(factory, channel=build_channel()).execute(OTHER_ADDRESS, NOW)

        confirm().execute(code_of(requested[1]), NEW_PASSWORD, ANSWERED_AT)

        assert password_hasher.verify(
            PlainPassword(PASSWORD), credential_hash_for(db_path, other.user_id)
        )
        # And the account whose code *was* presented has moved, which is what makes
        # the assertion above a statement about separation rather than about inaction.
        assert password_hasher.verify(
            PlainPassword(NEW_PASSWORD), credential_hash_for(db_path, account.user_id)
        )


class TestEverySessionEnds:
    """The half of a reset that is about somebody else holding the old password."""

    def test_a_session_minted_before_the_reset_is_gone(
        self, db_path, account, confirm, requested
    ):
        """**The headline claim of the whole feature.**

        A session opened with the old password, still live after the password was
        replaced, is a way in that survived the lock being changed - and the premise of
        a reset is that somebody else may know the old password. So a device that was
        signed in when the reset happened must be signed out by it, and "signed out"
        here means the row is *deleted* rather than flagged, per decision 49.
        """
        start_sessions(db_path, account.user_id, 1)

        confirm().execute(code_of(requested[1]), NEW_PASSWORD, ANSWERED_AT)

        assert sessions_of(db_path, account.user_id) == 0

    def test_every_one_of_them(self, db_path, account, confirm, requested):
        """Not one, and not the newest: *every* session the account holds.

        A revocation that deleted a single row - or the row it happened to find first -
        would leave a person who reset from their laptop still signed in on the phone
        they lost, which is the concrete case the second ruling recorded against this
        feature is about.
        """
        start_sessions(db_path, account.user_id, 3)

        confirm().execute(code_of(requested[1]), NEW_PASSWORD, ANSWERED_AT)

        assert sessions_of(db_path, account.user_id) == 0

    def test_the_result_reports_how_many_ended(self, db_path, account, confirm, requested):
        """Reported rather than kept internal, because it is what makes revocation visible.

        A person who reset a password they had forgotten is told how many devices were
        signed out; a person who did *not* reset it and gets the notice has just learned
        their sessions ended, which is the warning this flow exists to produce. The
        count is also the only way a test can tell "revoked three" from "there was
        nothing to revoke" without opening the table.
        """
        start_sessions(db_path, account.user_id, 3)

        result = confirm().execute(code_of(requested[1]), NEW_PASSWORD, ANSWERED_AT)

        assert result.sessions_revoked == 3

    def test_zero_is_a_true_answer_and_not_a_failure(self, confirm, requested):
        """An account with no live sessions is ordinary, not an error.

        Somebody who lost their password is very often somebody who has just been
        signed out everywhere already, and a use case that raised or refused here would
        strand precisely that person. ``0`` is what happened, so ``0`` is what is
        reported.
        """
        result = confirm().execute(code_of(requested[1]), NEW_PASSWORD, ANSWERED_AT)

        assert result.sessions_revoked == 0

    def test_another_accounts_sessions_are_untouched(
        self, db_path, account, confirm, sign_up, requested
    ):
        """**The account-scoped method with the widest blast radius, controlled.**

        ``delete_by_user_id`` is the only method on the session port whose scope is an
        *account* rather than an actor, and it is safe only because the id comes off a
        claimed row. This is the test that would fail if it ever came off a request:
        two accounts, both signed in, one reset - and the other account is still signed
        in afterwards.
        """
        other = sign_up.execute(OTHER_ADDRESS, PASSWORD, NOW)
        start_sessions(db_path, account.user_id, 2)
        start_sessions(db_path, other.user_id, 2)

        confirm().execute(code_of(requested[1]), NEW_PASSWORD, ANSWERED_AT)

        assert sessions_of(db_path, account.user_id) == 0
        assert sessions_of(db_path, other.user_id) == 2


class TestTheThreeRefusals:
    """Three classes, three messages, and a caller who learns nothing from holding one."""

    def test_a_code_that_names_nothing_is_refused(self, confirm):
        with pytest.raises(InvalidPasswordResetTokenError):
            confirm().execute("never-minted", NEW_PASSWORD, NOW)

    def test_a_code_already_answered_is_refused(self, confirm, requested):
        """**Distinguished from "that code means nothing", and the person benefits.**

        Reachable only because the row survives being spent, and worth more here than
        an address change's equivalent: somebody who has already reset their password
        and presents the same code again has, in all likelihood, forgotten which mail
        they answered. "You already did this" sends them to their mail for the *next*
        code rather than to the form for a new request.
        """
        code = code_of(requested[1])
        confirm().execute(code, NEW_PASSWORD, ANSWERED_AT)

        with pytest.raises(PasswordResetAlreadyUsedError):
            confirm().execute(code, NEW_PASSWORD, ANSWERED_AT + timedelta(minutes=1))

    def test_a_code_past_its_window_is_refused(self, confirm, requested):
        """At the instant it closes, not a moment later - the aggregate's own boundary.

        ``expires_at > ?`` in the claim and ``as_of >= expires_at`` in the aggregate are
        one rule written twice and the repository test pins the pair. What is asserted
        here is the consequence at this layer: the refusal is *about the window*, so the
        person is told to ask for a new code rather than told their code is meaningless.
        """
        with pytest.raises(PasswordResetExpiredError):
            confirm().execute(
                code_of(requested[1]), NEW_PASSWORD, NOW + PASSWORD_RESET_LIFETIME
            )

    def test_a_refused_answer_leaves_the_password_alone(
        self, db_path, account, confirm, password_hasher, requested
    ):
        """Every refusal above is in the same family, and this is the shared half.

        A refusal that had already replaced the credential and then failed would be the
        worst possible outcome, because the error a person saw would be the only sign
        that the account *had* changed.
        """
        with pytest.raises(InvalidPasswordResetTokenError):
            confirm().execute("never-minted", NEW_PASSWORD, NOW)

        assert password_hasher.verify(
            PlainPassword(PASSWORD), credential_hash_for(db_path, account.user_id)
        )

    def test_the_expired_refusal_leaves_the_code_unspent(
        self, db_path, account, confirm, requested
    ):
        """**The window is a gate, not a side effect.**

        An ``UPDATE`` that matched no row because of the window changed nothing, so a
        request answered a second inside its window still works. Somebody whose clock
        was wrong, or who opened the mail a second too early, is not left with a dead
        code on account of having tried - and the row is still ``AWAITING`` on disk,
        which is the fact the successful retry below turns on.
        """
        code = code_of(requested[1])

        with pytest.raises(PasswordResetExpiredError):
            confirm().execute(
                code, NEW_PASSWORD, NOW + PASSWORD_RESET_LIFETIME + timedelta(seconds=1)
            )

        assert stored_status(db_path, account.user_id) == "AWAITING"

        result = confirm().execute(
            code, NEW_PASSWORD, NOW + PASSWORD_RESET_LIFETIME - timedelta(seconds=1)
        )

        assert result.sessions_revoked == 0


class TestAPasswordThePolicyRefuses:
    """**The one place this flow deliberately diverges from the address change.**"""

    def test_a_short_password_is_refused(self, confirm, requested):
        with pytest.raises(WeakPasswordError):
            confirm().execute(code_of(requested[1]), WEAK_PASSWORD, NOW)

    def test_an_empty_password_is_refused_by_the_same_rule(self, confirm, requested):
        """Emptiness is checked before length, so the two refusals stay distinguishable.

        ``""`` is not "too short" in any useful sense - it is a caller that passed
        nothing - and collapsing them would make a bug at the boundary look like a
        person choosing a bad password.
        """
        with pytest.raises(InvalidPasswordError):
            confirm().execute(code_of(requested[1]), "", NOW)

    def test_the_same_code_still_works_afterwards(
        self, db_path, account, confirm, password_hasher, requested
    ):
        """**The divergence, and the reason for it, in one test.**

        ``ConfirmEmailChange`` spends the request on the *attempt*, because the case it
        decides is a change in the world: the address was taken by somebody else while
        the request waited, so a retry with the same code could not succeed and keeping
        it alive would hold a dead credential. A password that fails the policy is a
        statement about the value the caller just typed, and a retry with a longer one
        succeeds. So the remedy is "type a longer password" rather than "ask for a new
        code" - and refusing without spending is the kinder and the more accurate
        answer, since the person is already holding a code they had to go and find.
        """
        code = code_of(requested[1])

        with pytest.raises(WeakPasswordError):
            confirm().execute(code, WEAK_PASSWORD, NOW)

        result = confirm().execute(code, NEW_PASSWORD, ANSWERED_AT)

        assert result.user.user_id == account.user_id
        assert password_hasher.verify(
            PlainPassword(NEW_PASSWORD), credential_hash_for(db_path, account.user_id)
        )

    def test_the_refusal_does_not_even_read_the_code(self, confirm):
        """**Checked before the claim, so a malformed call cannot spend a code.**

        The ordering is observable without a valid code at all: an empty password is
        refused even when the token names nothing, which is only possible if
        ``PlainPassword`` ran first. That is what makes the divergence structural rather
        than a branch - there is no path in which a password the policy refuses reaches
        the store, and therefore none in which it could spend a row on its way to being
        refused.
        """
        with pytest.raises(InvalidPasswordError):
            confirm().execute("never-minted", "", NOW)

    def test_the_same_password_can_be_chosen_again(
        self, db_path, account, confirm, password_hasher, requested
    ):
        """**Not compared against the old one, and that is not laziness.**

        Replacing a forgotten password with the string it already was is a legitimate
        thing to do - the person proved the mailbox and chose a secret - and refusing it
        would tell the bearer of the code something about the account's *current*
        password, which is exactly the fact a reset is supposed to be independent of.
        """
        result = confirm().execute(code_of(requested[1]), PASSWORD, ANSWERED_AT)

        assert result.user.user_id == account.user_id
        assert password_hasher.verify(
            PlainPassword(PASSWORD), credential_hash_for(db_path, account.user_id)
        )


class TestTheWritesLandTogether:
    """One commit, proved by breaking it between two of the three writes."""

    @staticmethod
    def break_session_deletion(monkeypatch):
        """Make the session sweep raise, which is the middle of the three writes.

        Patched on the *class* the unit of work really uses rather than on an instance
        fetched by starting a second unit, so the failure lands inside the transaction
        under test rather than in a connection of its own. The gap is deliberately the
        riskiest one - between the new credential and the revocation - because that is
        where a partial write would leave a new password alongside a session that still
        works.
        """

        def refuse(_user_id):
            raise RuntimeError("the database went away mid-confirm")

        # ``staticmethod`` and not the bare function, on ``test_confirm_email_change``'s
        # pattern. A plain function assigned to a class attribute is a descriptor, so
        # the instance would be bound into the first parameter and ``refuse`` would be
        # called with ``(self, user_id)`` - a ``TypeError`` raised by the stub itself,
        # which reads as a failure of the code under test rather than of the seam.
        monkeypatch.setattr(
            SqliteSessionRepository, "delete_by_user_id", staticmethod(refuse)
        )

    def test_the_code_is_still_spendable(
        self, db_path, account, confirm, monkeypatch, requested
    ):
        """Nothing committed, so the row is still ``AWAITING`` on disk.

        A use case that committed the claim early, or a store that committed per
        statement, would leave the person holding a spent code and an unchanged
        account - the state the successful retry below would then be unable to reach.
        """
        self.break_session_deletion(monkeypatch)
        code = code_of(requested[1])

        with pytest.raises(RuntimeError):
            confirm().execute(code, NEW_PASSWORD, ANSWERED_AT)

        monkeypatch.undo()
        assert stored_status(db_path, account.user_id) == "AWAITING"

        result = confirm().execute(code, NEW_PASSWORD, ANSWERED_AT)

        assert result.user.user_id == account.user_id

    def test_the_old_password_still_works(
        self, db_path, account, confirm, monkeypatch, password_hasher, requested
    ):
        """The other half of the same claim, and the one a person would feel.

        A partially-applied reset is a person who believes their password changed and
        whose old one still works - which is worse than an error, because nothing tells
        them to try again.
        """
        self.break_session_deletion(monkeypatch)

        with pytest.raises(RuntimeError):
            confirm().execute(code_of(requested[1]), NEW_PASSWORD, ANSWERED_AT)

        monkeypatch.undo()
        assert password_hasher.verify(
            PlainPassword(PASSWORD), credential_hash_for(db_path, account.user_id)
        )

    def test_a_vanished_account_is_reported_as_the_codes_own_refusal(
        self, db_path, account, confirm, monkeypatch, requested
    ):
        """**One class for "no such code" and "that account is gone", deliberately.**

        A caller who could tell them apart has an oracle - it would answer whether an
        address still names an account, from an endpoint anybody can reach. So the
        absence of the account is reported in the token's own vocabulary, exactly as
        ``InvalidSessionError`` does for a session whose user was deleted.

        Note what this also proves: the refusal *unwinds the claim*. The row goes back
        to ``AWAITING`` rather than being committed as spent, which is safe because there
        is no longer an account for any retry to reach - and is right, because a spent
        code for an account that does not exist is a fact nobody can act on.
        """

        def vanish(_user_id):
            raise UserNotFoundError("no such user")

        # ``staticmethod`` for the reason ``break_session_deletion`` gives above.
        monkeypatch.setattr(SqliteUserRepository, "get_by_id", staticmethod(vanish))

        with pytest.raises(InvalidPasswordResetTokenError):
            confirm().execute(code_of(requested[1]), NEW_PASSWORD, ANSWERED_AT)

        monkeypatch.undo()
        assert stored_status(db_path, account.user_id) == "AWAITING"


class TestTheNotice:
    """Three states in two fields, cloned from the address change's outcome."""

    def test_a_successful_reset_is_announced(self, account, confirm, build_channel, requested):
        channel = build_channel()

        result = confirm(channel=channel).execute(
            code_of(requested[1]), NEW_PASSWORD, ANSWERED_AT
        )

        assert result.notice_sent is True
        assert result.notice_error is None
        assert len(channel.sent) == 1
        assert channel.sent[0].recipient == ADDRESS

    def test_the_notice_says_the_sessions_were_ended(self, confirm, build_channel, requested):
        """The actionable half of the message, and the only warning a person gets.

        Somebody whose password was set by somebody else needs two things: to know it
        happened, and to know that whatever device they are reading on has just been
        signed out. The second is the sentence that makes them go and look.
        """
        channel = build_channel()

        confirm(channel=channel).execute(code_of(requested[1]), NEW_PASSWORD, ANSWERED_AT)

        assert "signed out" in channel.sent[0].body

    def test_the_notice_does_not_count_the_devices(
        self, db_path, account, confirm, build_channel, requested
    ):
        """**The count is reported to the client and deliberately not mailed.**

        "Three sessions were ended" would tell a reader who did not ask for this how many
        devices the account was signed in on - a fact about the person, disclosed to
        whoever happens to be reading that mailbox. The count is in the result, where a
        person is looking at their own screen.
        """
        start_sessions(db_path, account.user_id, 3)
        channel = build_channel()

        result = confirm(channel=channel).execute(
            code_of(requested[1]), NEW_PASSWORD, ANSWERED_AT
        )

        assert result.sessions_revoked == 3
        # Spelled with the word it would be counted *in* rather than as a bare "3".
        # A bare digit is the assertion that looks right and is not: the body opens
        # with the moment the change happened, and this suite's fixed clock renders
        # it as ``2026-03-02T12:01`` - so "3" is in the message for a reason that has
        # nothing to do with sessions, and the assertion would pass or fail on the
        # date a future test happened to be pinned to.
        assert "3 session" not in channel.sent[0].body
        assert "sessions were" not in channel.sent[0].body

    def test_a_bounced_notice_is_reported_and_does_not_undo_the_reset(
        self, db_path, account, confirm, password_hasher, build_channel, requested
    ):
        """**The asymmetry with the reset mail, stated as a behaviour.**

        That mail carries the code, so a failure means nobody can answer and it raises.
        This one announces something already done and impossible to undo - and refusing
        to apply a reset because the warning about it bounced would be the worst possible
        trade: the person the endpoint exists for would be left locked out with the
        password unchanged and the code that would have changed it spent.
        """
        channel = build_channel(failures=[OSError("mailbox full")])

        result = confirm(channel=channel).execute(
            code_of(requested[1]), NEW_PASSWORD, ANSWERED_AT
        )

        assert result.notice_sent is False
        assert "mailbox full" in result.notice_error
        assert password_hasher.verify(
            PlainPassword(NEW_PASSWORD), credential_hash_for(db_path, account.user_id)
        )

    def test_no_channel_means_nothing_was_attempted(self, confirm, requested):
        """``False`` with no reason, and the third state is the one that could be misread.

        This is what an installation whose SMTP was unset *between* the request and the
        answer looks like, and it must not read as a failure: nothing was attempted, so
        there is no bounce to report. The reset itself is unaffected.
        """
        result = confirm().execute(code_of(requested[1]), NEW_PASSWORD, ANSWERED_AT)

        assert result.notice_sent is False
        assert result.notice_error is None

    def test_a_channel_that_raises_something_undeclared_is_still_caught(
        self, confirm, build_channel, requested
    ):
        """**Broad on purpose, because the reset has already happened.**

        A channel is third-party code talking to a network, and an exception type the
        adapter does not declare would become a 500 *after* the password had been
        replaced and the sessions already ended - reporting a reset that happened as a
        failure. Narrowing the catch to the declared type would let exactly that through.
        """
        channel = build_channel(failures=[KeyError("a shape the adapter did not expect")])

        result = confirm(channel=channel).execute(
            code_of(requested[1]), NEW_PASSWORD, ANSWERED_AT
        )

        assert result.notice_sent is False
        assert result.notice_error
