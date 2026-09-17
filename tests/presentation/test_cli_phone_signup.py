"""The CLI walking a phone signup: ask for a code, then create the account.

Two commands, and **neither reads the session file** - so this file's first claim
is made on the filesystem rather than in a comment: one test asserts the file does
not exist after either command has run, and another deletes it before answering.
The person running these may not *be* anybody in this system yet, because the
account is what answering the code creates.

The other four claims:

- **The code is in the text and nowhere else.** Not on the command line, not in the
  output. So reading it means reading the channel, exactly as the person does, and
  one test asserts the absence of the literal code in what was printed.
- **The number is printed as it was stored, not as it was typed.** ``0801 234 5678``
  and ``+2348012345678`` are one number, the row holds one spelling, and that
  spelling is what the provider was handed - so that is what the terminal shows.
  The block of tests below is the only place a person can see the fold happen.
- **The password is asked for twice.** A mistyped password is accepted, stored, and
  becomes the secret on a brand-new account - a lockout created at the moment of
  creation, on an account that has no address to recover through.
- **A weak password does not spend the code.** At a terminal that reads as: type a
  longer one, and the same code still works.

**The SMS channel is replaced at the builder**, which is the move every
presentation file here makes - and note what is *not* set: the environment. An
injected channel makes ``_sms_channel_for`` return it, so ``TERMII_API_KEY`` and
``TERMII_SENDER_ID`` are absent throughout and the one test about an SMS-less
install needs no arrangement at all.

**Real argon2 is used, and the cost is paid deliberately.** ``confirm-phone``
hashes with whatever the composition root builds, which is the argon2 adapter -
one hash per test that creates an account. Injecting the fake would leave the
command that writes a credential into a brand-new account hashing with something
no deployment has, and the thing worth checking here is that the account it
creates is one the real adapter could have made.
"""

import functools
import os
from datetime import datetime, timedelta

import pytest

from app.composition_root import (
    build_confirm_phone_sign_up,
    build_request_phone_verification,
)
from app.domain.identity.phoneVerification import (
    PHONE_VERIFICATION_LIFETIME,
    PhoneVerification,
)
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
    open_sqlite_connection,
)
from app.presentation import cli
from app.presentation.cli import main
from tests.conftest import (
    SIGNUP_CODE_LABEL,
    code_in,
    session_path_for,
)

#: The number as a person in Nigeria types it, and the one this file starts from.
TYPED = "08012345678"

#: What :data:`TYPED` folds to. The value the provider is handed, the value the
#: account holds, and - because of that - the value the terminal prints.
FOLDED = "2348012345678"

#: The password the confirmed account is given.
SIGNED_UP_PASSWORD = "a-longer-and-different-one"

#: A password ``PlainPassword`` refuses, for the divergence test.
WEAK_PASSWORD = "short"

#: What ``_confirm_phone`` calls the code it asks for, and the key the ``typed``
#: fixture answers it by. Spelled here rather than inline because one test's
#: assertion *is* that the command names it this: the address change asks for a
#: "confirmation code", and a phone signup that asked for one would send a person
#: to check they were in the right command.
PHONE_CODE_PROMPT = "verification code"

#: What ``_prompt_password`` asks for, once or twice.
PASSWORD_PROMPT = "password"

#: The second prompt, when the two answers to it differ.
AGAIN_PROMPT = "password again"


def run(db, session, *argv):
    """Invoke the CLI in-process, with this test's database and session file."""
    return main(["--db", db, "--session", session, *argv])


@pytest.fixture
def db(tmp_path):
    """The database this file's CLI runs against, and the file is not incidental.

    A ``:memory:`` database would be created and discarded per connection, and these
    tests run the CLI *twice* - a request and an answer - so both invocations must
    be looking at one file.
    """
    return str(tmp_path / "cli.db")


@pytest.fixture
def session(db):
    """Where the token would go, by the same rule as ``run``."""
    return session_path_for(db)


