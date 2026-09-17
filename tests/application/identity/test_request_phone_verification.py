"""Asking to prove a number, with nothing to prove but the number.

This is the second operation a stranger can start, and the shape of the file
follows ``test_request_password_reset.py`` because the two use cases follow each
other. Where they differ is where the interesting tests are, and there are four:

**Nothing is looked up.** A request for a number that already has an account is
not refused, not shortened, and not answered differently - it produces the same
row and the same text. The reset flow's version of that claim is that an unknown
address is treated as one naming nothing; this one is the mirror of it, and it is
the decision the whole two-step signup rests on: asking "is this number taken?"
here would make an unauthenticated endpoint an enumeration oracle over a space
that is small, structured and guessable.

**A malformed number is refused rather than folded into nothing.** Unlike an
address, a number is not a key being looked up - it is an address being dialled -
so there is no registered-or-not question a typo could be hiding, and telling
somebody their typo is a typo leaks nothing. The refusal is the *aggregate's*,
which is why this file asserts the exception ``User`` would raise rather than one
of its own.

**The code is a 64-character token and it is texted verbatim.** That is the one
decision this slice had to make about delivery, and the tests that pin it are
here rather than in a message test: what a person receives and what the store
can match are one claim, and the cost - a body that runs past one SMS segment
and therefore arrives in parts - is asserted rather than left to be discovered.

**The row is durable before the text goes out**, the same ordering the reset flow
argues, and it is pinned the same way: by asking the store from *inside* the
channel's ``send``, which is the only instant at which the ordering is visible.
"""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from app.application.identity.request_phone_verification import (
    RequestPhoneVerification,
)
from app.domain.identity.exception import (
    InvalidUserPhoneError,
    NoSmsAccountError,
)
from app.domain.identity.phoneVerification import (
    PHONE_VERIFICATION_LIFETIME,
    PhoneVerification,
)
from app.domain.identity.session import hash_session_token
from app.domain.identity.user import User
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
    open_sqlite_connection,
)
from tests.conftest import (
    SIGNUP_CODE_LABEL,
    FakeSmsChannel,
    code_in,
)

NOW = datetime(2026, 3, 2, 12, 0)

#: The number as a person in Nigeria types it, and the one every test below
#: starts from. The trunk ``0`` and the missing country code are the point of it:
#: what the store and the provider see is not what was typed, and several tests
#: here exist to say so.
TYPED = "08012345678"

#: What :data:`TYPED` folds to - country code first, digits only, no ``+``.
FOLDED = "2348012345678"

#: A second handset, for the tests that need one number's request not to silence
#: another's.
OTHER_TYPED = "08098765432"
OTHER_FOLDED = "2348098765432"

REASON = (
    "this installation has no SMS account, so a number cannot be verified: "
    "TERMII_API_KEY is not set"
)


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "identity.db")


@pytest.fixture
def factory(db_path):
    return SqliteUnitOfWorkFactory(db_path)


@pytest.fixture
def request_verification(factory):
    """``RequestPhoneVerification`` with an optional handset and optional reason.

    No actor parameter anywhere, and that absence is the class's own design rather
    than a shortcut in the fixture: there is no "as somebody" in this flow, because
    the person using it may not have an account to be somebody *as*.
    """

    def _build(channel=None, unconfigured_reason=None):
        return RequestPhoneVerification(
            factory, channel=channel, unconfigured_reason=unconfigured_reason
        )

    return _build


def verifications_in(db_path, phone=None) -> int:
    """How many requests the store holds, asked *around* the port rather than through it.

    ``PhoneVerificationRepository`` deliberately has no ``find`` - see its docstring -
    so a test that needs to know whether a row exists *at all*, or what it holds,
    has to open the file. That is the right trade for the reason the sibling file
    gives: the absence of a write is one of the properties under test, and a port
    method added so a test could assert it would be a method with no production
    caller.
    """
    connection = open_sqlite_connection(db_path)
    try:
        if phone is None:
            return connection.execute(
                "SELECT COUNT(*) FROM phone_verifications"
            ).fetchone()[0]
        return connection.execute(
            "SELECT COUNT(*) FROM phone_verifications WHERE phone = ?", (phone,)
        ).fetchone()[0]
    finally:
        connection.close()


