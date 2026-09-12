"""The CLI as a real client: sign up, log in, and be somebody.

**The only CLI file that runs argon2.** Every other test in ``tests/presentation``
starts from the ``signed_in`` helper, which registers with the suite's fake hasher
- so those files exercise a CLI that consumes a session without ever hashing a
password. This file is where the other half lives, and it has to: ``signup`` and
``login`` are the two commands whose entire job is a password, and a fake standing
in for the hasher here would mean nothing in the suite ever proved that the CLI
can verify a password the CLI wrote.

The cost is a handful of real hashes - a few hundred milliseconds - against the
hundred-plus tests in the sibling files, and that trade is the reason the fake
exists at all.

Nothing here is mocked. The commands run through ``main`` in-process against a
real database file, the password arrives through a monkeypatched ``getpass``
because a test cannot type, and the token lands in a real file with a real mode.
"""

import os
from uuid import uuid4

import pytest

from app.presentation.cli import main
from tests.conftest import session_path_for

#: A password that satisfies the policy and is not the suite's shared constant.
#: Deliberately a different value from ``TEST_USER_PASSWORD``: this file is the
#: one place where a real hash is written and read back, and a test that reused
#: the constant everywhere would not notice a CLI that ignored the password it was
#: given.
PASSWORD = "a-long-enough-passphrase"

ADDRESS = "cli@localhost"


@pytest.fixture
def db(tmp_path):
    """The database this file's CLI runs against.

    No autouse ``signed_in`` fixture here, unlike the three sibling files. This one
    is *about* the commands that come before signing in, so a fixture that started
    every test already logged in would have to be undone by most of them - and the
    state a test depends on belongs in the test.
    """
    return str(tmp_path / "cli.db")


@pytest.fixture
def session(db):
    """Where the token goes for this database, by the same rule as ``run``."""
    return session_path_for(db)


@pytest.fixture
def typed(typed_password):
    """Answer the CLI's password prompt with this file's password.

    The suite's ``typed_password`` fixture with a default bound to it, and the
    binding is what makes this file different: the password here is deliberately
    *not* ``TEST_USER_PASSWORD``, because this is the one presentation file whose
    passwords are really hashed - and a test that reused the shared constant
    everywhere would not notice a CLI that ignored the password it was given.

    Every helper below takes ``typed`` for the same reason ``run`` takes ``db``: the
    five call sites in a test would otherwise each spell out which password they
    meant, and the interesting ones are the four that pass something else.
    """

    def _typed(password: str = PASSWORD):
        typed_password(password)

    return _typed


def run(db, session, *argv):
    """Invoke the CLI in-process, with this test's database and session file."""
    return main(["--db", db, "--session", session, *argv])


def register(db, session, typed, address=ADDRESS, password=PASSWORD):
    typed(password)
    return run(db, session, "signup", address)


def sign_in(db, session, typed, address=ADDRESS, password=PASSWORD):
    typed(password)
    return run(db, session, "login", address)


