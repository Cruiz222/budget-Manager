"""The CLI walking a Google signup: one token, no password, and no session.

``signup-google`` and ``login-google`` are the first pair of commands here whose
warrant arrives from **outside this installation**. Every other prompt in
``cli.py`` asks for something this system or its operator produced - a password
somebody chose, a code this system mailed or texted, a token it issued. These two
ask for a value Google signed. The chain they close is argued in
``sign_up_with_google`` rather than here; what this file checks is what a person
meets at a terminal.

**The token is read at a prompt and never in argv**, and the assertion for that is
the absence of the literal in what was printed: an id_token is a live bearer
credential for about an hour, so a copy in scrollback or in a shell's history file
is a session on this machine that somebody else can start.

**No password is asked for, and the fixture is what says so.** ``typed`` raises on
any prompt the test did not name, so a signup that had grown a password question
would fail every test in the first class rather than pass them quietly - which
matters, because asking one here would be asking somebody to choose a secret for
an account whose whole point is that it has none.

**Signing up writes no session**, asserted on the filesystem. ``login``,
``login-phone`` and ``login-google`` are the three commands that reach
``_write_token``, and a fourth would be a token handed out for an account nobody
proved they hold.

**The two collisions are tested at the terminal, not only in the use case**,
because their remedies differ and the terminal is where a person reads them:
"log in" against "use another address". One of those tests creates its account
with ``signup``, which is the single real argon2 hash this file pays - the claim
being that an account made the ordinary way is not silently absorbed into a Google
identity, and a first account created *by* the Google path would not test that.

**The verifier is replaced at the builder**, which is the move every presentation
file here makes, and note what is *not* set: the environment. An injected verifier
makes ``google_verifier_for`` return it, so ``GOOGLE_CLIENT_ID`` is absent
throughout and the one test about an installation without one needs no arrangement
at all. Only ``verifier`` is bound and never ``settings`` - the commands pass
``settings=google`` themselves, and ``functools.partial`` raises a ``TypeError``
when the same keyword is bound twice.

That arrangement has one blind spot, and ``TestTheClientId`` exists to close it
deliberately: ``google_verifier_for`` resolves an injected verifier *first*, so a
command that handed the builder ``settings=None`` would behave identically in
every test above while refusing every sign-in on a configured installation. That
class asserts on the argument rather than on the outcome, which is the only place
the difference is visible.
"""

import functools
import os

import pytest

from app.composition_root import (
    build_log_in_with_google,
    build_sign_up_with_google,
)
from app.infrastructure.persistence.sqlite_unit_of_work import (
    open_sqlite_connection,
)
from app.presentation import cli
from app.presentation.cli import main
from tests.conftest import (
    TEST_GOOGLE_CLIENT_ID,
    TEST_GOOGLE_SETTINGS,
    session_path_for,
)

#: The address the Google identities below carry, and the one the account holds.
#: ``example.com`` for the reason every other presentation file gives: nobody can
#: mistake a reserved domain for a person.
ADDRESS = "ada@example.com"

#: A second address, used where two identities must not collide on one.
OTHER_ADDRESS = "grace@example.com"

#: Google's stable identifier for an account, and deliberately **not** an address.
#: It is what the account is looked up by, and a fixture that used an email-shaped
#: subject would make the "the subject is the key and the address is a record" pair
#: of claims indistinguishable from a bug where the two were swapped.
SUBJECT = "10769150350006150715113082367"

#: A second subject, for the identity that must collide on its *address*.
OTHER_SUBJECT = "10769150350006150715113082368"

#: The password the account created by ``signup`` is given, and the one typed into
#: a ``login`` that must fail. Deliberately not ``TEST_USER_PASSWORD``: this file
#: hashes for real, and a test that reused the suite's constant would not notice a
#: CLI that ignored the password it was handed.
PASSWORD = "a-long-enough-passphrase"

#: What ``_prompt_code`` is labelled at this pair's two call sites, and the key the
#: ``typed`` fixture answers by. An id_token rather than "code", because a person
#: who has just come from a browser is holding a token and not a mailed code.
TOKEN_PROMPT = "Google id_token"

#: What ``_prompt_password`` asks for, at the one test that runs ``signup``.
PASSWORD_PROMPT = "password"

#: The second of that pair, when the two answers must differ.
AGAIN_PROMPT = "password again"


def run(db, session, *argv):
    """Invoke the CLI in-process, with this test's database and session file."""
    return main(["--db", db, "--session", session, *argv])


