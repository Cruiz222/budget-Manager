"""Asking to set a new password, with nothing to prove but an address.

This is the loosest entry point in the codebase and the only operation a stranger
can start that writes a row and is not a sign-up, so most of this file is about the
edges of that:

**The unknown address is not an error.** It writes nothing, mails nothing, and
returns the same value shape as a known one - with ``requested`` false. The route
above this turns both into the same bytes, and the test that pins *that* lives in
``tests/presentation/api/test_password_resets.py``; what is pinned here is that the
use case gives it nothing to distinguish them by.

**The token leaves by exactly one route**: into one envelope, addressed to the
account's own address. Not through the outcome, not through a return value, not
into a log - so a test that wants to answer the request reads the code out of the
mailbox, which is the constraint the person is under too.

**The row is durable before the mail goes out.** That ordering is what makes a
failed send survivable, and the test that pins it asks the store from *inside* the
channel's ``send``, which is the only moment at which the ordering is observable.

**With no mail account there is no flow at all**, and that is where this differs
from an address change rather than merely resembling it: an address change can be
authorised by the password proof alone, and a forgotten password has none. So the
refusal names the missing setting and the test says so in those words.
"""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from app.application.identity.request_password_reset import (
    PasswordResetOutcome,
    RequestPasswordReset,
)
from app.application.identity.sign_up import SignUp
from app.domain.identity.exception import NoMailAccountError
from app.domain.identity.password import PlainPassword
from app.domain.identity.password_credential import PasswordCredential
from app.domain.identity.passwordReset import PASSWORD_RESET_LIFETIME
from app.domain.identity.session import hash_session_token
from app.domain.identity.user import User
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
    open_sqlite_connection,
)
from tests.conftest import RESET_CODE_LABEL, FakeChannel, code_in

NOW = datetime(2026, 3, 2, 12, 0)
PASSWORD = "correct-horse-battery"
ADDRESS = "ada@example.com"
UNKNOWN_ADDRESS = "nobody@unknown.invalid"
REASON = "this installation has no mail account, so a password cannot be reset: SMTP_HOST is not set"


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
    """An ordinary account at a real address, registered the ordinary way."""
    return sign_up.execute(ADDRESS, PASSWORD, NOW)


@pytest.fixture
def request_reset(factory):
    """``RequestPasswordReset`` with an optional mailbox and optional reason.

    No actor parameter anywhere, and that absence is the class's own design rather
    than a shortcut in the fixture - see its docstring. A test that wants to act as
    somebody has nothing to say here, because there is no "as somebody" in this
    flow at all.
    """

    def _build(channel=None, unconfigured_reason=None):
        return RequestPasswordReset(
            factory, channel=channel, unconfigured_reason=unconfigured_reason
        )

    return _build


def seed_account(
    db_path, email, user_id=None, with_password=False, password_hasher=None, phone=None
):
    """Write an account straight into the store, with or without a credential.

    **``with_password`` defaults to false and that is the interesting dial.** An
    account with no credential is one that has never had a password - a Google
    sign-in account, once that lands - and this flow is the only way such an account
    can acquire its first one. ``SignUp`` cannot build that state, so the row has to
    be written here.

    ``phone`` is a dial for the same class of reason: the reset flow's SMS branch
    needs a number on the account, and this is the only place a row is shaped by
    hand.
    """
    factory = SqliteUnitOfWorkFactory(db_path)
    uow = factory.start()
    try:
        user = User(
            user_id=user_id if user_id is not None else uuid4(),
            email=email,
            phone=phone,
            google_subject=None,
            created_at=NOW,
        )
        uow.users.save(user)
        if with_password:
            uow.password_credentials.save(
                PasswordCredential(
                    user_id=user.user_id,
                    password_hash=password_hasher.hash(PlainPassword(PASSWORD)),
                    updated_at=NOW,
                )
            )
        uow.commit()
    finally:
        uow.rollback()
    return user


def resets_in(db_path) -> int:
    """How many requests the store holds, asked *around* the port rather than through it.

    ``PasswordResetRepository`` deliberately has no ``find`` - see its docstring - so
    a test that needs to know whether a row exists *at all* has to open the file.
    That is the right trade here for the reason the sibling file gives: the absence
    of a write is precisely the property under test, and a port method added so a
    test could assert it would be a method with no production caller.
    """
    connection = open_sqlite_connection(db_path)
    try:
        return connection.execute(
            "SELECT COUNT(*) FROM password_resets"
        ).fetchone()[0]
    finally:
        connection.close()