class TestSigningUp:
    def test_it_registers_and_says_so(self, db, session, typed, capsys):
        assert register(db, session, typed) == 0

        out = capsys.readouterr().out
        assert ADDRESS in out
        assert "registered" in out

    def test_it_does_not_sign_anybody_in(self, db, session, typed, capsys):
        """The separation the whole phase rests on, at the command a user types.

        The password is in hand and ``_login`` is a function call away, which is
        exactly why this is asserted. A ``signup`` that also wrote a session file
        would mean the first token on a machine came from a command that never
        checked the password it had just been given - and the user would have no
        way to know their password was never verified.
        """
        register(db, session, typed)
        capsys.readouterr()

        assert not os.path.exists(session)

    def test_it_says_what_to_do_next(self, db, session, typed, capsys):
        """The prompt is the only place a user learns that signup is not login."""
        register(db, session, typed)

        assert "login" in capsys.readouterr().out

    def test_the_address_is_folded(self, db, session, typed, capsys):
        """``whoami`` afterwards reports the one spelling, not what was typed."""
        register(db, session, typed, address="  CLI@LocalHost  ")
        capsys.readouterr()

        sign_in(db, session, typed, address="cli@localhost")
        run(db, session, "whoami")

        assert "cli@localhost" in capsys.readouterr().out

    def test_a_second_account_at_the_same_address_fails(self, db, session, typed, capsys):
        register(db, session, typed)
        capsys.readouterr()

        assert register(db, session, typed) == 1

        err = capsys.readouterr().err
        assert "error:" in err
        assert "already registered" in err

    def test_a_weak_password_fails_without_taking_the_address(self, db, session, typed, capsys):
        """The policy is enforced on the way in, and the refusal leaves nothing behind.

        Both halves matter: the exit code says the command did not do the thing,
        and the successful registration afterwards says the address is still free -
        which is what stops a typo'd sign-up from stranding an address behind an
        account nobody can log into.

        The assertion is the *sentence* rather than the exception class, and that is
        the difference between this file and its API counterpart. ``_describe``
        renders ``str(exc)`` and falls back to the class name only when a message is
        empty, so what a person reads at the terminal is English. The API sends the
        class name because its reader is a program that has to branch on it. Two
        presentations, two vocabularies - and each test file asserts the one its
        reader actually sees.
        """
        assert register(db, session, typed, password="short") == 1
        assert "at least 8 characters" in capsys.readouterr().err

        assert register(db, session, typed) == 0

    def test_a_malformed_address_fails(self, db, session, typed, capsys):
        assert register(db, session, typed, address="not-an-address") == 1

        assert "must contain '@'" in capsys.readouterr().err

    def test_the_password_never_appears_in_the_output(self, db, session, typed, capsys):
        """Neither echo, nor in an error. The likeliest place for a secret to leak is a message.

        ``_prompt_password`` keeps it off the command line, and this is the other
        half: it must not come back out. A ``--password`` flag would have failed
        this in the shell history before the process even started.
        """
        register(db, session, typed)
        sign_in(db, session, typed)
        run(db, session, "whoami")

        captured = capsys.readouterr()
        assert PASSWORD not in captured.out
        assert PASSWORD not in captured.err

    def test_a_wrong_password_never_appears_in_an_error(self, db, session, typed, capsys):
        register(db, session, typed)
        capsys.readouterr()

        sign_in(db, session, typed, password="not-the-one-that-was-set")

        assert "not-the-one-that-was-set" not in capsys.readouterr().err


class TestSigningIn:
    def test_it_writes_the_session_file(self, db, session, typed, capsys):
        register(db, session, typed)
        capsys.readouterr()

        assert sign_in(db, session, typed) == 0

        assert os.path.exists(session)
        with open(session, encoding="utf-8") as handle:
            assert handle.read().strip()

    def test_the_file_is_mode_0600(self, db, session, typed, capsys):
        """Readable by its owner and nobody else, from the moment it exists.

        ``_write_token`` uses ``os.open`` with the mode rather than writing and
        then chmodding, so there is no window in which the token is world-readable
        - and this asserts the outcome of that choice. Note the umask can only
        remove bits, so the comparison is "no group or other access" rather than
        equality with ``0o600``.
        """
        register(db, session, typed)
        sign_in(db, session, typed)
        capsys.readouterr()

        mode = os.stat(session).st_mode & 0o777

        assert mode & 0o077 == 0, oct(mode)

    def test_it_reports_who_and_until_when(self, db, session, typed, capsys):
        register(db, session, typed)
        capsys.readouterr()

        sign_in(db, session, typed)

        out = capsys.readouterr().out
        assert ADDRESS in out
        assert "expires" in out

    def test_a_wrong_password_fails_and_writes_nothing(self, db, session, typed, capsys):
        register(db, session, typed)
        capsys.readouterr()

        assert sign_in(db, session, typed, password="not-the-one-that-was-set") == 1

        assert "did not match an account" in capsys.readouterr().err
        assert not os.path.exists(session)

    def test_an_unknown_address_fails_with_the_same_words(self, db, session, typed, capsys):
        """The account-enumeration property, at the terminal.

        Held by the domain rather than by this presentation, which is the point of
        asserting it here: the CLI does nothing to make it true, so a change that
        broke it would have to be made one layer down.
        """
        assert sign_in(db, session, typed, address="nobody@localhost") == 1
        unknown = capsys.readouterr().err

        register(db, session, typed)
        capsys.readouterr()
        assert sign_in(db, session, typed, password="wrong-password-entirely") == 1
        wrong = capsys.readouterr().err

        # The messages differ only in the path prefix, which belongs to the CLI's
        # own `error: ` rendering rather than to the refusal.
        assert unknown.split(": ", 1)[1] == wrong.split(": ", 1)[1]

    def test_it_overwrites_an_existing_session(self, db, session, typed, capsys):
        """Signing in as somebody else while signed in is a normal thing to want.

        Refusing it would mean two commands to do one thing, with a state in
        between where the machine is signed in as nobody.
        """
        register(db, session, typed, address="first@localhost")
        register(db, session, typed, address="second@localhost")
        capsys.readouterr()
        sign_in(db, session, typed, address="first@localhost")
        capsys.readouterr()

        sign_in(db, session, typed, address="second@localhost")
        run(db, session, "whoami")

        assert "second@localhost" in capsys.readouterr().out


