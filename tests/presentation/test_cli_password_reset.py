"""The CLI walking a forgotten password: ask for a code, then set a new password.

Two commands, and **neither reads the session file** - so this file's first claim is
made on the filesystem rather than in a comment: one test deletes the session before
asking, and everything still works. A person who has forgotten their password is a
person who may be sitting at a machine they have never logged in on.

The other three claims:

- **``reset-password`` tells the truth about whether an account was found**, and
  this is the one place the two presentations deliberately disagree. A terminal with
  write access to the database is not a public surface - its operator could
  ``SELECT`` the answer - so withholding it would protect nobody and leave a person
  unable to tell a typo from a delivery failure.
- **The new password is asked for twice and the code once.** A mistyped code is
  refused by the lookup; a mistyped *password* is accepted, stored, and silently
  becomes the one the person has, which recreates the lockout this command exists to
  end - and does it while reporting success.
- **A weak password does not spend the code.** That is the flow's one divergence
  from the address change, and at a terminal it reads as: type a longer one, and the
  same code still works.

**The hasher is not replaced here, and that is the opposite of what the sibling
files do.** ``test_cli_email_change.py`` and the API's tests inject
``FakePasswordHasher``, because the account ``signed_in`` writes holds a hash only
the fake can verify, and a CLI that checked with argon2 would look like a bug. There
is no such check to make here: ``reset-password`` proves nothing and
``confirm-password-reset`` *replaces* the credential, so this flow never verifies an
existing password. What it does do is assert that a password **logs in**, and
``login`` verifies with the real argon2 adapter - so the account below is registered
through the CLI's own ``signup`` and the reset stores a real argon2 hash. Injecting
the fake would make the headline assertion impossible to write, and it is the
headline for a reason: "the new password works" is the entire point of the flow.

The cost is real and worth naming: a few argon2 operations per test where the fake
would make them free. It is paid because the alternative is a suite that proves a
password logs in by checking it against a hasher no deployment uses.

**The mail is replaced at the builders**, which is the move every presentation file
makes here - and note what is *not* set: the environment. An injected channel makes
``_channel_for`` return it, so the four ``SMTP_*`` variables are absent throughout
and the one test about a mail-less install needs no arrangement at all.
"""

import functools
import os

import pytest

from app.composition_root import (
    build_confirm_password_reset,
    build_request_password_reset,
)
from app.presentation import cli
from app.presentation.cli import main
from tests.conftest import (
    RESET_CODE_LABEL,
    TEST_USER_EMAIL,
    TEST_USER_PASSWORD,
    code_in,
    session_path_for,
)

#: The account whose password is forgotten. The suite's standard address, because
#: this flow has no entry rule of its own - it only ever looks an address up.
ADDRESS = TEST_USER_EMAIL

#: The password the reset sets. Deliberately not the shared constant: the claim is
#: that the *old* one stopped working, and reusing it could not tell an update from
#: a no-op.
NEW_PASSWORD = "a-longer-and-different-one"

#: A password ``PlainPassword`` refuses, for the divergence test.
WEAK_PASSWORD = "short"