@pytest.fixture
def typed(monkeypatch):
    """Answer the CLI's prompts **by name**, so a test says what it types where.

        typed({})                            # this command asks nothing at all
        typed({PASSWORD_PROMPT: "hunter2"})  # answered twice, identically
        typed({PHONE_CODE_PROMPT: code,
               PASSWORD_PROMPT: "a", AGAIN_PROMPT: "b"})   # a mismatch

    The same fake ``test_cli_password_reset.py`` uses, and for its reason: this
    command asks for two *different* things in one invocation, so a queue of values
    would make every test depend on the order the command happens to ask in.
    Answering a prompt the test did not name is an ``AssertionError`` that names it,
    so ``typed({})`` is a real assertion rather than a formality.

    The one default is that ``AGAIN_PROMPT`` takes whatever ``PASSWORD_PROMPT`` was
    given, because the command confirms the password by asking for the same string
    twice - and spelling it out everywhere would bury the mismatch test among the
    tests that are not about a mismatch.
    """

    def _typed(answers: dict | None = None) -> None:
        resolved = dict(answers or {})
        if PASSWORD_PROMPT in resolved and AGAIN_PROMPT not in resolved:
            resolved[AGAIN_PROMPT] = resolved[PASSWORD_PROMPT]

        def fake(prompt: str = "") -> str:
            key = prompt.strip().rstrip(":")
            assert key in resolved, (
                f"the command asked {prompt!r}, which this test did not answer"
            )
            return resolved[key]

        monkeypatch.setattr("getpass.getpass", fake)

    return _typed


@pytest.fixture
def handset(monkeypatch, build_sms_channel):
    """Wire the CLI's request builder to text through a fake, and hand it back.

        handset()          # a working SMS account
        handset(channel)   # a specific one, so a failure can be scripted

    **The builder rather than the adapter**, which is the choice the reset file
    makes and for its reason: what is substituted is the piece the *presentation*
    reaches for, so everything the use case does with the result stays real - and
    the arguments the builder is handed stay in the path where a CLI-specific
    mistake would live.

    **Only ``channel`` is bound, and the CLI's own ``settings=`` must not be.** The
    command calls the builder with ``settings=termii`` - the value it read from an
    environment this suite clears, so ``None`` - and ``functools.partial`` raises a
    ``TypeError`` if the same keyword is bound twice. It does not need to be bound:
    ``_sms_channel_for`` resolves the injected channel *first*, so a partial that
    supplies one means the environment is never read at all, which is the property
    the builder's own docstring claims and the reason this arrangement works rather
    than merely fails to break.

    ``build_confirm_phone_sign_up`` is replaced too, and with **no channel at all**,
    which is not a mirror of the line above but the statement of a fact about this
    flow: the confirm half takes no channel because nothing is sent from it. A
    version of the command that had somehow grown one would fail here.
    """

    def _install(channel=None) -> None:
        # **Built when none is passed, rather than bound as ``None``.** That default
        # is the whole of this command's wiring: ``channel=None`` means "this
        # installation has no SMS account", so a fixture that forwarded its own
        # default would leave every test that called ``handset()`` running against
        # an unconfigured installation while believing it had one - and the
        # failures would land on the assertions about the *number*.
        resolved = build_sms_channel() if channel is None else channel
        monkeypatch.setattr(
            cli,
            "build_request_phone_verification",
            functools.partial(build_request_phone_verification, channel=resolved),
        )
        monkeypatch.setattr(
            cli, "build_confirm_phone_sign_up", build_confirm_phone_sign_up
        )

    return _install


@pytest.fixture
def asked(db, session, handset, build_sms_channel, typed):
    """Ask for a code as the CLI, and hand back the channel and the code.

    Returned as a pair because the two callers want different halves: most tests
    want the code, and the ones about what was *printed* want the message.

    ``typed({})`` is the assertion that this command prompts for nothing at all -
    which is the property that makes it usable by somebody who is not yet an
    account, and a version that asked for a password would be asking for a
    credential that will be chosen two commands later.
    """

    def _ask(phone: str = TYPED) -> tuple:
        channel = build_sms_channel()
        handset(channel)
        typed({})
        assert run(db, session, "signup-phone", phone) == 0
        return channel, code_in(channel.sent[0], SIGNUP_CODE_LABEL)

    return _ask


@pytest.fixture
def seed_expired(db):
    """A verification whose ten minutes are up, written straight into the store.

    **Expiry is the reason.** No test may wait ten minutes for a window to close, so
    an expired code is produced the way time produces one: a request issued eleven
    minutes ago. That is not a workaround for a missing seam - it *is* the state,
    written by the same ``PhoneVerification.issue`` the command calls and read back
    by the same claim, and it is honest about what an expired code is: a perfectly
    valid row that the window has moved past.
    """

    def _seed(phone: str = TYPED) -> str:
        verification, token = PhoneVerification.issue(
            phone=phone, now=datetime.now() - PHONE_VERIFICATION_LIFETIME - timedelta(minutes=1)
        )

        uow = SqliteUnitOfWorkFactory(db).start()
        try:
            uow.phone_verifications.save(verification)
            uow.commit()
        finally:
            uow.rollback()
        return token

    return _seed