class TestThePendingOutcome:
    """What asking produces when the installation can send mail: a request, not a reset."""

    def test_it_records_a_request(self, request_reset, account, db_path, build_channel):
        request_reset(channel=build_channel()).execute(ADDRESS, NOW)

        assert resets_in(db_path) == 1

    def test_it_does_not_change_the_password(
        self, request_reset, account, factory, password_hasher, build_channel
    ):
        """**The whole point of the two-step flow, asserted at the store.**

        A request that replaced the password would make the mailed code decorative:
        anybody who could type an address would have taken the account, and the
        person who owns it would be locked out by a form they never filled in. The
        old password still verifies, unchanged, and that is what "nothing has
        happened yet" means in the mail's own words.
        """
        request_reset(channel=build_channel()).execute(ADDRESS, NOW)

        uow = factory.start()
        try:
            stored = uow.password_credentials.find_by_user_id(account.user_id)
        finally:
            uow.rollback()

        assert stored is not None
        assert password_hasher.verify(PlainPassword(PASSWORD), stored.password_hash)

    def test_the_outcome_carries_the_request_and_its_deadline(
        self, request_reset, account, build_channel
    ):
        """``requested`` and ``expires_at`` are read off ``reset``, not stored beside it.

        So the two cannot disagree, and the deadline a caller reports is the one the
        claim will enforce - ``expires_at`` is the same field the mail prints and the
        same one ``claim_by_token_hash`` tests against.
        """
        outcome = request_reset(channel=build_channel()).execute(ADDRESS, NOW)

        assert outcome.requested is True
        assert outcome.expires_at == NOW + PASSWORD_RESET_LIFETIME

    def test_it_mails_the_account_and_nobody_else(
        self, request_reset, account, build_channel
    ):
        """One message, to the address the account holds.

        One rather than "at least one", because a second send would be a second code
        with the same authority as the first - and there is no code in the notice,
        since the notice does not exist yet.
        """
        channel = build_channel()
        request_reset(channel=channel).execute(ADDRESS, NOW)

        assert len(channel.sent) == 1
        assert channel.sent[0].recipient == ADDRESS

    def test_the_recipient_is_the_accounts_address_and_not_what_was_typed(
        self, request_reset, account, build_channel
    ):
        """**Folded on the way in, so the two can differ, and the account's wins.**

        ``find_by_email`` folds the argument before looking, so a person who types
        their address with a capital or a stray space reaches their account. The mail
        must then go to the address the account *holds*, because that is the string
        its mail actually arrives at - and a `To:` header echoing whatever was typed
        would be this system promising to deliver to a spelling it never checked.
        """
        channel = build_channel()
        request_reset(channel=channel).execute("  ADA@Example.COM  ", NOW)

        assert len(channel.sent) == 1
        assert channel.sent[0].recipient == ADDRESS

    def test_the_mailed_code_is_the_one_the_stored_hash_would_mint(
        self, request_reset, account, db_path, build_channel
    ):
        """**The other half of "the code exists in exactly one place".**

        The repository test next door pins that the *store* hashes nothing. This
        pins the complementary fact at this layer: the value in the envelope hashes
        to the value in the column, so the code a person reads out of their mail is
        the one the claim will match - and nothing else in the system could have
        produced it, because ``issue`` returns the plaintext once and to this method
        alone.
        """
        channel = build_channel()
        request_reset(channel=channel).execute(ADDRESS, NOW)
        mailed = code_in(channel.sent[0], RESET_CODE_LABEL)

        connection = open_sqlite_connection(db_path)
        try:
            stored = connection.execute(
                "SELECT token_hash FROM password_resets"
            ).fetchone()[0]
        finally:
            connection.close()

        assert stored == hash_session_token(mailed)

    def test_the_stored_row_carries_a_hash_and_not_the_mailed_code(
        self, request_reset, account, db_path, build_channel
    ):
        """**What makes a leaked database not a set of live reset codes.**

        A person who could read ``password_resets`` - a backup, an operator with a
        shell, a bug that dumps a table - holds a hash and not a credential. It is
        the same property ``sessions`` and ``email_changes`` have, and it is worth
        asserting on this table separately because what it protects here is a
        password: a leaked reset code is not one session, it is the account.
        """
        channel = build_channel()
        request_reset(channel=channel).execute(ADDRESS, NOW)
        mailed = code_in(channel.sent[0], RESET_CODE_LABEL)

        connection = open_sqlite_connection(db_path)
        try:
            row = connection.execute(
                "SELECT * FROM password_resets"
            ).fetchone()
            dump = " ".join(str(value) for value in tuple(row))
        finally:
            connection.close()

        assert mailed not in dump
        assert hash_session_token(mailed) in dump

    def test_the_mail_names_the_command_that_answers_it(
        self, request_reset, account, build_channel
    ):
        """The mail is the only instruction a locked-out person gets.

        There is no link in it - this system does not know its own public address -
        so the sentence naming the command is the whole of the way back in, and a
        mail that omitted it would carry a code nobody could present.
        """
        channel = build_channel()
        request_reset(channel=channel).execute(ADDRESS, NOW)

        assert "budget-manager confirm-password-reset" in channel.sent[0].body

    def test_the_mail_says_that_nothing_has_happened(
        self, request_reset, account, build_channel
    ):
        """**A claim about the account's state, and it has to be true.**

        A reset code that is merely mailed changes nothing: the old password works
        and the sessions are live. Somebody who reads this mail and concludes
        otherwise reports a compromise that has not occurred - and the sentence is
        exactly the reassurance the flow is for, so it is asserted rather than left
        to whoever next rewords the message.
        """
        channel = build_channel()
        request_reset(channel=channel).execute(ADDRESS, NOW)

        body = channel.sent[0].body
        assert "nothing has happened and nothing will" in body
        assert "still works" in body


