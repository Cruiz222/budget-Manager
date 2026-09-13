"""The CLI walking an address change: ask with a password, answer with a code.

Two commands, and they sit in different halves of ``cli.py`` on purpose - so this
file is arranged the same way, and the assertions follow the arrangement:

- **``change-email`` runs as somebody.** It resolves the stored token, checks the
  password against that account, and then either mails a code or applies the
  change. Its output has to say which of those happened, because a person who
  thinks their address has moved when it has not is the failure this feature
  exists to prevent.
- **``confirm-email`` runs as nobody at all.** It reads no session file, and one
  test below deletes the session before answering to say so with the filesystem
  rather than in a comment. The code was mailed to the address being moved to, so
  it proves something a session does not - which is the whole reason somebody who
  asked at a desk can answer from a phone.

**The hasher and the wire are the two things replaced**, and both are replaced the
way every other presentation file replaces them. ``signed_in`` writes a credential
with ``FakePasswordHasher``, so a CLI that verified with argon2 would refuse a
correct password and look like a bug - the trap ``tests/presentation/api`` avoids
by injecting the same fake into ``create_app``. The mail channel is injected by
patching the two builders ``cli`` imported, which is the move
``test_cli_plans.py`` makes for the deliverer: the use case underneath is real, and
only the socket is fake.
"""

import functools
import os

import pytest

from app.composition_root import (
    build_confirm_email_change,
    build_request_email_change,
)
from app.presentation import cli
from app.presentation.cli import main
from tests.conftest import (
    TEST_USER_EMAIL,
    TEST_USER_PASSWORD,
    code_in,
    session_path_for,
    signed_in,
)

#: The account every test here moves. A real domain, because ``change-email`` is
#: one of the two places an address is *minted* and is therefore bound by the entry
#: rule - which is a property of the command rather than of this file.
ADDRESS = TEST_USER_EMAIL

#: Where it moves to.
NEW_ADDRESS = "test.new@example.com"

#: An address ``signup`` refuses today and an account can still hold, so a change
#: *to* it is refused by the same rule. See ``emailAddress``.
STRANDED = "nobody@localhost"

#: The four variables that make ``from_environment`` answer with settings rather
#: than ``None``, so that ``change-email`` takes the branch that mails a code. The
#: values are never dialled - the adapter is replaced by ``builders`` below - but
#: they must be *complete*, or the fallback is what the command would take.
MAIL_ENVIRONMENT = {
    "SMTP_HOST": "smtp.example.com",
    "SMTP_USER": "me@example.com",
    "SMTP_PASSWORD": "hunter2",
    "BUDGET_NOTIFY_TO": "chinedu@example.com",
}


def run(db, session, *argv):
    """Invoke the CLI in-process, with this test's database and session file."""
    return main(["--db", db, "--session", session, *argv])


@pytest.fixture
def db(tmp_path):
    """The database this file's CLI runs against, and the file is not incidental.

    A ``:memory:`` database would be created and discarded per connection, and
    these tests run the CLI *twice* - a request and an answer, in two invocations -
    so the two commands must be looking at one file. ``signed_in`` and ``run``
    already agree on ``tmp_path``; this is the name they agree on.
    """
    return str(tmp_path / "cli.db")


@pytest.fixture
def session(db):
    """Where the token goes for this database, by the same rule as ``run``."""
    return session_path_for(db)


@pytest.fixture
def builders(monkeypatch, password_hasher):
    """Wire the CLI's two email builders to this suite's hasher and a channel.

        builders()          # no channel: the installation's own mail is used, and
                            # with none configured the change applies immediately
        builders(channel)   # deliver through ``channel``

    **The builders rather than the adapter**, which is the choice
    ``test_cli_plans.py`` already made for the deliverer and for its reason: what
    is substituted is the piece the *presentation* reaches for, so everything the
    use case does with the result stays real. Patching
    ``composition_root.SmtpNotificationChannel`` would work too and would prove
    less - it would leave ``_channel_for``'s decision in the path while this leaves
    the builder's arguments in it, which is where a CLI-specific mistake would live.

    ``password_hasher`` is injected for the reason ``create_app`` takes one: the
    account ``signed_in`` writes holds a hash only the fake can verify. This is not
    weakening the test - the CLI's job here is to *ask* for a password and act on
    the answer, and which algorithm produced the stored hash is a fact about a
    different layer, tested against the real adapter in
    ``tests/infrastructure/security/``.

    Calling it twice in one test is the way a mail account *changes between two
    commands*, which is how a notice that cannot be delivered is set up: the
    request goes through one channel and the answer through another.
    """

    def _install(channel=None) -> None:
        monkeypatch.setattr(
            cli,
            "build_request_email_change",
            functools.partial(
                build_request_email_change,
                password_hasher=password_hasher,
                channel=channel,
            ),
        )
        monkeypatch.setattr(
            cli,
            "build_confirm_email_change",
            functools.partial(build_confirm_email_change, channel=channel),
        )

    return _install