def seed_account(db_path, email=None, phone=None):
    """Write an account straight into the store, so a number can be *taken*.

    The signup that would create such an account is the other half of this slice,
    so a row has to be written here - and it is written through the repository, one
    layer below the rule, which is also how reality produced the rows this file is
    about: an account holding a number exists before anybody asks to verify that
    number again.
    """
    factory = SqliteUnitOfWorkFactory(db_path)
    uow = factory.start()
    try:
        user = User(
            user_id=uuid4(),
            email=email,
            phone=phone,
            google_subject=None,
            created_at=NOW,
        )
        uow.users.save(user)
        uow.commit()
    finally:
        uow.rollback()
    return user


class TestThePendingRequest:
    """What asking produces when the installation can text: a row, and a message."""

    def test_it_records_a_request(self, request_verification, db_path, build_sms_channel):
        request_verification(channel=build_sms_channel()).execute(TYPED, NOW)

        assert verifications_in(db_path) == 1

    def test_it_texts_the_number(self, request_verification, build_sms_channel):
        """One message, to the number this request is about.

        One rather than "at least one", because a second send would be a second
        copy of one code - the same credential twice, on a person's bill.
        """
        channel = build_sms_channel()
        request_verification(channel=channel).execute(TYPED, NOW)

        assert len(channel.sent) == 1
        assert channel.sent[0].recipient == FOLDED

    def test_the_recipient_is_the_folded_number_and_not_what_was_typed(
        self, request_verification, build_sms_channel
    ):
        """**The one place the fold is observable from outside.**

        ``RequestPhoneVerification`` sends to ``verification.phone`` rather than to
        the argument, and the two differ for every national-form number - which is
        the form nearly every person in this product's market types. The recipient
        has to be the folded value, because that is what Termii requires and what
        the ``UNIQUE`` slot holds: a message addressed to the typed spelling would
        be a text to a number this system never recorded, and the code in it would
        be matched against a row for a different string.

        The fake refuses a recipient carrying a trunk ``0`` or a ``+``, so this is
        also the test that would fail if ``fold_phone`` regressed - which is
        decision 151-155's lesson applied before the bug rather than after it.
        """
        channel = build_sms_channel()
        request_verification(channel=channel).execute("+234 801 234 5678", NOW)

        assert channel.sent[0].recipient == FOLDED

    def test_the_message_body_does_not_carry_the_number(
        self, request_verification, build_sms_channel
    ):
        """**The absence, and it is the same choice the aggregate makes about ``str``.**

        The number is on the *envelope* - ``recipient``, because that is how the
        message is addressed - and nowhere in the text. ``phoneVerificationMessage``
        gives the argument: a phone number is the smallest enumerable identifier in
        this product, and a text is stored, forwarded and screenshotted in ways a
        mail is not. A body naming the number would put it in a preview notification,
        in a lock screen, and in whatever the handset syncs to.

        Asserted as an absence because that is the whole of the claim, and because a
        test that only checked the recipient would pass on a body that printed the
        number twice.
        """
        channel = build_sms_channel()
        request_verification(channel=channel).execute(TYPED, NOW)

        assert channel.sent[0].recipient == FOLDED
        assert FOLDED not in channel.sent[0].body
        assert TYPED not in channel.sent[0].body

    def test_the_texted_code_is_the_one_the_stored_hash_would_mint(
        self, request_verification, db_path, build_sms_channel
    ):
        """**The other half of "the code exists in exactly one place".**

        The value in the message hashes to the value in the column, so the code a
        person reads off their handset is the one the claim will match - and
        nothing else in the system could have produced it, because ``issue``
        returns the plaintext once and to this method alone.
        """
        channel = build_sms_channel()
        request_verification(channel=channel).execute(TYPED, NOW)
        texted = code_in(channel.sent[0], SIGNUP_CODE_LABEL)

        connection = open_sqlite_connection(db_path)
        try:
            stored = connection.execute(
                "SELECT token_hash FROM phone_verifications"
            ).fetchone()[0]
        finally:
            connection.close()

        assert stored == hash_session_token(texted)

    def test_the_stored_row_carries_a_hash_and_not_the_texted_code(
        self, request_verification, db_path, build_sms_channel
    ):
        """**What makes a leaked database not a set of live signup codes.**

        A person who could read ``phone_verifications`` - a backup, an operator
        with a shell, a bug that dumps a table - holds a hash and not a credential.
        Here the credential is the one that *creates an account*, so the stake is
        at the top of this table's range: a leaked code is not one session, it is a
        new account on a number somebody else holds.
        """
        channel = build_sms_channel()
        request_verification(channel=channel).execute(TYPED, NOW)
        texted = code_in(channel.sent[0], SIGNUP_CODE_LABEL)

        connection = open_sqlite_connection(db_path)
        try:
            row = connection.execute("SELECT * FROM phone_verifications").fetchone()
            dump = " ".join(str(value) for value in tuple(row))
        finally:
            connection.close()

        assert texted not in dump
        assert hash_session_token(texted) in dump

    def test_the_message_says_no_account_exists_yet(
        self, request_verification, build_sms_channel
    ):
        """**A claim about the world, and it has to be true.**

        Nothing has happened: no account exists, no wallet exists, and the only
        thing on disk is a pending row. Somebody who reads this text and concludes
        otherwise reports a signup that has not occurred - and the sentence is the
        whole of the reassurance the message is for, so it is asserted rather than
        left to whoever next rewords it.
        """
        channel = build_sms_channel()
        request_verification(channel=channel).execute(TYPED, NOW)

        body = channel.sent[0].body
        assert "nothing has happened and nothing will" in body
        assert "no account exists until this code comes back" in body

    def test_the_message_prints_the_deadline_the_claim_will_enforce(
        self, request_verification, build_sms_channel
    ):
        """**One clock reading, so the promise and the gate cannot disagree.**

        ``execute`` takes ``now``, ``issue`` counts the window from it, and the
        message renders ``expires_at`` - the same field ``claim_by_token_hash``
        tests against with ``expires_at > ?``. A message composed from a second
        reading of the clock would promise a deadline up to a few microseconds
        different from the one enforced, and the person would be told the code had
        stopped working a moment before it did.
        """
        channel = build_sms_channel()
        verification = request_verification(channel=channel).execute(TYPED, NOW)

        deadline = (NOW + PHONE_VERIFICATION_LIFETIME).isoformat(timespec="minutes")
        assert verification.expires_at == NOW + PHONE_VERIFICATION_LIFETIME
        assert deadline in channel.sent[0].body

    def test_the_returned_request_is_the_one_that_was_stored(
        self, request_verification, db_path, build_sms_channel
    ):
        """There is no outcome wrapper, so this is the assertion that justifies it.

        The return value is the aggregate itself - unlike ``RequestPasswordReset``,
        which wraps a possibly-absent request in an outcome - because every valid
        number produces exactly one, and both ways to fail raise. What a caller
        reads off the returned value (the folded number, the deadline) is the row
        the claim will act on, and this pins that there is no second copy to drift.
        """
        verification = request_verification(channel=build_sms_channel()).execute(
            TYPED, NOW
        )

        connection = open_sqlite_connection(db_path)
        try:
            row = connection.execute(
                "SELECT phone_verification_id, phone, expires_at "
                "FROM phone_verifications"
            ).fetchone()
        finally:
            connection.close()

        assert str(verification.phone_verification_id) == row[0]
        assert verification.phone == row[1] == FOLDED
        assert verification.expires_at.isoformat(timespec="seconds") == row[2]