class TestTheAddressThatNamesNothing:
    """The case the whole response shape is designed around."""

    def test_it_writes_no_row(self, request_reset, db_path, build_channel):
        request_reset(channel=build_channel()).execute(UNKNOWN_ADDRESS, NOW)

        assert resets_in(db_path) == 0

    def test_it_mails_nothing(self, request_reset, build_channel):
        """Nothing at all, and not merely "no code".

        Sending a "no account here" mail would be a courtesy that leaks the answer
        to whoever typed the address, and it would also make this endpoint a way to
        put mail into any mailbox in the world by typing an address - the abuse a
        rate limiter is for. So the correct number of messages is zero.
        """
        channel = build_channel()
        request_reset(channel=channel).execute(UNKNOWN_ADDRESS, NOW)

        assert channel.attempts == []

    def test_it_is_not_an_error(self, request_reset, build_channel):
        """An exception here would be the enumeration oracle in its plainest form.

        A 404 for an unknown address and a 202 for a known one is a lookup table for
        anybody with a list of addresses, and the mistake is an easy one to make:
        raising is what the *sibling* flow does, because there the address is a value
        about to be written down. Nothing is written here.
        """
        outcome = request_reset(channel=build_channel()).execute(UNKNOWN_ADDRESS, NOW)

        assert isinstance(outcome, PasswordResetOutcome)
        assert outcome.requested is False

    def test_the_outcome_cannot_say_more_than_the_route_may(
        self, request_reset, build_channel
    ):
        """``expires_at`` is ``None``, and ``requested`` is the only positive fact.

        **This is the shape that keeps the two presentations honest.** The route
        ignores ``requested`` entirely; the CLI prints it. Both read the same value,
        and the value cannot express "a code was sent to an account whose deadline
        is..." without the request itself - so there is no second place in which the
        known and unknown answers could drift apart.
        """
        outcome = request_reset(channel=build_channel()).execute(UNKNOWN_ADDRESS, NOW)

        assert outcome.reset is None
        assert outcome.expires_at is None
        assert outcome.requested is False

    def test_a_malformed_address_is_treated_as_one_that_names_nothing(
        self, request_reset, db_path, build_channel
    ):
        """**Not refused, and the difference from the sibling flow is deliberate.**

        ``RequestEmailChange`` checks the address is well formed, because there the
        address is a value the account would come to *hold*. Here nothing is written
        down, so the shape rule belongs where it already lives - ``User``, which
        every account that exists passed through. A check here would be a second
        copy of that rule, free to disagree with the first, and its disagreement
        would show up as an address this system accepts at sign-up and refuses at
        reset.
        """
        channel = build_channel()
        outcome = request_reset(channel=channel).execute("not-an-address", NOW)

        assert outcome.requested is False
        assert resets_in(db_path) == 0
        assert channel.attempts == []