def accounts_in(db) -> int:
    """How many accounts the store holds, asked around the repository.

    Read from a connection of its own over the file the CLI is using, rather than
    through a unit of work: what is being asserted is what is on disk, which is a
    fact about the file and not about any object's opinion of it.
    """
    connection = open_sqlite_connection(db)
    try:
        return connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        connection.close()


class TestAskingForACode:
    """``signup-phone``: one argument, one text, and nothing read from disk."""

    def test_it_texts_the_number_and_says_until_when(self, capsys, asked):
        """Neither half is decoration.

        *Where* it went is the folded number - the one the provider was handed and
        the one the account will hold - and the deadline is a moment rather than "ten
        minutes", because a person is holding a handset, not a stopwatch. And it
        names the next command, because nothing has happened yet: a person who closed
        the terminal here would otherwise be left with a number that is not yet an
        account and no way to find out what to do about it.
        """
        channel, _ = asked()

        out = capsys.readouterr().out
        assert f"verification code sent to {FOLDED}" in out
        assert "expires" in out
        assert "confirm-phone" in out
        assert [message.recipient for message in channel.sent] == [FOLDED]

    @pytest.mark.parametrize(
        "typed_phone",
        [
            "08012345678",
            "+2348012345678",
            "2348012345678",
            "0801 234 5678",
            "0801-234-5678",
            "(0801) 234 5678",
        ],
        ids=["national", "international", "no plus", "spaced", "dashed", "bracketed"],
    )
    def test_it_prints_the_stored_spelling_and_not_the_typed_one(
        self, capsys, asked, typed_phone
    ):
        """**The only place a person can see the fold happen, and it is deliberate.**

        Every spelling above is one number, and the row holds one of them. Printing
        the typed string back would be more comfortable and less true: it is not what
        the provider was handed, and somebody who typed ``0801 234 5678`` and sees
        ``2348012345678`` has learned something about their own account rather than
        been confused by it.

        ``_reset_password`` makes the same choice the other way and prints
        ``args.email``, because an address has no fold a user would not recognise.
        """
        asked(typed_phone)

        assert f"verification code sent to {FOLDED}" in capsys.readouterr().out

    def test_it_asks_for_nothing_at_all(self, db, session, handset, typed):
        """No password, no code, no session - the number is the whole of the input.

        ``typed({})`` fails the moment the command prompts for anything, so this is
        an assertion about the command rather than about the fixture.
        """
        handset()

        assert run(db, session, "signup-phone", TYPED) == 0

    def test_it_writes_no_session_file(self, db, session, asked):
        """**A signup has no session to write, and this is where that is checked.**

        ``login`` is the only command that writes the file, and it is reachable only
        with address and password. A version of this command that minted a token
        would be handing out a credential for an account that does not exist yet -
        and it would do it on the machine of somebody who has not proved anything.
        """
        asked()

        assert not os.path.exists(session)

    def test_the_code_is_not_printed(self, capsys, asked):
        """**The absence, in the strongest form the terminal allows.**

        The code is a credential that creates an account. It exists in the text and
        nowhere else, so a command that echoed it would put it in scrollback, in a
        terminal multiplexer's buffer and in anything capturing the session - on a
        machine whose operator never has to be the person holding the handset.
        """
        channel, code = asked()

        out = capsys.readouterr().out
        assert code not in out
        assert code in channel.sent[0].body

    def test_a_malformed_number_is_refused(self, db, session, capsys, handset, typed):
        """Exit 1 with an ``error:`` line rather than a row nobody can answer.

        Refused rather than folded into nothing, for the reason the route documents:
        a number is dialled rather than looked up, so a value that is not one has no
        registered-or-not question hiding behind it.
        """
        handset()
        typed({})

        assert run(db, session, "signup-phone", "not-a-number") == 1

        assert "phone must contain only digits" in capsys.readouterr().err

    def test_with_no_sms_account_it_refuses_and_names_the_variable(
        self, db, session, capsys, typed
    ):
        """**Exit 1, carrying the same sentence the API's 503 carries.**

        No channel is injected and the environment is clear, so the builder composes
        the reason from ``describe_termii_configuration`` and the use case raises it.
        This is the difference from an address change made visible: that flow falls
        back to applying on the password proof alone because it *has* a proof, and
        there is no proof here - the reason to believe somebody holds a handset *is*
        a message arriving on it - so an install that cannot text cannot do this at
        all, and it should say which variable is missing rather than leave somebody
        guessing.
        """
        typed({})

        assert run(db, session, "signup-phone", TYPED) == 1

        err = capsys.readouterr().err
        assert "TERMII_API_KEY is not set" in err
        assert "no SMS account" in err

    def test_nothing_is_written_when_there_is_no_sms_account(
        self, db, session, capsys, typed
    ):
        """Refused before a unit is opened, so there is no row to find."""
        typed({})

        run(db, session, "signup-phone", TYPED)

        connection = open_sqlite_connection(db)
        try:
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM phone_verifications"
                ).fetchone()[0]
                == 0
            )
        finally:
            connection.close()