class TestTheNumberThatIsNotOne:
    """Where this flow differs from the reset flow's treatment of a typo."""

    @pytest.mark.parametrize(
        "typed",
        [
            "not-a-number",
            "",
            "   ",
            "()",
            "+",
            "0",
            "0801",
            "0801234567890123456",
            "08012345678alt",
        ],
        ids=[
            "words",
            "empty",
            "spaces",
            "punctuation",
            "plus only",
            "trunk only",
            "truncated",
            "too long",
            "letters appended",
        ],
    )
    def test_it_is_refused(self, request_verification, build_sms_channel, typed):
        """**Refused rather than treated as a number that names nothing.**

        Every value here is one somebody could type into a signup form, and none of
        them is a number: the folded length, the character set and the emptiness of
        the fold between them rule out all nine. The reset flow deliberately does
        *not* do this for an address - ``not-an-address`` is folded and looked up,
        and answers exactly as an unknown account does - and the difference is what
        the two values are for. An address is a key; a number is dialled.
        """
        with pytest.raises(InvalidUserPhoneError):
            request_verification(channel=build_sms_channel()).execute(typed, NOW)

    def test_it_writes_nothing_and_texts_nothing(
        self, request_verification, db_path, build_sms_channel
    ):
        """The refusal is the aggregate's, so it happens before there is a row.

        Not merely "the row is not committed": nothing reaches the store at all,
        because ``PhoneVerification.issue`` constructs the aggregate - and the
        aggregate refuses - before ``save`` is ever called. The second half matters
        for the same reason it does everywhere else in this file: a malformed number
        that still caused a text would be an unauthenticated way to spend the
        installation's money on a form submission.
        """
        channel = build_sms_channel()

        with pytest.raises(InvalidUserPhoneError):
            request_verification(channel=channel).execute("not-a-number", NOW)

        assert verifications_in(db_path) == 0
        assert channel.attempts == []

    def test_the_rule_is_not_applied_twice(
        self, request_verification, db_path, build_sms_channel
    ):
        """**A departure from ``SignUp``, pinned so that it stays one.**

        ``SignUp`` folds and checks its identifier up front because it needs the
        folded spelling *before* the aggregate exists - the duplicate lookup runs on
        it. Nothing is looked up here, so an explicit check in this file would be a
        second copy of a rule the aggregate already owns, free to drift from it. The
        observable consequence is the *class* of the refusal: it is the aggregate's
        ``InvalidUserPhoneError``, raised from ``__post_init__``, and the way to see
        that from outside is that a number ``checked_phone`` accepts is accepted
        here whatever spelling it arrived in.
        """
        channel = build_sms_channel()

        for spelling in ("08012345678", "+2348012345678", "2348012345678", "0801 234 5678"):
            request_verification(channel=channel).execute(spelling, NOW)

        assert verifications_in(db_path, phone=FOLDED) == 1
        assert len(channel.sent) == 4