class TestAnAccountWithNoPassword:
    """The Google-account case, decided before the account type exists."""

    def test_it_is_still_mailed(self, request_reset, db_path, build_channel):
        """**Refused would be the tidier rule and the worse answer.**

        An account with no credential has no old password to be locked out of, but it
        also has no way to acquire one - so refusing here would strand it for ever.
        And the refusal would have to be *silent* to keep the response identical for
        an unknown address, which turns the case into a person staring at a form that
        accepts their address and does nothing.
        """
        seed_account(db_path, ADDRESS)

        channel = build_channel()
        outcome = request_reset(channel=channel).execute(ADDRESS, NOW)

        assert outcome.requested is True
        assert len(channel.sent) == 1

    def test_the_row_is_written_as_for_any_other_account(
        self, request_reset, db_path, build_channel
    ):
        """Nothing about this branch is special-cased, and this is what says so.

        A `password_credentials` lookup before the write would be a read this use case
        does not need, and it would be the read that produced a different answer for
        this account than for any other.
        """
        seed_account(db_path, ADDRESS)

        request_reset(channel=build_channel()).execute(ADDRESS, NOW)

        assert resets_in(db_path) == 1


class TestAskingTwice:
    """One pending reset per account, and the second code is the live one."""

    def test_it_leaves_one_row(self, request_reset, account, db_path, build_channel):
        request_reset(channel=build_channel()).execute(ADDRESS, NOW)
        request_reset(channel=build_channel()).execute(ADDRESS, NOW + timedelta(minutes=1))

        assert resets_in(db_path) == 1

    def test_the_second_send_carries_a_different_code(
        self, request_reset, account, build_channel
    ):
        """**Because a resend that reused the code would be a resend that did nothing.**

        The row is replaced, which kills the first code the instant the second
        commits - so a second mail carrying the *same* code would be a mail whose
        code had already been invalidated by its own arrival. The person would
        present it and be told it means nothing.
        """
        channel = build_channel()
        request_reset(channel=channel).execute(ADDRESS, NOW)
        request_reset(channel=channel).execute(ADDRESS, NOW + timedelta(minutes=1))

        first = code_in(channel.sent[0], RESET_CODE_LABEL)
        second = code_in(channel.sent[1], RESET_CODE_LABEL)

        assert first != second

    def test_both_mails_are_sent(self, request_reset, account, build_channel):
        """The supersede happens in the store; nothing is suppressed at this layer.

        A use case that refused to send while a request was outstanding would need to
        ask the store whether one was - and that read would be a place for the two
        halves to disagree, since the only thing that makes a request "outstanding"
        is whether its window has closed, which is a clock.
        """
        channel = build_channel()
        request_reset(channel=channel).execute(ADDRESS, NOW)
        request_reset(channel=channel).execute(ADDRESS, NOW + timedelta(minutes=1))

        assert len(channel.sent) == 2

    def test_a_second_accounts_request_still_sends(
        self, request_reset, db_path, build_channel
    ):
        """The key is the account, so one person asking does not silence another.

        A store keyed on anything global - or an early return keyed on "a row exists"
        rather than on "this account has a row" - would pass every test above in a
        database with one account in it, and would silently stop mailing everybody
        else. This is the test that rules that out.
        """
        seed_account(db_path, ADDRESS)
        seed_account(db_path, "grace@example.com")
        channel = build_channel()

        request_reset(channel=channel).execute(ADDRESS, NOW)
        request_reset(channel=channel).execute("grace@example.com", NOW)

        assert len(channel.sent) == 2
        assert resets_in(db_path) == 2


class TestTheOrderOfTheRowAndTheMail:
    """The row is on disk before the code is on a wire."""

    def test_the_request_is_committed_before_the_send(
        self, request_reset, account, db_path
    ):
        """**Asked from inside ``send``, which is the only instant it is observable at.**

        A channel that opened the database *during* the send and found no row would
        prove the opposite ordering, and the failure it would represent is not
        theoretical: a code that left before its row was durable is a code for a
        request that does not exist, so the person presents it exactly as instructed
        and is told it means nothing. The other direction - a row whose mail never
        arrived - expires on its own and is superseded on the next attempt.
        """

        class CountingChannel(FakeChannel):
            def __init__(self):
                super().__init__()
                self.rows_at_send: list = []

            def send(self, message) -> None:
                self.rows_at_send.append(resets_in(db_path))
                super().send(message)

        channel = CountingChannel()
        request_reset(channel=channel).execute(ADDRESS, NOW)

        assert channel.rows_at_send == [1]

    def test_a_failed_send_raises(self, request_reset, account, build_channel):
        """**This mail carries a credential somebody is waiting for.**

        Letting the failure pass quietly would leave the person watching a mailbox
        for a mail that is not coming, while the account stays shut - which is worse
        than an error, because it looks like progress. So it propagates, matching
        ``RequestEmailChange``, and the route above turns it into a 500.
        """
        channel = build_channel(failures=[OSError("connection refused")])

        with pytest.raises(OSError):
            request_reset(channel=channel).execute(ADDRESS, NOW)

    def test_the_row_survives_a_failed_send(
        self, request_reset, account, db_path, build_channel
    ):
        """**The survivable half of that ordering, stated rather than implied.**

        What is left behind is a row holding a hash of a code nobody has - harmless,
        because it can never be claimed, and superseded the moment the person asks
        again. A rollback on send failure would leave nothing at all, which is the
        same state from the person's side and would lose the record that somebody
        asked.
        """
        channel = build_channel(failures=[OSError("connection refused")])

        with pytest.raises(OSError):
            request_reset(channel=channel).execute(ADDRESS, NOW)

        assert resets_in(db_path) == 1