#: What ``_confirm_password_reset`` calls the code it asks for, and the key the
#: ``typed`` fixture answers it by. Spelled here rather than inline at each call site
#: because one test's assertion *is* that the command names it this: a command that
#: said "confirmation code" - the address change's label - would be asking a person
#: who asked for a reset to wonder whether they were in the right command.
RESET_CODE_PROMPT = "reset code"

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
    tests run the CLI *three or four* times - a registration, a sign-in, a request and
    an answer - so every invocation must be looking at one file.
    """
    return str(tmp_path / "cli.db")


@pytest.fixture
def session(db):
    """Where the token goes for this database, by the same rule as ``run``."""
    return session_path_for(db)


@pytest.fixture
def typed(monkeypatch):
    """Answer the CLI's prompts **by name**, so a test says what it types where.

        typed({})                            # this command asks nothing at all
        typed({PASSWORD_PROMPT: "hunter2"})  # answered twice, identically
        typed({RESET_CODE_PROMPT: code,
               PASSWORD_PROMPT: "a", AGAIN_PROMPT: "b"})   # a mismatch

    **Announced by prompt rather than by order**, which is the one thing this fake
    does that ``conftest.typed_password`` does not, and the reason is that this
    command asks for two *different* things in one invocation. A queue of values would
    make every test here depend on the order the command happens to ask in, and a test
    that got that order wrong would quietly assert something about the wrong value
    rather than failing.

    Answering a prompt the test did not name is an ``AssertionError`` that names it -
    so ``typed({})`` is a real assertion and not a formality. It says ``reset-password``
    asks for nothing, and a version of it that prompted for a password would fail on
    the first test to run it.

    The one default is that ``AGAIN_PROMPT`` takes whatever ``PASSWORD_PROMPT`` was
    given, because the commands that confirm a password are asking for the same string
    twice and spelling it out everywhere would bury the tests that are about a
    *mismatch* among the ones that are not.
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
def builders(monkeypatch):
    """Wire the CLI's two reset builders to deliver through a channel.

        builders()          # no channel: with no environment either, the refusal
        builders(channel)   # deliver through ``channel``

    **The builders rather than the adapter**, which is the choice
    ``test_cli_email_change.py`` already made and for its reason: what is substituted
    is the piece the *presentation* reaches for, so everything the use case does with
    the result stays real, and the arguments the builder is handed stay in the path
    where a CLI-specific mistake would live. One of them matters here in particular -
    ``unconfigured_reason``, which the builder composes from the environment and which
    is where the whole no-mail refusal is decided.

    Calling it twice in one test is the way a mail account *changes between two
    commands*: a request that went through one channel and an answer through another
    is how a bounced notice is set up, and how an installation that lost its mail
    account mid-flow is.

    Note what is *not* passed: a ``password_hasher``. The reason is the module
    docstring's, and it points the other way from every sibling file.
    """

    def _install(channel=None) -> None:
        monkeypatch.setattr(
            cli,
            "build_request_password_reset",
            functools.partial(build_request_password_reset, channel=channel),
        )
        monkeypatch.setattr(
            cli,
            "build_confirm_password_reset",
            functools.partial(build_confirm_password_reset, channel=channel),
        )

    return _install


@pytest.fixture
def registered(db, session, typed):
    """An account that exists, with a live session, created the way a person does it.

    Through the CLI's own ``signup`` and ``login`` rather than ``conftest.signed_in``,
    which is what every other CLI module uses - and the difference is the hasher.
    ``signed_in`` writes with ``FakePasswordHasher`` to keep argon2 out of a hundred
    tests that never check a password; this file checks one, at the login prompt, with
    the real adapter. An account created through the fake would fail that check before
    the reset as well as after it, so the test would prove nothing about the reset.

    The session it leaves behind is the one the reset ends, which is where the "1
    session signed out" line comes from.
    """
    typed({PASSWORD_PROMPT: TEST_USER_PASSWORD})
    assert run(db, session, "signup", ADDRESS) == 0
    assert run(db, session, "login", ADDRESS) == 0


def code_mailed_to(channel) -> str:
    """The reset code out of the one message a request send produced."""
    return code_in(channel.sent[0], RESET_CODE_LABEL)


@pytest.fixture
def asked(db, session, registered, builders, build_channel, typed):
    """Ask for a reset code as the CLI, and hand back the mailbox and the code.

    Returned as a pair because the two callers want different halves: most tests want
    the code, and the ones about a *delivery* want the channel.

    ``typed({})`` is the assertion that this command prompts for nothing at all. It is
    worth stating, because "reads no session and asks no password" is the property that
    makes the command usable by the person it is for - and a version that asked for
    anything would be asking a locked-out person for a credential they have just told
    the system they do not have.
    """

    def _ask(address: str = ADDRESS) -> tuple:
        channel = build_channel()
        builders(channel)
        typed({})
        assert run(db, session, "reset-password", address) == 0
        return channel, code_mailed_to(channel)

    return _ask