@pytest.fixture
def mail_configured(monkeypatch):
    """Make this installation one that has an email account.

    The variables are set rather than the settings object passed, because that is
    the path under test: ``main`` reads the environment once per invocation and
    computes ``deferred_reason`` from it, so an installation that "has mail" only
    because a test handed it some would exercise a code path no deployment has.
    """
    for name, value in MAIL_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)


class TestAskingForTheChange:
    """``change-email``: a password at the prompt, and then one of two outcomes."""

    def test_a_code_is_sent_and_the_change_waits(
        self, db, session, typed_password, capsys, builders, build_channel, mail_configured
    ):
        """The pending outcome, and the two things its output must say.

        *Where* the code went and *when* it dies - not "fifteen minutes", which is a
        duration a person would have to do arithmetic with. And it names the next
        command, because the change has not happened and a person who closed the
        terminal here would otherwise be left with an account that is still at the
        old address and no idea that anything was pending.
        """
        channel = build_channel()
        builders(channel)
        signed_in(db)
        typed_password(TEST_USER_PASSWORD)

        assert run(db, session, "change-email", NEW_ADDRESS) == 0

        out = capsys.readouterr().out
        assert f"confirmation code sent to {NEW_ADDRESS}" in out
        assert "expires" in out
        assert "confirm-email" in out
        assert [message.recipient for message in channel.sent] == [NEW_ADDRESS]

    def test_the_account_has_not_moved_yet(
        self, db, session, typed_password, capsys, builders, build_channel, mail_configured
    ):
        """``whoami`` after a request, which is the difference made visible.

        A request is not a change, and the only way to tell from a terminal is to
        ask who this session is - so a person who has just been told a code is in
        the post can check, and the answer is that nothing has happened yet. This
        is the assertion ``_change_email``'s "has *not* happened yet" comment makes
        about the outcome rather than about the words.
        """
        builders(build_channel())
        signed_in(db)
        typed_password(TEST_USER_PASSWORD)
        run(db, session, "change-email", NEW_ADDRESS)
        capsys.readouterr()

        assert run(db, session, "whoami") == 0

        assert f"email: {ADDRESS}" in capsys.readouterr().out

    def test_with_no_mail_the_change_is_applied_and_says_why(
        self, db, session, typed_password, capsys, builders
    ):
        """**The third outcome, and the one a fresh install meets.**

        No channel and no environment, so ``settings`` is ``None`` and the change
        happens on the password proof alone. The note names the missing variable
        rather than saying "no email is configured" and leaving somebody to guess
        which of four variables that means - the same sentence the deliverers
        report, because it is the same fact.

        It is printed *after* the line saying the address changed, which is the
        order that makes it a footnote: the command succeeded, and this is why it
        succeeded without the step the person might have expected.
        """
        builders()
        signed_in(db)
        typed_password(TEST_USER_PASSWORD)

        assert run(db, session, "change-email", NEW_ADDRESS) == 0

        out = capsys.readouterr().out
        assert f"email changed to {NEW_ADDRESS}" in out
        assert f"was {ADDRESS}" in out
        assert "SMTP_HOST is not set" in out

    def test_a_wrong_password_is_refused_and_nothing_is_sent(
        self, db, session, typed_password, capsys, builders, build_channel, mail_configured
    ):
        """The one refusal the two commands share with ``login``, and the same words.

        Read from stderr, because that is where this presentation puts a refusal -
        the arrangement ``main``'s handler makes for every domain error and every
        ``CliError``, so that a command that failed is distinguishable from one that
        printed something. The empty channel is the second half: the credential is
        checked before anything is composed, so a wrong password cannot post a code
        to an address the caller does not control.
        """
        channel = build_channel()
        builders(channel)
        signed_in(db)
        typed_password("not-the-password")

        assert run(db, session, "change-email", NEW_ADDRESS) == 1

        assert "those details did not match an account" in capsys.readouterr().err
        assert channel.attempts == []

    def test_an_unusable_address_is_refused_with_the_reason(
        self, db, session, typed_password, capsys, builders, build_channel, mail_configured
    ):
        """Exit 1, and a sentence naming the address and what it wanted.

        The reason matters more here than the status: "nobody@localhost cannot be
        used as an email address; it needs a domain with a dot in it" is something
        a person can act on, and the alternative - a code posted to an address no
        provider will bill - is the trap the entry rule was built to close.
        """
        channel = build_channel()
        builders(channel)
        signed_in(db)
        typed_password(TEST_USER_PASSWORD)

        assert run(db, session, "change-email", STRANDED) == 1

        err = capsys.readouterr().err
        assert STRANDED in err
        assert "needs a domain with a dot" in err
        assert channel.attempts == []

    def test_it_needs_a_session(self, db, session, typed_password, capsys, builders):
        """Asking is done *as* an account, so this is where the actor line bites.

        Nothing is signed in and the command refuses before it prompts for anything.
        Deliberately left at the contrast with ``confirm-email`` below, which works
        on this same machine in this same state: one of the two halves needs to know
        who is asking, and the other is told by the code.
        """
        builders()
        typed_password(TEST_USER_PASSWORD)

        assert run(db, session, "change-email", NEW_ADDRESS) == 1

        assert "not signed in" in capsys.readouterr().err