class TestCreatingTheAccount:
    """``confirm-phone``: one code, one password typed twice, no session."""

    def test_the_account_is_created_and_the_output_says_so(
        self, db, session, capsys, asked, typed
    ):
        """The first line, and the only one that reports the creation itself.

        Everything after it is commentary on something that has already happened.
        """
        _, code = asked()
        typed({PHONE_CODE_PROMPT: code, PASSWORD_PROMPT: SIGNED_UP_PASSWORD})

        assert run(db, session, "confirm-phone") == 0

        out = capsys.readouterr().out
        assert f"registered {FOLDED}" in out
        assert accounts_in(db) == 1

    def test_it_names_the_command_that_can_sign_in_as_this_account(
        self, db, session, capsys, asked, typed
    ):
        """**The line that changed when ``login-phone`` arrived, and the assertion
        that had to change with it.**

        Until that command existed this printed no "next" line at all, because
        ``signup``'s - "run 'login'" - would have sent the operator to a command
        that resolves an address and this account has none. The gap is closed, so
        the line is back in its own form, and the address-only consequence stays
        stated because it is still true: a deposit from this account is refused
        until an address is set.

        **The absence assertion below is a *second* string rather than the
        substring it used to be, and the difference is the point.** This test
        previously read ``assert "next: run 'login'" not in out``, which looks like
        it pins "no address-based login line" and does not: the new line is ``next:
        run 'login-phone' to start a session``, and the old literal's trailing
        quote means it is not a substring of that. The assertion would have gone on
        passing - in a test whose name promised it would fail - while
        ``_confirm_phone`` printed a command that refuses. Both strings are now
        written out in full, so the pair means exactly what it says: the phone line
        is present, and the address line is not.
        """
        _, code = asked()
        typed({PHONE_CODE_PROMPT: code, PASSWORD_PROMPT: SIGNED_UP_PASSWORD})

        run(db, session, "confirm-phone")

        out = capsys.readouterr().out
        assert "no address is on this account yet" in out
        assert "deposits are refused" in out
        assert "next: run 'login-phone' to start a session" in out
        assert "next: run 'login' to start a session" not in out

    def test_it_writes_no_session_file(self, db, session, asked, typed):
        """The account exists and nobody is signed in as it - which is the point.

        Registering and proving are separate acts; a command that also started a
        session would be handing out a credential the caller has not used, and the
        person at the terminal has proved the *number*, not that they are its owner
        in any sense a session should attest.
        """
        _, code = asked()
        typed({PHONE_CODE_PROMPT: code, PASSWORD_PROMPT: SIGNED_UP_PASSWORD})

        run(db, session, "confirm-phone")

        assert not os.path.exists(session)

    def test_the_code_cannot_be_used_twice(self, db, session, capsys, asked, typed):
        """Exit 1, and the sentence means "you already did this" rather than "no".

        The row survives being spent, which is what makes the two states tellable
        apart at all - so a person who has just created an account and answers the
        same code again is told that rather than told their code means nothing.
        """
        _, code = asked()
        typed({PHONE_CODE_PROMPT: code, PASSWORD_PROMPT: SIGNED_UP_PASSWORD})
        assert run(db, session, "confirm-phone") == 0

        assert run(db, session, "confirm-phone") == 1

        assert "already been answered" in capsys.readouterr().err
        assert accounts_in(db) == 1

    def test_a_code_that_means_nothing_is_refused(self, db, session, capsys, handset, typed):
        handset()
        typed(
            {
                PHONE_CODE_PROMPT: "a-code-nobody-ever-minted",
                PASSWORD_PROMPT: SIGNED_UP_PASSWORD,
            }
        )

        assert run(db, session, "confirm-phone") == 1

        assert accounts_in(db) == 0

    def test_an_expired_code_is_refused(self, db, session, capsys, handset, typed, seed_expired):
        """The window is ten minutes, and the command says so when it has passed.

        The code was texted, the person read it, and they typed it a minute too late
        - which is a state a terminal should describe rather than one it should
        report as a code that means nothing, since the remedy differs: ask again.
        """
        handset()
        typed({PHONE_CODE_PROMPT: seed_expired(), PASSWORD_PROMPT: SIGNED_UP_PASSWORD})

        assert run(db, session, "confirm-phone") == 1

        err = capsys.readouterr().err
        assert "expired" in err
        assert "can no longer be answered" in err

    def test_a_weak_password_does_not_spend_the_code(
        self, db, session, capsys, asked, typed
    ):
        """**The claim that makes the refusal a person can act on.**

        Every other refusal in this flow is after the claim - the code is spent by
        the attempt - and this one is before it, because ``PlainPassword`` is
        constructed before the unit is opened. So a person who types six characters
        is told to type a longer one, and the same code still works: no second text,
        no waiting for a message on a handset they are already holding.

        Asserted as the pair, because only the second half makes the first half mean
        anything - a command that refused for the right reason and then refused the
        good password too would pass a test that only checked the refusal.
        """
        _, code = asked()
        typed({PHONE_CODE_PROMPT: code, PASSWORD_PROMPT: WEAK_PASSWORD})

        assert run(db, session, "confirm-phone") == 1
        assert "at least" in capsys.readouterr().err

        typed({PHONE_CODE_PROMPT: code, PASSWORD_PROMPT: SIGNED_UP_PASSWORD})

        assert run(db, session, "confirm-phone") == 0
        assert accounts_in(db) == 1

    def test_the_two_answers_to_the_password_must_agree(
        self, db, session, capsys, asked, typed
    ):
        """A mistyped password is accepted and stored, and this is the guard.

        The stake is the mirror image of the reset flow's: a wrong password here
        becomes the secret on a brand-new account, which is a lockout created at the
        moment of creation - on an account with no address to recover through and
        whose way back in is a texted code rather than a mailbox. It is the worse of
        the two versions of the same mistake, and the reason this asks twice.
        """
        _, code = asked()
        typed(
            {
                PHONE_CODE_PROMPT: code,
                PASSWORD_PROMPT: "a-longer-and-different-one",
                AGAIN_PROMPT: "a-longer-and-different-too",
            }
        )

        assert run(db, session, "confirm-phone") == 1

        assert "did not match" in capsys.readouterr().err
        assert accounts_in(db) == 0