class TestBeingSomebody:
    def test_whoami_reports_the_account_the_token_belongs_to(self, db, session, typed, capsys):
        register(db, session, typed)
        sign_in(db, session, typed)
        capsys.readouterr()

        assert run(db, session, "whoami") == 0

        out = capsys.readouterr().out
        assert f"email: {ADDRESS}" in out
        assert "user_id:" in out

    def test_a_command_that_needs_somebody_needs_a_session(self, db, session, typed, capsys):
        """The precondition, stated as the thing a user would hit.

        No account, no session file, and ``whoami`` declines rather than guessing -
        which is the whole difference from the ``--user`` flag this replaced. That
        flag defaulted to a name, so the command would have answered with a
        confident lie.
        """
        assert run(db, session, "whoami") == 1

        err = capsys.readouterr().err
        assert "error:" in err
        assert "not signed in" in err
        assert "login" in err

    def test_a_wallet_command_needs_a_session_too(self, db, session, typed, capsys):
        """Every command that is not identity, not just ``whoami``.

        One assertion covering the routing rather than one per command: ``open``
        reaches the same resolve-before-dispatch line, and the thing being tested
        is that the line is there at all.
        """
        assert run(db, session, "open", "--currency", "NGN") == 1

        assert "not signed in" in capsys.readouterr().err

    def test_the_refusal_names_the_path_and_the_fix(self, db, session, typed, capsys):
        """A user who has never logged in has to be told where to look and what to type.

        ``NotSignedInError`` is a ``CliError`` rather than a ``MoneyError``
        precisely because this is a presentation concern: "there is no file here"
        is not a fact about money or identity, and the message names the file
        because the fix for a wrong ``--session`` is a different fix from the one
        for never having logged in.
        """
        run(db, session, "whoami")

        err = capsys.readouterr().err
        assert session in err

    def test_an_empty_session_file_is_refused(self, db, session, typed, capsys):
        """The value a bug produces - a truncated write, a stray redirect.

        Refused as "not signed in" rather than sent to the store as an empty token,
        because an empty file is a state a *person* can be in - ``touch``, a
        failed write - and the useful message is the one that says so.
        """
        with open(session, "w", encoding="utf-8"):
            pass

        assert run(db, session, "whoami") == 1
        err = capsys.readouterr().err
        assert "empty" in err

    def test_a_token_from_another_installation_is_refused(self, db, session, typed, capsys):
        """A session file copied between machines, or a database restored without its sessions.

        A well-formed token that this database has never seen, so it reaches
        ``ResolveActorFromSession`` and is refused there - the 401 case rather than
        the "not signed in" one, and worth its own test because the two paths are
        different code.
        """
        register(db, session, typed)
        sign_in(db, session, typed)
        capsys.readouterr()
        with open(session, "w", encoding="utf-8") as handle:
            handle.write("a-token-this-database-has-never-issued\n")

        assert run(db, session, "whoami") == 1

        assert "not a valid session" in capsys.readouterr().err