class TestANumberThatIsAlreadySomebodys:
    """The lookup that deliberately does not happen."""

    def test_it_is_still_texted(self, request_verification, db_path, build_sms_channel):
        """**The decision the two-step signup rests on, asserted at the store.**

        A number with an account is not refused here, and the text still goes out.
        Refusing would be *free* for whoever is trying to claim the number - they
        will attempt it anyway - and asking the question at all would turn an
        unauthenticated endpoint into an enumeration oracle over a space that is
        small, structured and guessable. That is ``LogIn``'s argument, and it is
        stronger here because there is no password being asked for to slow an
        attacker down.

        The refusal moves one step later, to the confirm, where the caller has
        proved they hold the handset and is owed a sentence about why they cannot
        have the account.
        """
        seed_account(db_path, email=None, phone=FOLDED)
        channel = build_sms_channel()

        request_verification(channel=channel).execute(TYPED, NOW)

        assert len(channel.sent) == 1
        assert verifications_in(db_path) == 1

    def test_the_same_number_still_folds_to_one_row(
        self, request_verification, db_path, build_sms_channel
    ):
        """And asking again on a taken number supersedes, like any other request.

        A branch that skipped the write for a taken number would leave the first
        request's code live while texting a code that means nothing - two codes,
        one of which the store would honour, and the person holding the one they
        were just sent.
        """
        seed_account(db_path, email=None, phone=FOLDED)
        channel = build_sms_channel()

        request_verification(channel=channel).execute(TYPED, NOW)
        request_verification(channel=channel).execute(TYPED, NOW + timedelta(minutes=1))

        assert verifications_in(db_path) == 1
        assert len(channel.sent) == 2