@pytest.fixture
def answered(db, session, asked, typed):
    """Ask for a code and answer it with a new password: two CLI invocations.

    Returns ``(channel, exit_code)``, so a test asserts on the outcome as well as on
    what was printed. The code can be overridden, which is how the pasted-code test
    presents a *different* string that still has to arrive as the right one.

    **The session file is removed before the answer**, so every test that goes through
    here runs ``confirm-password-reset`` with no session on disk. That is not a
    convenience: it is the shape of the flow - asked at a desk, answered from a phone -
    and it is therefore the default rather than a case three tests set up by hand.
    """

    def _answered(password: str = NEW_PASSWORD, code: str | None = None) -> tuple:
        channel, mailed = asked()
        if os.path.exists(session):
            os.remove(session)
        typed({RESET_CODE_PROMPT: mailed if code is None else code,
               PASSWORD_PROMPT: password})
        return channel, run(db, session, "confirm-password-reset")

    return _answered


class TestAskingForAResetCode:
    """``reset-password``: an address, and then one of two honest answers."""

    def test_a_code_is_sent_and_the_output_says_where_and_until_when(
        self, capsys, asked
    ):
        """Neither half is decoration.

        *Where* the code went is what a person checks against their own address, and
        the deadline is a moment rather than "fifteen minutes" - they are holding a
        mailbox, not a stopwatch. And it names the next command, because nothing has
        happened yet and a person who closed the terminal here would otherwise be left
        with an account that still cannot be opened.
        """
        channel, _ = asked()

        out = capsys.readouterr().out
        assert f"reset code sent to {ADDRESS}" in out
        assert "expires" in out
        assert "confirm-password-reset" in out
        assert [message.recipient for message in channel.sent] == [ADDRESS]

    def test_it_works_with_no_session_file_at_all(
        self, db, session, registered, builders, build_channel, typed
    ):
        """**The claim that makes this command usable by the person it is for.**

        The session file is *deleted* rather than merely absent, which is the state of
        somebody who has just been signed out everywhere and of a machine that has
        never logged in. The account exists because ``registered`` created it before
        the file was removed.

        Asserted on the filesystem rather than in a comment, because it is the whole
        difference between this command and ``change-email``, which needs a live
        session *and* the password: a person who cannot authenticate is precisely who
        this is for. ``confirm-password-reset`` is the other half, and ``answered``
        removes the session before every answer it drives.
        """
        os.remove(session)
        channel = build_channel()
        builders(channel)
        typed({})

        assert run(db, session, "reset-password", ADDRESS) == 0

        assert [message.recipient for message in channel.sent] == [ADDRESS]

    def test_the_password_has_not_changed_yet(self, db, session, capsys, asked, typed):
        """A request is not a reset, and at a terminal the only way to tell is to log in.

        The old password still works after the request - which is both the truth the
        mail states in words and the property that stops a stranger who can type an
        address from taking the account. Logging in through the CLI is the strongest
        available form of the assertion, since it is the thing the person will do.
        """
        asked()
        typed({PASSWORD_PROMPT: TEST_USER_PASSWORD})

        assert run(db, session, "login", ADDRESS) == 0

    def test_an_address_that_names_no_account_says_so(
        self, db, session, capsys, registered, builders, build_channel, typed
    ):
        """**Where this presentation parts company with the API, on purpose.**

        The API answers byte-identically for a known and an unknown address, because a
        stranger with a list of addresses is a different caller entirely. A terminal
        with write access to this database is not a public surface - its operator could
        read the ``users`` table - so withholding the answer would protect nobody and
        leave somebody unable to tell a typo from a delivery failure.

        Exit 0, because nothing went wrong: this is the answer to a typo, and a typo is
        how a good share of legitimate requests arrive. Nothing is attempted on the
        channel, which is checked because "nothing was sent" is the second half of the
        sentence and the load-bearing half.
        """
        channel = build_channel()
        builders(channel)
        typed({})
        capsys.readouterr()

        assert run(db, session, "reset-password", "nobody@unknown.invalid") == 0

        out = capsys.readouterr().out
        assert "no account reads mail at nobody@unknown.invalid" in out
        assert "nothing was sent" in out
        assert channel.attempts == []

    def test_with_no_mail_configured_it_refuses_and_names_the_variable(
        self, db, session, capsys, registered, builders, typed
    ):
        """**Exit 1, carrying the same sentence the API's 503 carries.**

        No channel and no environment, so the builder composes the reason from
        ``describe_configuration`` and the use case raises it. This is the third ruling
        behind the feature made visible: an address change falls back to applying on
        the password proof alone because it *has* a proof, and a forgotten password has
        none - so an install with no SMTP cannot do this at all, and it should say
        which variable is missing rather than leave somebody guessing.
        """
        builders()
        typed({})

        assert run(db, session, "reset-password", ADDRESS) == 1

        err = capsys.readouterr().err
        assert "SMTP_HOST is not set" in err
        assert "no mail account" in err

    def test_a_failed_send_is_a_failure_and_not_a_reassurance(
        self, db, session, capsys, registered, builders, build_channel, typed
    ):
        """**It raises, because the person is locked out and there is nothing else to try.**

        ``build_channel(failures=[...])`` makes the one send throw, and nothing
        catches it: the mail carries the credential, so a request whose code did not
        arrive is one nobody can answer, and printing "reset code sent" anyway would
        leave the person waiting for a message that was never accepted. This is the
        failure mode the branch exists to avoid, and it is the *reason* the send is
        outside the transaction - the row is already durable and harmless, holding a
        hash of a token nobody has, superseded on the next attempt.

        **Asserted as a raise rather than as an exit code, because that is what
        actually happens.** ``main`` catches ``MoneyError`` and ``CliError``, and an
        ``OSError`` is neither - so this travels out of ``main`` and the *process*
        exits 1 with a traceback rather than with an ``error:`` line. The API reaches
        500 for the same exception through ``unexpected_error_handler``; the CLI's
        equivalent boundary has no such fallthrough, which is a difference worth
        knowing about rather than one this test can paper over.
        """
        builders(build_channel(failures=[OSError("connection refused")]))
        typed({})

        with pytest.raises(OSError, match="connection refused"):
            run(db, session, "reset-password", ADDRESS)

        assert "reset code sent" not in capsys.readouterr().out