class TestTheInstallationWithNoMailAccount:
    """Where this flow differs from the address change rather than resembling it."""

    def test_it_refuses(self, request_reset, account):
        with pytest.raises(NoMailAccountError):
            request_reset().execute(ADDRESS, NOW)

    def test_the_refusal_names_the_missing_setting(self, request_reset, account):
        """**One sentence, composed by the builder, rendered by both presentations.**

        The reason is passed in because a use case is not allowed to read the
        environment - ``describe_configuration`` is the only thing that does - and it
        is the *builder* that composes it so that the API's 503 and the CLI's exit 1
        carry the same words. A reason invented here would be a second copy of the
        configuration vocabulary, free to name a variable that does not exist.
        """
        with pytest.raises(NoMailAccountError) as refused:
            request_reset(unconfigured_reason=REASON).execute(ADDRESS, NOW)

        assert REASON in str(refused.value)
        assert "SMTP_HOST" in str(refused.value)

    def test_the_fallback_sentence_still_says_something_useful(self, request_reset, account):
        """For the one caller that could forget to pass a reason: a test.

        Degrading into a worse message rather than into a ``TypeError``, so that a
        hand-built use case refuses for the right *reason* even when it cannot name
        the right variable.
        """
        with pytest.raises(NoMailAccountError) as refused:
            request_reset().execute(ADDRESS, NOW)

        assert "no mail account" in str(refused.value)

    def test_it_writes_nothing(self, request_reset, account, db_path):
        """Refused before a unit is opened, so there is no row to find.

        The refusal depends on nothing about the input, so getting past it teaches a
        caller nothing - and the row that is *not* written is what makes this a clean
        refusal rather than a request nobody can answer.
        """
        with pytest.raises(NoMailAccountError):
            request_reset().execute(ADDRESS, NOW)

        assert resets_in(db_path) == 0

    def test_the_refusal_is_keyed_on_the_channel_and_not_on_the_reason(
        self, request_reset, account, build_channel
    ):
        """**The condition from the side that shows what it is keyed on.**

        The refusal is the absence of a ``channel``, and the reason is only the
        sentence it is refused *with* - which is the arrangement a production caller
        cannot separate, because ``_no_mail_account_reason`` returns a reason exactly
        when it returns no channel. A hand-built use case can separate them, and this
        is what it says when it does: a channel that exists is a capability, so the
        request goes through and one message is sent.

        The alternative reading - refuse whenever a reason was passed - would make
        the two signals a conjunction and would refuse an installation that had just
        been configured, which is the state a stale reason would describe. Pinning
        the precedence here is what stops somebody "tidying" the check into that.
        """
        channel = build_channel()

        outcome = request_reset(channel=channel, unconfigured_reason=REASON).execute(
            ADDRESS, NOW
        )

        assert outcome.requested is True
        assert len(channel.sent) == 1

    def test_it_refuses_a_stranger_address_too(self, request_reset):
        """**Before the lookup, so the refusal cannot become an oracle.**

        The check is first in ``execute`` precisely so that it depends on nothing
        about the address. A version that looked the address up first and refused
        only when an account was found would answer 503 for a real address and 202
        for an unknown one - an enumeration oracle built out of an *error*, which is
        the more conspicuous of the two answers.
        """
        with pytest.raises(NoMailAccountError):
            request_reset(unconfigured_reason=REASON).execute(UNKNOWN_ADDRESS, NOW)