class TestAnsweringWithTheCode:
    """``confirm-email``: the code, and nothing else at all."""

    def test_the_code_moves_the_account_and_the_old_address_is_told(
        self, db, session, typed_password, capsys, builders, build_channel, mail_configured
    ):
        """The whole flow, in two invocations against one database.

        Both mails are asserted, and their order is the point: the code went to the
        address being moved *to*, and the notice went to the address being moved
        *from*. A notice sent to the new address would be an announcement to the
        only person who already knows.
        """
        channel = build_channel()
        builders(channel)
        signed_in(db)
        typed_password(TEST_USER_PASSWORD)
        run(db, session, "change-email", NEW_ADDRESS)
        code = code_in(channel.sent[0])
        capsys.readouterr()

        typed_password(code)
        assert run(db, session, "confirm-email") == 0

        out = capsys.readouterr().out
        assert f"email changed to {NEW_ADDRESS}" in out
        assert f"was {ADDRESS}" in out
        assert f"{ADDRESS} was told about the change" in out
        assert [message.recipient for message in channel.sent] == [NEW_ADDRESS, ADDRESS]

    def test_it_needs_no_session_file_at_all(
        self, db, session, typed_password, capsys, builders, build_channel, mail_configured
    ):
        """**The property the command's placement in ``cli.py`` was chosen for.**

        The session file is *deleted* before the code is answered, and ``--session``
        still points at where it used to be - so this is the state of a machine
        where somebody asked in a browser, or on another computer, and is now
        answering from a terminal that has never logged in. The last assertion is
        that the file is still gone: a command that quietly recreated it would pass
        the exit code and fail this.
        """
        channel = build_channel()
        builders(channel)
        signed_in(db)
        typed_password(TEST_USER_PASSWORD)
        run(db, session, "change-email", NEW_ADDRESS)
        code = code_in(channel.sent[0])
        os.remove(session)
        capsys.readouterr()

        typed_password(code)
        assert run(db, session, "confirm-email") == 0

        assert f"email changed to {NEW_ADDRESS}" in capsys.readouterr().out
        assert not os.path.exists(session)

    def test_a_code_that_means_nothing_is_refused(
        self, db, session, typed_password, capsys, builders
    ):
        """The refusal is the class name, because the domain raised bare.

        ``InvalidEmailChangeTokenError`` carries no message on purpose - a sentence
        naming the thing that did not match would be an oracle for somebody guessing
        - so ``_describe`` falls back to the class name, exactly as
        ``errors._detail`` does on the other presentation. A client that had to read
        two shapes for one refusal would be reading this one twice.
        """
        builders()
        typed_password("not-a-code")

        assert run(db, session, "confirm-email") == 1
        assert "InvalidEmailChangeTokenError" in capsys.readouterr().err

    def test_a_notice_that_bounces_is_a_note_and_not_a_failure(
        self, db, session, typed_password, capsys, builders, build_channel, mail_configured
    ):
        """The change stands, and the failure is printed as the footnote it is.

        The builders are installed twice with two different channels, which is the
        CLI's version of "the mail account stopped working between the two
        commands" - there is one channel per invocation here rather than one per
        request, so a test that wants the second send to fail has to say which send
        it means.

        Exit 0 is the assertion that matters most. The address has already moved by
        the time the notice is attempted, and a command that failed here would tell
        somebody their change had not happened when it had.
        """
        channel = build_channel()
        builders(channel)
        signed_in(db)
        typed_password(TEST_USER_PASSWORD)
        run(db, session, "change-email", NEW_ADDRESS)
        code = code_in(channel.sent[0])
        capsys.readouterr()

        builders(build_channel(failures=[OSError("the mailbox is full")]))
        typed_password(code)

        assert run(db, session, "confirm-email") == 0

        out = capsys.readouterr().out
        assert f"email changed to {NEW_ADDRESS}" in out
        assert f"{ADDRESS} could not be told" in out
        assert "the mailbox is full" in out