class TestSettingTheNewPassword:
    """``confirm-password-reset``: one code, one password typed twice, no session."""

    def test_the_password_is_changed_and_the_output_says_so(self, capsys, answered):
        """The first line, and the only one that reports the change itself.

        Everything after it is commentary on something that has already happened -
        which is why the session count and the notice footnote follow rather than
        precede.
        """
        answered()

        captured = capsys.readouterr()
        assert f"password changed for {ADDRESS}" in captured.out
        assert "error:" not in captured.err

    def test_it_needs_no_session_and_the_new_password_logs_in(
        self, db, session, capsys, answered, typed
    ):
        """**The headline claim, from a terminal that was signed in as nobody.**

        ``answered`` removed the session file before the answer - see its docstring -
        and the code was accepted anyway. The *new* password is then proved at the login
        prompt, with the real argon2 adapter on both sides, which is the whole round
        trip a locked-out person makes in the order they make it. The old one is checked
        in the test below, and the pair is what makes either mean anything.
        """
        _, exit_code = answered()

        assert exit_code == 0
        typed({PASSWORD_PROMPT: NEW_PASSWORD})
        assert run(db, session, "login", ADDRESS) == 0

    def test_the_old_password_no_longer_logs_in(
        self, db, session, capsys, answered, typed
    ):
        """The counterpart, so the pair cannot both pass for the wrong reason.

        A login that succeeded with either string would pass the test above while the
        reset had done nothing. This one is only meaningful because the account was
        registered through the CLI: with ``signed_in``'s fake hash it would fail before
        the reset as well, and would report a real bug and no bug identically.
        """
        answered()
        typed({PASSWORD_PROMPT: TEST_USER_PASSWORD})

        assert run(db, session, "login", ADDRESS) == 1

        assert "did not match an account" in capsys.readouterr().err

    def test_the_sessions_that_were_ended_are_reported(self, capsys, answered):
        """**The fact that makes the revocation visible rather than a surprise.**

        ``registered`` leaves one live session behind and the reset ends it - so a
        person who was signed in on a phone will find it signed out, and a command that
        did that silently would leave them reading a closed tab as a bug.
        """
        answered()

        assert "1 session signed out" in capsys.readouterr().out

    def test_more_than_one_session_is_counted_in_the_plural(
        self, db, session, capsys, asked, typed
    ):
        """The other branch of the same line, which a single sign-in never reaches.

        ``log_in`` mints a new row each time rather than replacing one, so a second
        ``login`` is what a second device looks like - and the count that comes back is
        the number a person can check against the devices they actually own. The plural
        is asserted rather than the count alone, because a CLI that printed "2 session
        signed out" would be the same number and the wrong sentence.
        """
        typed({PASSWORD_PROMPT: TEST_USER_PASSWORD})
        assert run(db, session, "login", ADDRESS) == 0  # a second device
        _, code = asked()
        typed({RESET_CODE_PROMPT: code, PASSWORD_PROMPT: NEW_PASSWORD})

        assert run(db, session, "confirm-password-reset") == 0

        assert "2 sessions signed out" in capsys.readouterr().out

    def test_the_notice_that_was_sent_is_reported(self, capsys, answered):
        """The footnote, in its first state: the account was told.

        Printed rather than assumed, because it is the whole warning a person gets that
        somebody else set their password - and a command that said nothing about it
        would leave the operator unable to tell a delivered warning from a bounced one.
        """
        answered()

        assert f"{ADDRESS} was told about the change" in capsys.readouterr().out

    def test_a_bounced_notice_is_a_footnote_and_not_a_failure(
        self, db, session, capsys, asked, builders, build_channel, typed
    ):
        """**Exit 0, and the second of the three notice states.**

        The password has already changed and nothing can undo it, so exiting non-zero
        would report a reset that happened as one that did not - and the person would
        run the command again with a code that is already spent. The failure is still
        reported, because it is the only warning anybody gets.

        The bounce is arranged by handing the *second* command a different channel that
        fails its first send, which is the public way to say "the mail account broke
        between the request and the answer" - and is the same move
        ``test_cli_email_change.py`` makes for a notice it cannot deliver.
        """
        _, code = asked()
        bounced = build_channel(failures=[OSError("mailbox full")])
        builders(bounced)
        typed({RESET_CODE_PROMPT: code, PASSWORD_PROMPT: NEW_PASSWORD})

        assert run(db, session, "confirm-password-reset") == 0

        out = capsys.readouterr().out
        assert f"password changed for {ADDRESS}" in out
        assert "could not be told" in out
        assert "mailbox full" in out
        assert bounced.attempts and not bounced.sent

    def test_a_reset_answered_after_the_mail_account_vanished_still_changes_the_password(
        self, db, session, capsys, asked, builders, typed
    ):
        """**The third notice state, and the only one nobody would think to arrange.**

        A confirm is reachable only through a request, and a request refuses without a
        mail account - so the only way to be here with no channel is for the
        installation to have been reconfigured in the fifteen-minute window. The reset
        goes through regardless, and that is deliberate: refusing at this point would
        leave a spent code and an unchangeable password, which is the worst state this
        feature can produce. Nobody is told, and the output says so rather than
        printing nothing, because silence here would read as a delivery that happened.
        """
        _, code = asked()
        builders()  # the installation's mail account is gone
        typed({RESET_CODE_PROMPT: code, PASSWORD_PROMPT: NEW_PASSWORD})

        assert run(db, session, "confirm-password-reset") == 0

        out = capsys.readouterr().out
        assert f"password changed for {ADDRESS}" in out
        assert "no email configured" in out
        assert "was not told" in out

    def test_a_weak_password_is_refused_without_spending_the_code(
        self, db, session, capsys, asked, typed
    ):
        """**The divergence from the address change, at a terminal.**

        The refusal comes *before* the claim, so the same code answers successfully
        with a longer password - which at a terminal reads as "type a longer one" rather
        than "go and find another mail". Spending a code on a typo of the password would
        be the flow being strict about the wrong thing: nothing about this flow can
        change in the window, so the only thing a retry has to satisfy is the policy.

        The two invocations share one code, and that is the assertion.
        """
        _, code = asked()
        typed({RESET_CODE_PROMPT: code, PASSWORD_PROMPT: WEAK_PASSWORD})

        assert run(db, session, "confirm-password-reset") == 1

        assert "at least" in capsys.readouterr().err

        typed({RESET_CODE_PROMPT: code, PASSWORD_PROMPT: NEW_PASSWORD})
        assert run(db, session, "confirm-password-reset") == 0

        assert f"password changed for {ADDRESS}" in capsys.readouterr().out

    def test_a_mismatched_confirmation_is_refused(self, db, session, capsys, asked, typed):
        """**The refusal this command has and the API does not, and why it exists.**

        Nothing downstream can catch a mismatch: the password is accepted, stored and
        silently becomes the one the person has - which recreates exactly the lockout the
        command was run to end, while reporting success. So it is caught at the prompt,
        as a fact about the prompt rather than about passwords.

        The code is not spent either, since the refusal happens before the use case is
        reached at all - the same property the weak-password test asserts by reusing its
        code, and here by consequence rather than by invitation.
        """
        _, code = asked()
        typed({
            RESET_CODE_PROMPT: code,
            PASSWORD_PROMPT: NEW_PASSWORD,
            AGAIN_PROMPT: NEW_PASSWORD + "-typo",
        })

        assert run(db, session, "confirm-password-reset") == 1

        assert "the two passwords did not match" in capsys.readouterr().err

    def test_a_pasted_code_answers_the_request(self, db, session, capsys, asked, typed):
        """**What ``_prompt_code`` exists for, and the fix decision 180 recorded.**

        Selecting a code in a mail client's terminal, or pasting it with a newline,
        arrives with whitespace that was never part of the 256-bit value - and an
        unstripped one matches no row, which presents as "that code means nothing" for a
        code that is correct in every character the person can see. Stripping is safe
        because the alphabet a token is drawn from contains no whitespace at all, so a
        stripped code is either the code or a string that was never going to match.

        The value comes out of the mailbox and is *then* wrapped, so the claim is about
        this code rather than about some other string that happens to survive a strip.
        """
        _, code = asked()
        typed({RESET_CODE_PROMPT: f"  {code}\n", PASSWORD_PROMPT: NEW_PASSWORD})

        assert run(db, session, "confirm-password-reset") == 0

        assert f"password changed for {ADDRESS}" in capsys.readouterr().out

    def test_a_password_with_a_trailing_space_keeps_it(
        self, db, session, capsys, asked, typed
    ):
        """The other half of that rule, and the sharper of the two.

        A password is a secret its owner chose, and a trailing space is a character they
        chose. Trimming it would silently store something they did not type, and they
        would have no way to know which of the two they hold - so the value that logs in
        is the one with the space still on it. ``_prompt_code`` strips and
        ``_prompt_password`` deliberately does not, and this is that difference
        asserted rather than described.
        """
        spaced = NEW_PASSWORD + "  "
        _, code = asked()
        typed({RESET_CODE_PROMPT: code, PASSWORD_PROMPT: spaced})

        assert run(db, session, "confirm-password-reset") == 0
        capsys.readouterr()
        typed({PASSWORD_PROMPT: spaced})

        assert run(db, session, "login", ADDRESS) == 0