class TestAskingTwice:
    """One pending verification per number, and the second code is the live one."""

    def test_it_leaves_one_row(self, request_verification, db_path, build_sms_channel):
        request_verification(channel=build_sms_channel()).execute(TYPED, NOW)
        request_verification(channel=build_sms_channel()).execute(
            TYPED, NOW + timedelta(minutes=1)
        )

        assert verifications_in(db_path) == 1

    def test_the_second_text_carries_a_different_code(
        self, request_verification, build_sms_channel
    ):
        """**Because a resend that reused the code would be a resend that did nothing.**

        The row is superseded, which kills the first code the instant the second
        commits - so a second text carrying the *same* code would be a text whose
        code had already been invalidated by its own arrival, and the person would
        present it and be told it means nothing.
        """
        channel = build_sms_channel()
        request_verification(channel=channel).execute(TYPED, NOW)
        request_verification(channel=channel).execute(TYPED, NOW + timedelta(minutes=1))

        first = code_in(channel.sent[0], SIGNUP_CODE_LABEL)
        second = code_in(channel.sent[1], SIGNUP_CODE_LABEL)

        assert first != second

    def test_both_texts_are_sent(self, request_verification, build_sms_channel):
        """The supersede happens in the store; nothing is suppressed at this layer.

        A use case that refused to send while a request was outstanding would need
        to ask the store whether one was - and that read would be a place for the
        two halves to disagree, since the only thing that makes a request
        "outstanding" is whether its window has closed, which is a clock. It is also
        the exact read this class exists to avoid.
        """
        channel = build_sms_channel()
        request_verification(channel=channel).execute(TYPED, NOW)
        request_verification(channel=channel).execute(TYPED, NOW + timedelta(minutes=1))

        assert len(channel.sent) == 2

    def test_a_second_numbers_request_still_sends(
        self, request_verification, db_path, build_sms_channel
    ):
        """The key is the number, so one person asking does not silence another.

        A store keyed on anything global - or a supersede written against the wrong
        column, so that a second request from anybody replaced everybody's row -
        would pass every test above in a database with one number in it, and would
        silently stop texting everybody else. This is the test that rules that out.
        """
        channel = build_sms_channel()

        request_verification(channel=channel).execute(TYPED, NOW)
        request_verification(channel=channel).execute(OTHER_TYPED, NOW)

        assert len(channel.sent) == 2
        assert verifications_in(db_path, phone=FOLDED) == 1
        assert verifications_in(db_path, phone=OTHER_FOLDED) == 1


class TestTheOrderOfTheRowAndTheText:
    """The row is on disk before the code is on a wire."""

    def test_the_request_is_committed_before_the_send(
        self, request_verification, db_path
    ):
        """**Asked from inside ``send``, which is the only instant it is observable at.**

        A channel that opened the database *during* the send and found no row would
        prove the opposite ordering, and the failure it would represent is not
        theoretical: a code that left before its row was durable is a code for a
        request that does not exist, so the person presents it exactly as instructed
        and is told it means nothing. The other direction - a row whose text never
        arrived - expires on its own in ten minutes and is superseded on the next
        attempt.
        """

        class CountingChannel(FakeSmsChannel):
            def __init__(self):
                super().__init__()
                self.rows_at_send: list = []

            def send(self, message) -> None:
                self.rows_at_send.append(verifications_in(db_path))
                super().send(message)

        channel = CountingChannel()
        request_verification(channel=channel).execute(TYPED, NOW)

        assert channel.rows_at_send == [1]

    def test_a_failed_send_raises(self, request_verification, build_sms_channel):
        """**This text carries the credential that creates the account.**

        Letting the failure pass quietly would leave the person watching a handset
        for a message that is not coming, with no account and no clue which of the
        two halves broke - which is worse than an error, because it looks like
        progress. So it propagates, matching ``RequestPasswordReset`` and
        ``RequestEmailChange``, and the route above turns it into a 500.
        """
        channel = build_sms_channel(failures=[OSError("connection refused")])

        with pytest.raises(OSError):
            request_verification(channel=channel).execute(TYPED, NOW)

    def test_the_row_survives_a_failed_send(
        self, request_verification, db_path, build_sms_channel
    ):
        """**The survivable half of that ordering, stated rather than implied.**

        What is left behind is a row holding a hash of a code nobody has -
        harmless, because it can never be claimed, and superseded the moment the
        person asks again. A rollback on send failure would leave nothing at all,
        which is the same state from the person's side and would lose the record
        that somebody asked.
        """
        channel = build_sms_channel(failures=[OSError("connection refused")])

        with pytest.raises(OSError):
            request_verification(channel=channel).execute(TYPED, NOW)

        assert verifications_in(db_path) == 1