@pytest.fixture
def confirmed(db, session, asked, typed):
    """Create an account the way the CLI creates one, and return the number typed.

    The two commands above, run for real - a request, a code read out of the
    channel, a password typed twice - rather than a row written straight into the
    store. That is the point of putting these tests *here* rather than in the
    identity file: what they exercise is the account this flow produces, and a
    seeded row would only resemble it.

    The password is a dial so that a login can be attempted with the wrong one.
    """

    def _confirm(phone: str = TYPED, password: str = SIGNED_UP_PASSWORD) -> str:
        _, code = asked(phone)
        typed({PHONE_CODE_PROMPT: code, PASSWORD_PROMPT: password})
        assert run(db, session, "confirm-phone") == 0
        return phone

    return _confirm


class TestSigningInWithTheNumber:
    """``login-phone``: the command that closed the gap ``confirm-phone`` warned about.

    **No SMS channel is wired anywhere in this class, and that is an assertion
    rather than an omission.** The ``handset`` fixture is what the two commands
    above need in order to text somebody; a sign-in sends nothing, so no test here
    requests it - and a version of ``login-phone`` that reached for a channel
    would fail on the unconfigured installation this suite clears the environment
    to create, rather than quietly opening a socket.

    **The session file is the subject of half of these.** Until this command
    existed, an account created from a texted code could not obtain a session at
    all, which made it an account that could hold a wallet it could not reach -
    ``_confirm_phone`` said so in its own output. So "a token lands in the file"
    and "``whoami`` reports the number" are the two claims that together mean the
    gap is closed, and the second is a real round trip: ``whoami`` sends the token
    to the server and is given back the account it names.
    """

    def test_it_writes_the_session_file_and_reports_the_number(
        self, db, session, capsys, confirmed, typed
    ):
        confirmed()
        capsys.readouterr()
        typed({PASSWORD_PROMPT: SIGNED_UP_PASSWORD})

        assert run(db, session, "login-phone", TYPED) == 0

        assert os.path.exists(session)
        out = capsys.readouterr().out
        assert FOLDED in out
        assert "expires" in out

    @pytest.mark.parametrize(
        "spelling", [TYPED, FOLDED, "+2348012345678", "0801 234 5678"]
    )
    def test_any_spelling_of_the_number_signs_in(
        self, db, session, confirmed, typed, spelling
    ):
        """The fold, at the terminal, which is where a person meets it.

        The account was created by typing one of these. The others are the same
        number written differently, and their working is the difference between a
        ``UNIQUE`` column that identifies a person and one that identifies a
        keystroke.
        """
        confirmed()
        typed({PASSWORD_PROMPT: SIGNED_UP_PASSWORD})

        assert run(db, session, "login-phone", spelling) == 0

    def test_a_wrong_password_is_refused_and_writes_nothing(
        self, db, session, capsys, confirmed, typed
    ):
        confirmed()
        typed({PASSWORD_PROMPT: "not-the-one-that-was-set"})

        assert run(db, session, "login-phone", TYPED) == 1

        assert "did not match an account" in capsys.readouterr().err
        assert not os.path.exists(session)

    def test_an_unknown_number_is_refused_with_the_same_words(
        self, db, session, capsys, confirmed, typed
    ):
        """Decision 55 at the terminal, and the two refusals compared rather than
        described.

        A number is a small, enumerable space, so a message that separated "no such
        account" from "wrong password" would let anybody at a shell walk a range of
        numbers and learn which ones hold accounts. Asserted by running both and
        comparing what came out, because two sentences that agree in intent while
        differing in a word are the leak.
        """
        confirmed()
        typed({PASSWORD_PROMPT: "not-the-one-that-was-set"})
        run(db, session, "login-phone", TYPED)
        wrong = capsys.readouterr().err

        typed({PASSWORD_PROMPT: SIGNED_UP_PASSWORD})
        assert run(db, session, "login-phone", "08099999999") == 1
        unknown = capsys.readouterr().err

        assert unknown == wrong
        assert "08099999999" not in unknown

    def test_an_address_typed_into_this_command_is_refused_as_an_unknown_number(
        self, db, session, capsys, confirmed, typed
    ):
        """The cross-identifier case, which is the mistake the two commands invite.

        There is no moment at which this command could answer "that is an address,
        try ``login``": the lookup is by number and an address names no number, so
        the honest answer and the safe answer are the same one. It is asserted
        rather than reasoned about because the tempting improvement is a message
        that helps the person who typed the wrong command - and it would help
        anybody else by reporting which accounts hold numbers.
        """
        confirmed()
        typed({PASSWORD_PROMPT: SIGNED_UP_PASSWORD})

        assert run(db, session, "login-phone", "ada@example.com") == 1

        assert "did not match an account" in capsys.readouterr().err
        assert not os.path.exists(session)

    def test_whoami_reports_the_number_and_no_address(
        self, db, session, capsys, confirmed, typed
    ):
        """**The round trip, and the rendering this step had to fix.**

        ``whoami`` resolves an actor from the stored token, so this is the proof
        that the token written above is one the server accepts - not merely a
        well-shaped string in a file. What it prints is the other half: an account
        with no address used to render as ``email: None``, which reads as a bug in
        the login rather than as the fact it is, so each identifier is printed only
        when it is held. Both absences are asserted, because the failure mode is a
        line that *is* printed and prints nothing.
        """
        confirmed()
        typed({PASSWORD_PROMPT: SIGNED_UP_PASSWORD})
        run(db, session, "login-phone", TYPED)
        capsys.readouterr()

        assert run(db, session, "whoami") == 0

        out = capsys.readouterr().out
        assert f"phone: {FOLDED}" in out
        assert "email:" not in out
        assert "None" not in out