@pytest.fixture
def db(tmp_path):
    """The database this file's CLI runs against, and the file is not incidental.

    Each test runs the CLI *twice* - a registration and then a login - and the
    second invocation is a separate process-shaped call, so both must be looking at
    one file. A ``:memory:`` database would be created and discarded per connection.
    """
    return str(tmp_path / "cli.db")


@pytest.fixture
def session(db):
    """Where the token would go, by the same rule as ``run``."""
    return session_path_for(db)


@pytest.fixture
def typed(monkeypatch):
    """Answer the CLI's prompts **by name**, so a test says what it types where.

        typed({TOKEN_PROMPT: token})                       # the Google pair
        typed({PASSWORD_PROMPT: "hunter2"})                # answered twice
        typed({PASSWORD_PROMPT: "a", AGAIN_PROMPT: "b"})   # a mismatch

    The same fake the phone and reset files use, and for its reason: one invocation
    asks for two *different* things, so a queue of values would make every test
    depend on the order the command happens to ask in. Answering a prompt the test
    did not name is an ``AssertionError`` that names it - which is what makes
    "this command asks for nothing else" an assertion rather than a formality, and
    it is the whole of the claim that no password is asked for here.

    The one default is that ``AGAIN_PROMPT`` takes whatever ``PASSWORD_PROMPT`` was
    given, because ``signup`` confirms a password by asking for it twice.
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
def verifier(monkeypatch, build_google_verifier):
    """Wire both Google builders to a fake verifier, and hand it back.

        verifier.mint(SUBJECT, ADDRESS)                # a token that resolves
        verifier.mint(SUBJECT, ADDRESS, email_verified=False)

    **The builders rather than the adapter**, which is the choice the phone file
    makes and for its reason: what is substituted is the piece the *presentation*
    reaches for, so everything the use cases do with the result stays real - and
    the arguments the builders are handed stay in the path where a CLI-specific
    mistake would live.

    **Only ``verifier`` is bound, and the commands' own ``settings=`` must not be.**
    Both commands call the builder with ``settings=google`` - the value they read
    from an environment this suite clears, so ``None`` - and ``functools.partial``
    raises a ``TypeError`` if the same keyword is bound twice. It does not need to
    be bound: ``google_verifier_for`` resolves the injected verifier *first*, so a
    partial that supplies one means the environment is never consulted.

    That ordering is also this fixture's blind spot, and it is worth naming where
    it is created rather than where it bites: a command that passed ``settings=None``
    would look identical from here. ``TestTheClientId`` is the test that sees the
    difference.
    """
    fake = build_google_verifier()
    monkeypatch.setattr(
        cli,
        "build_sign_up_with_google",
        functools.partial(build_sign_up_with_google, verifier=fake),
    )
    monkeypatch.setattr(
        cli,
        "build_log_in_with_google",
        functools.partial(build_log_in_with_google, verifier=fake),
    )
    return fake


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


def registered(db, session, verifier, typed) -> str:
    """Create an account the way the CLI creates one, and return the token.

    The command run for real - a token minted, read out of the fake, typed at the
    prompt - rather than a row written straight into the store. That is why these
    tests live here rather than in the application file: a seeded row would only
    resemble the account this flow produces.
    """
    token = verifier.mint(SUBJECT, ADDRESS)
    typed({TOKEN_PROMPT: token})
    assert run(db, session, "signup-google") == 0
    return token


class TestRegisteringTheAccount:
    """``signup-google``: one token, no password, no session."""

    def test_the_account_is_created_and_the_output_says_so(
        self, db, session, capsys, verifier, typed
    ):
        """The first line, and the only one that reports the creation itself."""
        token = verifier.mint(SUBJECT, ADDRESS)
        typed({TOKEN_PROMPT: token})

        assert run(db, session, "signup-google") == 0

        out = capsys.readouterr().out
        assert f"registered {ADDRESS}" in out
        assert accounts_in(db) == 1

    def test_the_next_line_points_at_the_google_login(
        self, db, session, capsys, verifier, typed
    ):
        """**Both strings written out in full, which is the phone file's lesson.**

        That file records an assertion reading ``assert "next: run 'login'" not in
        out`` which went on passing after the line it was about had changed, because
        the new text had a longer command name in it. So the pair here is spelled the
        same way: the Google line is present, and the password line is not - the
        second being a claim worth making, since ``login`` resolves an address and
        this account's is a record rather than something it was signed up with.

        It is also the only line that tells a person what to do next. Nothing has
        happened yet: a terminal closed here leaves an account nobody is signed in as.
        """
        token = verifier.mint(SUBJECT, ADDRESS)
        typed({TOKEN_PROMPT: token})

        run(db, session, "signup-google")

        out = capsys.readouterr().out
        assert "next: run 'login-google' to start a session" in out
        assert "next: run 'login' to start a session" not in out

    def test_it_writes_no_session_file(self, db, session, verifier, typed):
        """**A signup has no session to write, and this is where that is checked.**

        The account exists and nobody is signed in as it. A command that also
        started a session would hand out a credential the caller has not used - and
        it would do it on the machine of somebody who has proved only that they
        hold *a* Google account, not that the account is this one.
        """
        registered(db, session, verifier, typed)

        assert not os.path.exists(session)

    def test_the_token_is_not_printed(self, db, session, capsys, verifier, typed):
        """**The absence, in the strongest form the terminal allows.**

        The token is a live bearer credential, and it arrived from somewhere this
        command cannot see. Echoing it would write it to scrollback and to anything
        recording the session, on a machine whose operator need not be the person
        whose Google account it is. The second assertion is what makes the first
        mean something: the token really was the one presented.
        """
        token = verifier.mint(SUBJECT, ADDRESS)
        typed({TOKEN_PROMPT: token})

        run(db, session, "signup-google")

        assert token not in capsys.readouterr().out
        assert verifier.attempts == [token]

    def test_no_password_is_asked_for(self, db, session, verifier, typed):
        """``typed`` is passed the token and nothing else, so this is an assertion.

        A password prompt here would be asking somebody to choose a secret for an
        account that has none by design - and the person would have no way to know
        the secret they chose is not what lets them in. The account's way in is the
        Google subject, and ``signup`` remains the command that sets a password.
        """
        token = verifier.mint(SUBJECT, ADDRESS)
        typed({TOKEN_PROMPT: token})

        assert run(db, session, "signup-google") == 0

    def test_a_token_nobody_minted_is_refused(
        self, db, session, capsys, verifier, typed
    ):
        """Exit 1, with the verifier's refusal and no account.

        The common case for a stranger poking at this: a string that is not a token
        Google signed for this application. It is one class for every way that can
        be true - unknown, malformed, expired, wrong signature, wrong audience -
        because the remedy is identical and a message that separated them would
        describe a forgery to the forger.
        """
        typed({TOKEN_PROMPT: "a-token-nobody-ever-minted"})

        assert run(db, session, "signup-google") == 1

        assert "not a valid Google identity token" in capsys.readouterr().err
        assert accounts_in(db) == 0

    def test_an_unverified_address_is_refused(
        self, db, session, capsys, verifier, typed
    ):
        """The rendering of a rule whose argument lives one layer down.

        ``GoogleIdentity`` carries the reasoning: an account's address is what
        ``RequestPasswordReset`` mails a code to, with no credential check, so an
        account holding an address Google has merely *claimed* could be taken over
        by whoever controls that address. What this test adds is the part only the
        CLI can show - the sentence reaches a person, and the exit code is 1.
        """
        token = verifier.mint(SUBJECT, ADDRESS, email_verified=False)
        typed({TOKEN_PROMPT: token})

        assert run(db, session, "signup-google") == 1

        assert "Google has not verified that address" in capsys.readouterr().err
        assert accounts_in(db) == 0

    def test_a_google_account_that_already_signed_up_here_is_refused(
        self, db, session, capsys, verifier, typed
    ):
        """The refusal whose remedy is "log in", and the terminal says which.

        The same subject arrives twice - the same person, having forgotten they
        already registered. Distinct from the address collision below because the
        advice differs, and the subject is checked first for exactly this reason: it
        is the one the caller cannot work around, and a person sent to make a second
        account would make one.
        """
        registered(db, session, verifier, typed)
        token = verifier.mint(SUBJECT, OTHER_ADDRESS)
        typed({TOKEN_PROMPT: token})

        assert run(db, session, "signup-google") == 1

        assert "already has an account here" in capsys.readouterr().err
        assert accounts_in(db) == 1

    def test_an_address_another_account_holds_is_refused(
        self, db, session, capsys, verifier, typed
    ):
        """**The decision this flow was planned around, at the terminal.**

        The account is made with ``signup`` rather than with this command, and that
        is the point of the test rather than a convenience: the claim is that an
        account somebody already has is never silently linked to a Google identity,
        and a first account created *by* the Google path would leave "of any kind"
        untested. It is also this file's one real argon2 hash - paid, as the phone
        file pays its own, so that the account in the way is one a deployment could
        have made.

        The remedy here is "use another address", which is why it is not the same
        refusal as the subject collision above.
        """
        typed({PASSWORD_PROMPT: PASSWORD})
        assert run(db, session, "signup", ADDRESS) == 0
        capsys.readouterr()

        token = verifier.mint(SUBJECT, ADDRESS)
        typed({TOKEN_PROMPT: token})

        assert run(db, session, "signup-google") == 1

        assert f"{ADDRESS} is already registered" in capsys.readouterr().err
        assert accounts_in(db) == 1

    def test_with_no_google_client_id_it_refuses_and_names_the_variable(
        self, db, session, capsys, typed
    ):
        """**Exit 1, carrying the same sentence the API's 503 carries.**

        No verifier is injected and the environment is clear, so ``main`` reads
        ``None`` for the Google configuration, the builder composes the reason from
        ``describe_google_configuration``, and the use case raises it. The sentence
        is composed in the composition root rather than here, which is what makes
        the CLI and the API print one identical line - this is the CLI's half of
        that arrangement.

        The token is still typed, because the prompt is evaluated before the call it
        is an argument to. That is honest rather than ideal, and it is invisible: the
        refusal is a fact about the installation and no value of the token changes
        it, so what was typed is discarded either way.

        Nothing is written. The refusal is raised before the unit is opened, so there
        is no row to find afterwards - which is the difference between a refusal and
        a failure.
        """
        typed({TOKEN_PROMPT: "any-token-at-all"})

        assert run(db, session, "signup-google") == 1

        err = capsys.readouterr().err
        assert "GOOGLE_CLIENT_ID is not set" in err
        assert "no Google client id" in err
        assert accounts_in(db) == 0


class TestTheClientId:
    """The one line the fake verifier cannot see, and why it needs its own class.

    ``google_verifier_for`` returns an injected verifier *before* it looks at the
    settings, which is what makes every other test in this file runnable without a
    client id - and it is also what hides a specific mistake. A command that handed
    the builder ``settings=None`` would behave identically in every test above, and
    on a configured installation it would refuse every sign-in as unconfigured. The
    suite would be green and the deployment would be broken.

    So the assertion here is about the *argument* rather than the outcome, which is
    the only place the difference is observable without reaching Google: the spy
    records what it was called with and then delegates to the real builder with the
    fake verifier injected, so the wiring stays real and the network is never
    touched.
    """

    def test_the_environment_client_id_is_handed_to_the_signup_builder(
        self, db, session, monkeypatch, build_google_verifier, typed
    ):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", TEST_GOOGLE_CLIENT_ID)
        fake = build_google_verifier()
        seen = {}

        def spy(*args, **kwargs):
            seen.update(kwargs)
            return build_sign_up_with_google(*args, verifier=fake, **kwargs)

        monkeypatch.setattr(cli, "build_sign_up_with_google", spy)
        token = fake.mint(SUBJECT, ADDRESS)
        typed({TOKEN_PROMPT: token})

        assert run(db, session, "signup-google") == 0

        assert seen["settings"] == TEST_GOOGLE_SETTINGS
        assert accounts_in(db) == 1

    def test_the_environment_client_id_is_handed_to_the_login_builder(
        self, db, session, monkeypatch, build_google_verifier, typed
    ):
        """The same claim for the other half, because they are two call sites.

        Two commands read the one ``google = google_from_environment()`` above the
        dispatch, and a fix applied to one of them is the shape this test exists to
        catch. Nothing is registered here, so the run is refused for the ordinary
        reason - the subject names no account - and what is asserted is the
        argument that reached the builder.
        """
        monkeypatch.setenv("GOOGLE_CLIENT_ID", TEST_GOOGLE_CLIENT_ID)
        fake = build_google_verifier()
        seen = {}

        def spy(*args, **kwargs):
            seen.update(kwargs)
            return build_log_in_with_google(*args, verifier=fake, **kwargs)

        monkeypatch.setattr(cli, "build_log_in_with_google", spy)
        token = fake.mint(SUBJECT, ADDRESS)
        typed({TOKEN_PROMPT: token})

        assert run(db, session, "login-google") == 1

        assert seen["settings"] == TEST_GOOGLE_SETTINGS


class TestSigningInWithTheToken:
    """``login-google``: the third command that writes the session file.

    **The session file is the subject of half of these.** A Google account has no
    password, so until this command existed it had no way to obtain a session at
    all - which would have made it an account that could hold a wallet it could not
    reach. "A token lands in the file" and "``whoami`` reports the account" are the
    two claims that together mean that gap is closed, and the second is a real round
    trip: ``whoami`` sends the token to the server and is given back the account it
    names.

    **It creates nothing, and that is the decision this half exists to keep.** A
    token whose subject names nobody here is refused rather than registered, which
    is what stops this being the find-or-create ``SignUp`` spent a phase removing -
    and what makes ``signup-google``'s duplicate refusals meaningful, since the
    remedy for an unknown subject is that command rather than a quiet success here.
    """

    def test_it_writes_the_session_file_and_reports_the_address(
        self, db, session, capsys, verifier, typed
    ):
        token = registered(db, session, verifier, typed)
        capsys.readouterr()
        typed({TOKEN_PROMPT: token})

        assert run(db, session, "login-google") == 0

        assert os.path.exists(session)
        out = capsys.readouterr().out
        assert ADDRESS in out
        assert "expires" in out

    def test_whoami_reports_the_account(self, db, session, capsys, verifier, typed):
        """**The round trip, and the proof that the token is one the server accepts.**

        Everything above asserts about a file; this asserts about what the server
        makes of what is in it. The account has an address and no number, so the
        phone line is absent rather than empty - the rendering ``_whoami`` was
        corrected for when ``login-phone`` made a one-identifier account reachable
        with a session. Both absences are checked, because the failure mode is a
        line that *is* printed and prints nothing.
        """
        token = registered(db, session, verifier, typed)
        typed({TOKEN_PROMPT: token})
        run(db, session, "login-google")
        capsys.readouterr()

        assert run(db, session, "whoami") == 0

        out = capsys.readouterr().out
        assert f"email: {ADDRESS}" in out
        assert "phone:" not in out
        assert "None" not in out

    def test_an_unknown_subject_is_refused_in_the_same_words_as_a_wrong_password(
        self, db, session, capsys, verifier, typed
    ):
        """**Two refusals compared rather than described, which is the stronger form.**

        A subject that names no account must be indistinguishable, at the terminal,
        from an address that names no account - the same class and the same sentence
        - and the way to assert that is to run both and compare what came out. Two
        sentences that agree in intent while differing in a word are the leak, and a
        description of them would not notice.

        The password run is against an address this database has never held, so it
        refuses without a password ever being verified as correct, and the cost is
        the dummy-hash comparison ``LogIn`` makes on the path where no credential was
        found.
        """
        token = verifier.mint(SUBJECT, ADDRESS)
        typed({PASSWORD_PROMPT: PASSWORD})
        assert run(db, session, "login", "nobody@example.com") == 1
        wrong = capsys.readouterr().err

        typed({TOKEN_PROMPT: token})
        assert run(db, session, "login-google") == 1
        unknown = capsys.readouterr().err

        assert unknown == wrong
        assert not os.path.exists(session)

    def test_an_unknown_subject_creates_nothing(self, db, session, verifier, typed):
        """The account is not made on the way past, which is the whole decision.

        A create-or-log-in command would have made "the address was free" and "the
        proof held" one answer, and a person who had never registered here would be
        registered by a login - the find-or-create this codebase spent Phase 2a
        removing, arrived at from the other direction.
        """
        token = verifier.mint(SUBJECT, ADDRESS)
        typed({TOKEN_PROMPT: token})

        assert run(db, session, "login-google") == 1

        assert accounts_in(db) == 0

    def test_the_token_is_not_printed(self, db, session, capsys, verifier, typed):
        """The stronger version of the signup's claim, and for a better reason.

        That command only creates an account; this one turns whatever it is handed
        into the session this machine will act as for the next thirty days. A copy
        of the id_token in scrollback is a shorter-lived version of the same thing,
        and neither belongs on a disk a person did not choose to write it to.
        """
        token = registered(db, session, verifier, typed)
        capsys.readouterr()
        typed({TOKEN_PROMPT: token})

        run(db, session, "login-google")

        assert token not in capsys.readouterr().out
        assert verifier.attempts.count(token) == 2