class TestTheInstallationWithNoSmsAccount:
    """Where the refusal is the only true answer rather than a fallback."""

    def test_it_refuses(self, request_verification):
        with pytest.raises(NoSmsAccountError):
            request_verification().execute(TYPED, NOW)

    def test_the_refusal_names_the_missing_setting(self, request_verification):
        """**One sentence, composed by the builder, rendered by both presentations.**

        The reason is passed in because a use case is not allowed to read the
        environment - ``describe_termii_configuration`` is the only thing that does
        - and it is the *builder* that composes it so that the API's 503 and the
        CLI's exit 1 carry the same words. A reason invented here would be a second
        copy of the configuration vocabulary, free to name a variable that does not
        exist.
        """
        with pytest.raises(NoSmsAccountError) as refused:
            request_verification(unconfigured_reason=REASON).execute(TYPED, NOW)

        assert REASON in str(refused.value)
        assert "TERMII_API_KEY" in str(refused.value)

    def test_the_fallback_sentence_still_says_something_useful(self, request_verification):
        """For the one caller that could forget to pass a reason: a test.

        Degrading into a worse message rather than into a ``TypeError``, so that a
        hand-built use case refuses for the right *reason* even when it cannot name
        the right variable.
        """
        with pytest.raises(NoSmsAccountError) as refused:
            request_verification().execute(TYPED, NOW)

        assert "no SMS account" in str(refused.value)

    def test_it_writes_nothing(self, request_verification, db_path):
        """Refused before a unit is opened, so there is no row to find.

        The refusal depends on nothing about the input, so getting past it teaches a
        caller nothing - and the row that is *not* written is what makes this a clean
        refusal rather than a request nobody can answer.
        """
        with pytest.raises(NoSmsAccountError):
            request_verification().execute(TYPED, NOW)

        assert verifications_in(db_path) == 0

    def test_the_refusal_comes_before_the_number_is_even_checked(
        self, request_verification, db_path
    ):
        """**Ordered first, so the refusal cannot become an oracle either.**

        A malformed number on an install with no SMS account refuses with
        ``NoSmsAccountError`` and not ``InvalidUserPhoneError``, because the channel
        check precedes everything. The reset flow's counterpart asserts this about an
        *unknown address*; here the input that could distinguish two answers is
        rejected by the shape rule, so the ordering is only observable from the other
        side - a value the shape rule would refuse still gets the configuration
        refusal, which is what "it depends on nothing about the input" means.
        """
        with pytest.raises(NoSmsAccountError):
            request_verification(unconfigured_reason=REASON).execute("not-a-number", NOW)

        assert verifications_in(db_path) == 0

    def test_the_refusal_is_keyed_on_the_channel_and_not_on_the_reason(
        self, request_verification, build_sms_channel
    ):
        """**The condition from the side that shows what it is keyed on.**

        The refusal is the absence of a ``channel``, and the reason is only the
        sentence it is refused *with* - which is the arrangement a production caller
        cannot separate, because ``_no_sms_account_reason`` returns a reason exactly
        when it returns no channel. A hand-built use case can separate them, and this
        is what it says when it does: a channel that exists is a capability, so the
        request goes through and one message is sent.

        The alternative reading - refuse whenever a reason was passed - would make
        the two signals a conjunction and would refuse an installation that had just
        been configured, which is the state a stale reason would describe.
        """
        channel = build_sms_channel()

        verification = request_verification(
            channel=channel, unconfigured_reason=REASON
        ).execute(TYPED, NOW)

        assert isinstance(verification, PhoneVerification)
        assert len(channel.sent) == 1