class TestSigningOut:
    def test_it_discards_the_session_file(self, db, session, typed, capsys):
        register(db, session, typed)
        sign_in(db, session, typed)
        capsys.readouterr()

        assert run(db, session, "logout") == 0

        assert not os.path.exists(session)

    def test_it_reports_that_it_did(self, db, session, typed, capsys):
        register(db, session, typed)
        sign_in(db, session, typed)
        capsys.readouterr()

        run(db, session, "logout")

        assert "logged out" in capsys.readouterr().out

    def test_the_token_is_gone_from_the_server_too(self, db, session, typed, capsys):
        """Ending it locally is not enough, and the order in ``_logout`` says why.

        Server first, then the file. Deleting the file first would mean a failure
        in between leaves a token that still works with no copy of it anywhere -
        the session is not ended and the user cannot end it, because the thing that
        names it is gone. So this puts the token back and asserts the server
        refuses it.
        """
        register(db, session, typed)
        sign_in(db, session, typed)
        capsys.readouterr()
        with open(session, encoding="utf-8") as handle:
            token = handle.read().strip()

        run(db, session, "logout")
        capsys.readouterr()
        with open(session, "w", encoding="utf-8") as handle:
            handle.write(token + "\n")

        assert run(db, session, "whoami") == 1
        assert "not a valid session" in capsys.readouterr().err

    def test_signing_out_twice_is_not_an_error(self, db, session, typed, capsys):
        """The postcondition is "there is no usable session at this path", and it holds.

        The second ``logout`` finds no file, prints the note ``_read_token`` wrote
        - which already says what is wrong and what to do - and exits 0. Reporting
        a failure to a caller whose intent was satisfied is the thing being avoided.
        """
        register(db, session, typed)
        sign_in(db, session, typed)
        capsys.readouterr()

        assert run(db, session, "logout") == 0
        capsys.readouterr()
        assert run(db, session, "logout") == 0

    def test_signing_out_when_never_signed_in_is_not_an_error(self, db, session, typed, capsys):
        assert run(db, session, "logout") == 0

        assert "not signed in" in capsys.readouterr().out

    def test_a_command_after_signing_out_fails_with_a_clear_message(
        self, db, session, typed, capsys
    ):
        """The round trip's last step, and the one verification step 4 asks for.

        ``logout`` → ``balance`` must fail with something a person can act on
        rather than a traceback or a 500 - which it does because the failure is
        ``NotSignedInError``, caught at the top of ``main`` beside every domain
        refusal.
        """
        register(db, session, typed)
        sign_in(db, session, typed)
        run(db, session, "open", "--currency", "NGN")
        capsys.readouterr()

        run(db, session, "logout")
        capsys.readouterr()

        # A *parseable* wallet id, so the command gets as far as resolving an actor
        # and fails there. An unparseable one would exit 2 from argparse before
        # ``main``'s handler ever ran, which is a different failure and not the one
        # being tested.
        assert run(db, session, "balance", str(uuid4())) == 1

        err = capsys.readouterr().err
        assert "not signed in" in err
        assert "Traceback" not in err

    def test_signing_out_does_not_delete_the_account(self, db, session, typed, capsys):
        """The account survives, so signing in again works.

        The token goes to the server and the account stays, which is what makes
        ``logout`` a logout rather than a deletion - and the reason this is worth
        asserting is that both operations end with a row being removed.
        """
        register(db, session, typed)
        sign_in(db, session, typed)
        capsys.readouterr()
        run(db, session, "logout")
        capsys.readouterr()

        assert sign_in(db, session, typed) == 0
        assert run(db, session, "whoami") == 0


class TestTheCommandsThatNeedNobody:
    """``signup``, ``login``, ``logout`` and ``plan tick`` run before any actor is resolved.

    The routing is the design rather than an optimisation. The three identity
    commands cannot require a session - they are how a session comes to exist, or
    ceases to - and ``plan tick`` must not, because it serves the whole
    installation and there is no person it could act as. Resolving an actor first,
    as ``main`` used to do unconditionally, would have made a scheduler that
    requires a login.
    """

    def test_scheduler_runs_with_no_session_at_all(self, db, session, typed, capsys):
        """Verification step 4's last clause, and the sharpest form of it.

        No account, no session file, and ``plan tick`` still runs - because it is
        dispatched above the line that resolves an actor. This is what a cron entry
        looks like, and it is the reason the routing had to change rather than the
        reason it was convenient to.
        """
        assert not os.path.exists(session)

        assert run(db, session, "plan", "tick") == 0

    def test_and_it_says_so_rather_than_silently_doing_nothing(self, db, session, typed, capsys):
        """Exit 0 with a report, so a cron job's log distinguishes "ran" from "failed"."""
        run(db, session, "plan", "tick")

        captured = capsys.readouterr()
        assert captured.out or captured.err

    def test_a_missing_session_does_not_stop_it(self, db, session, typed, capsys):
        """A file that does not exist and a file that cannot be read are both irrelevant here.

        Because nothing reads it. Asserted by pointing ``--session`` at a path in a
        directory that does not exist, which ``_write_token`` would have had to
        create - and which nothing here touches.
        """
        elsewhere = os.path.join(os.path.dirname(session), "no-such-dir", "session")

        assert run(db, elsewhere, "plan", "tick") == 0

    def test_identity_commands_are_not_blocked_by_a_session_file(self, db, session, typed, capsys):
        """A corrupt session file must not stop somebody signing in.

        Signing in *over* a broken token is the ordinary repair, and it only works
        because ``login`` is dispatched before anything reads the file. Writing a
        garbage value and then logging in is the test.
        """
        with open(session, "w", encoding="utf-8") as handle:
            handle.write("garbage\n")
        register(db, session, typed)

        assert sign_in(db, session, typed) == 0
        assert run(db, session, "whoami") == 0
