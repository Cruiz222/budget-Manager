"""The second-level confirmation, at the terminal.

``test_cli.py`` covers what the money does and runs every one of those commands
with ``--yes``; this file covers the question itself. The split is deliberate and
it is the same split the feature makes: what a person is shown, and what happens
when they answer, are two subjects, and a file that mixed them would make both
harder to read.

**The seam is ``builtins.input``, not anything in ``cli.py``.** The prompt is a
conversation with a *terminal*, so the thing to replace is the terminal - exactly
as ``typed_password`` (tests/conftest.py) replaces ``getpass`` for the password
prompt, and for the same reason: a test that reached into the module to stub its
own helper would be testing the stub.

Three answers matter and each has a test:

    ``y``        the money moves, and the command reports it
    ``n``        nothing moves, the command exits 0, and the request is left
    no answer    the same, because an unattended run is not agreement

The last one is the one worth reading. ``EOFError`` and an empty line are both
refusals, and ``--yes`` is the only way past a prompt nobody is there to answer -
which is what makes the flag a statement about the room rather than a way around
the rule.
"""

import re

import pytest

from app.presentation.cli import main
from tests.conftest import session_path_for, signed_in, starter_wallet_id


@pytest.fixture(autouse=True)
def signed_in_cli(tmp_path):
    """The same precondition every CLI test needs; see ``test_cli.py``."""
    return signed_in(str(tmp_path / "cli.db"))


def run(db_path, *argv):
    return main(["--db", db_path, "--session", session_path_for(db_path), *argv])


def opened_wallet_id(db_path, capsys):
    """Return the NGN wallet that signup opened for the test account."""
    capsys.readouterr()
    return starter_wallet_id(db_path)


def funded_wallet(db_path, capsys, amount="10000"):
    wallet_id = opened_wallet_id(db_path, capsys)
    assert run(db_path, "deposit", wallet_id, amount) == 0
    capsys.readouterr()
    return wallet_id


def answering(monkeypatch, *answers):
    """Feed the prompt a sequence of answers, one per call.

    A list rather than a single value because a command must not be able to ask
    twice - if it did, the second ``input`` would raise ``StopIteration`` and the
    test would fail loudly rather than quietly reusing the first answer. That is
    the assertion hiding inside the helper, and it is why it is written this way
    rather than as a constant.

    **The prompt is written back out, because ``input`` does that itself.** The
    real ``input(prompt)`` prints its prompt to stdout and then reads a line, so
    a double that swallowed the argument would be a double that behaves
    differently from the thing it replaces - and the difference lands squarely on
    the tests that are about what the person is *shown*. ``confirm? [y/N]`` is
    the whole of what the prompt asks; a fake that kept it to itself would make
    that string unreachable from any test in this file, which is how this line
    came to be here. Found by running the suite; the module docstring's promise
    that the prompt is asserted rather than assumed is what caught it.
    """
    remaining = list(answers)

    def _input(prompt=""):
        print(prompt, end="")
        if not remaining:
            raise AssertionError("the CLI asked more times than the test allowed")
        return remaining.pop(0)

    monkeypatch.setattr("builtins.input", _input)


def balance_of(db_path, capsys, wallet_id) -> str:
    assert run(db_path, "balance", wallet_id) == 0
    out = capsys.readouterr().out
    match = re.search(r"available: (\S+ \S+)", out)
    assert match, out
    return match.group(1)


class TestThePreview:
    def test_it_names_the_amount_and_both_balances(self, tmp_path, capsys, monkeypatch):
        """The arithmetic a person is being asked to check.

        ``available 10000.00 NGN -> 7000.00 NGN`` is the whole value of the
        prompt: it is the number they cannot compute in their head while looking
        at a terminal, and the one that says whether they typed one zero too many.
        """
        db = str(tmp_path / "cli.db")
        wallet_id = funded_wallet(db, capsys)
        answering(monkeypatch, "n")

        assert run(db, "withdraw", wallet_id, "3000") == 0

        out = capsys.readouterr().out
        assert f"about to withdraw 3000.00 NGN from wallet {wallet_id}" in out
        assert "available 10000.00 NGN -> 7000.00 NGN" in out
        assert "this moves money out of your wallet for good" in out
        assert "confirm? [y/N]" in out

    def test_an_overdraft_says_so_in_words(self, tmp_path, capsys, monkeypatch):
        """A negative balance is not a number a wallet can hold.

        Printing ``-> -3000.00 NGN`` would be showing a state that cannot exist,
        and a reader would reasonably conclude the system was about to allow it.
        The refusal is still the domain's - this line is a courtesy, not a check -
        but a courtesy that lied about arithmetic would be worse than silence.
        """
        db = str(tmp_path / "cli.db")
        wallet_id = funded_wallet(db, capsys, "1000")
        answering(monkeypatch, "n")

        assert run(db, "withdraw", wallet_id, "4000") == 0

        out = capsys.readouterr().out
        assert "more than the available balance of 1000.00 NGN" in out
        assert "it will be refused" in out
        assert "-3000.00" not in out

    def test_a_payout_preview_names_who_is_being_paid(
        self, tmp_path, capsys, monkeypatch
    ):
        """The person, not just the account number.

        A payout's whole risk is a wrong recipient, and an account number is the
        thing a human is least able to check at a glance. The destination is read
        off the *record* here as everywhere else in this file - see
        ``cli._payout``.
        """
        db = str(tmp_path / "cli.db")
        wallet_id = funded_wallet(db, capsys)
        assert run(db, "fund", "open", "--wallet", wallet_id, "--name", "Rent",
                   "--kind", "personal") == 0
        assert run(db, "fund", "lock", wallet_id, "Rent", "6000") == 0
        capsys.readouterr()
        answering(monkeypatch, "n")

        assert run(
            db, "payout", wallet_id, "2000",
            "--account", "0123456789", "--bank-code", "058",
            "--name", "Chinedu Okafor",
        ) == 0

        out = capsys.readouterr().out
        assert (
            "about to pay 2000.00 NGN to Chinedu Okafor "
            "(bank_account:0123456789)" in out
        )
        assert "drawn from the matured pots, oldest first" in out
        assert "confirm? [y/N]" in out

    def test_a_named_pot_is_named_in_the_preview(self, tmp_path, capsys, monkeypatch):
        """Which pot, because "drawn from the matured pots" would be false here.

        A named draw and a pooled draw are two different operations over the same
        wallet, and a preview that described both the same way would hide the one
        thing the person asked for by name.
        """
        db = str(tmp_path / "cli.db")
        wallet_id = funded_wallet(db, capsys)
        assert run(db, "fund", "open", "--wallet", wallet_id, "--name", "Rent",
                   "--kind", "personal") == 0
        assert run(db, "fund", "lock", wallet_id, "Rent", "6000") == 0
        capsys.readouterr()
        answering(monkeypatch, "n")

        assert run(
            db, "payout", wallet_id, "2000", "--fund", "Rent",
            "--account", "0123456789", "--bank-code", "058",
            "--name", "Chinedu Okafor",
        ) == 0

        out = capsys.readouterr().out
        assert "drawn from the pot 'Rent'" in out


class TestAnswering:
    def test_yes_moves_the_money(self, tmp_path, capsys, monkeypatch):
        """The only path to a withdrawal, and it goes through the answer.

        The result line is the pre-existing one, unchanged - what a person sees
        after saying yes is exactly what they saw before this feature existed,
        because the feature is a question rather than a new report.
        """
        db = str(tmp_path / "cli.db")
        wallet_id = funded_wallet(db, capsys)
        answering(monkeypatch, "y")

        assert run(db, "withdraw", wallet_id, "3000") == 0

        out = capsys.readouterr().out
        assert "withdrew 3000.00 NGN | available 7000.00 NGN" in out
        assert balance_of(db, capsys, wallet_id) == "7000.00 NGN"

    def test_the_answer_is_read_case_insensitively(self, tmp_path, capsys, monkeypatch):
        """``Y`` is a yes. Capitals happen, and a lock key is a real thing.

        Worth pinning because the rule is asymmetric: everything that is not a
        clear yes is a no, so a missed capital would be a refusal - safe, but
        baffling to somebody who watched themselves type the letter.
        """
        db = str(tmp_path / "cli.db")
        wallet_id = funded_wallet(db, capsys)
        answering(monkeypatch, "Y")

        assert run(db, "withdraw", wallet_id, "3000") == 0

        assert "withdrew 3000.00 NGN" in capsys.readouterr().out

    def test_no_moves_nothing_and_is_not_an_error(self, tmp_path, capsys, monkeypatch):
        """Exit 0, because nothing failed - a person declined.

        A non-zero exit here would tell a script that something went wrong when
        the system did exactly what it was told, and a script that treated it as a
        failure would retry a command the user had just refused.
        """
        db = str(tmp_path / "cli.db")
        wallet_id = funded_wallet(db, capsys)
        answering(monkeypatch, "n")

        assert run(db, "withdraw", wallet_id, "3000") == 0

        out = capsys.readouterr().out
        assert "declined - nothing was moved" in out
        assert "withdrew" not in out
        assert balance_of(db, capsys, wallet_id) == "10000.00 NGN"

    def test_anything_that_is_not_yes_is_a_no(self, tmp_path, capsys, monkeypatch):
        """A blank line, a word, a shrug - all refusals.

        The cost of the two mistakes is not symmetric. A mistyped ``y`` costs a
        second run of the command; a mistyped ``n`` read as agreement costs money
        that cannot be called back. So the rule is "is this a clear yes", not "is
        this anything other than a no".
        """
        db = str(tmp_path / "cli.db")
        wallet_id = funded_wallet(db, capsys)
        answering(monkeypatch, "")

        assert run(db, "withdraw", wallet_id, "3000") == 0

        assert "declined - nothing was moved" in capsys.readouterr().out
        assert balance_of(db, capsys, wallet_id) == "10000.00 NGN"

    def test_an_unanswered_prompt_moves_nothing(self, tmp_path, capsys, monkeypatch):
        """``EOFError``: an unattended run with nothing on stdin.

        This is the case the feature has to get right for a cron job or a piped
        invocation, and the honest answer is a refusal rather than a crash -
        defaulting the other way would make the prompt a decoration on any machine
        whose stdin happens to be closed.
        """
        db = str(tmp_path / "cli.db")
        wallet_id = funded_wallet(db, capsys)

        def _eof(prompt=""):
            raise EOFError

        monkeypatch.setattr("builtins.input", _eof)

        assert run(db, "withdraw", wallet_id, "3000") == 0

        out = capsys.readouterr().out
        assert "no answer given - nothing was moved" in out
        assert balance_of(db, capsys, wallet_id) == "10000.00 NGN"

    def test_a_closed_stdin_moves_nothing_either(self, tmp_path, capsys, monkeypatch):
        """``OSError``: the same situation arriving by a different route.

        A closed file descriptor raises ``OSError`` where an exhausted pipe
        raises ``EOFError``, and from this function's point of view the two are
        one fact - nobody is there to ask. This is the shape pytest's own
        captured stdin takes, which is how the gap was found: the first CLI test
        to reach this prompt without ``--yes`` failed with a traceback instead of
        with the refusal ``_confirmed`` promises.
        """
        db = str(tmp_path / "cli.db")
        wallet_id = funded_wallet(db, capsys)

        def _closed(prompt=""):
            raise OSError("stdin is closed")

        monkeypatch.setattr("builtins.input", _closed)

        assert run(db, "withdraw", wallet_id, "3000") == 0

        assert "no answer given - nothing was moved" in capsys.readouterr().out
        assert balance_of(db, capsys, wallet_id) == "10000.00 NGN"

    def test_a_refusal_leaves_the_request_to_expire(
        self, tmp_path, capsys, monkeypatch
    ):
        """And a second command makes a *second* request rather than answering
        the first.

        Worth stating because it is the one thing a person might expect the
        opposite of. The declined request is left in the database - it is what the
        ledger of requests is for - but nothing in the CLI reaches back to it, and
        the next ``withdraw`` records a new one. Two requests, one movement.
        """
        db = str(tmp_path / "cli.db")
        wallet_id = funded_wallet(db, capsys)
        answering(monkeypatch, "n")
        assert run(db, "withdraw", wallet_id, "3000") == 0
        capsys.readouterr()

        answering(monkeypatch, "y")
        assert run(db, "withdraw", wallet_id, "3000") == 0

        assert "withdrew 3000.00 NGN" in capsys.readouterr().out
        assert balance_of(db, capsys, wallet_id) == "7000.00 NGN"


class TestAssumeYes:
    def test_yes_skips_the_question_entirely(self, tmp_path, capsys):
        """No prompt is printed, and no answer is read.

        ``input`` is deliberately *not* patched here, so if ``--yes`` stopped
        short-circuiting, this test would raise pytest's stdin error rather than
        hang or pass by accident - which is the only way to assert the absence of
        a question convincingly.
        """
        db = str(tmp_path / "cli.db")
        wallet_id = funded_wallet(db, capsys)

        assert run(db, "withdraw", wallet_id, "3000", "--yes") == 0

        out = capsys.readouterr().out
        assert "confirm?" not in out
        assert "withdrew 3000.00 NGN | available 7000.00 NGN" in out

    def test_the_preview_is_still_printed(self, tmp_path, capsys):
        """``--yes`` answers the question; it does not suppress the report.

        The two are separable and this is the separation: a script that passes
        ``--yes`` still gets the line saying what was about to happen, which is
        the only record a person reading a log has of why 3000 NGN left.
        """
        db = str(tmp_path / "cli.db")
        wallet_id = funded_wallet(db, capsys)

        assert run(db, "withdraw", wallet_id, "3000", "--yes") == 0

        out = capsys.readouterr().out
        assert "about to withdraw 3000.00 NGN" in out
        assert "available 10000.00 NGN -> 7000.00 NGN" in out

    def test_a_payout_takes_the_flag_too(self, tmp_path, capsys):
        db = str(tmp_path / "cli.db")
        wallet_id = funded_wallet(db, capsys)
        assert run(db, "fund", "open", "--wallet", wallet_id, "--name", "Rent",
                   "--kind", "personal") == 0
        assert run(db, "fund", "lock", wallet_id, "Rent", "6000") == 0
        capsys.readouterr()

        assert run(
            db, "payout", wallet_id, "2000", "--fund", "Rent",
            "--account", "0123456789", "--bank-code", "058",
            "--name", "Chinedu Okafor", "--yes",
        ) == 0

        out = capsys.readouterr().out
        assert "confirm?" not in out
        assert "paid 2000.00 NGN to Chinedu Okafor" in out

    def test_the_flag_changes_nothing_about_the_record(
        self, tmp_path, capsys, monkeypatch
    ):
        """``--yes`` is a statement about the room, not about the rules.

        The same request is recorded and the same request is spent either way;
        the flag only decides whether the CLI looks for a person before answering
        it. Asserted by comparing what the two paths leave behind: one movement,
        one PENDING row, the same balance - because if the flag skipped the
        confirmation, the *server* would still need one and this is what would
        differ.
        """
        db = str(tmp_path / "cli.db")
        wallet_id = funded_wallet(db, capsys)

        answering(monkeypatch, "y")
        assert run(db, "withdraw", wallet_id, "1000") == 0
        capsys.readouterr()
        assert run(db, "withdraw", wallet_id, "1000", "--yes") == 0
        capsys.readouterr()

        assert run(db, "history", wallet_id) == 0
        out = capsys.readouterr().out
        assert out.count("withdrawal") == 2
        assert "pending" in out
        assert balance_of(db, capsys, wallet_id) == "8000.00 NGN"

    def test_it_is_not_on_the_commands_that_are_not_confirmed(
        self, tmp_path, capsys
    ):
        """``deposit`` takes no ``--yes``, and argparse is what says so.

        A flag that existed on every command would suggest every command could
        need one, and the absence is the same statement the plan makes about which
        operations are confirmed: money leaving, and nothing else. A deposit is
        money arriving on a command the user typed themselves, and it is
        reversible in the only sense that matters here - nothing has left.
        """
        db = str(tmp_path / "cli.db")
        wallet_id = opened_wallet_id(db, capsys)
        capsys.readouterr()

        with pytest.raises(SystemExit) as excinfo:
            run(db, "deposit", wallet_id, "500", "--yes")

        assert excinfo.value.code == 2

    def test_and_not_on_the_pot_commands(self, tmp_path, capsys):
        """A lock, a release and an extend stay one step.

        All three are reversible and stay inside the wallet, so a mis-tap is
        undone by typing the opposite command. A prompt in front of them would
        only teach people to answer prompts without reading them - which is the
        cost that would land on the three commands that do need one.
        """
        db = str(tmp_path / "cli.db")
        wallet_id = opened_wallet_id(db, capsys)
        assert run(db, "fund", "open", "--wallet", wallet_id, "--name", "Rent",
                   "--kind", "personal") == 0
        capsys.readouterr()

        with pytest.raises(SystemExit) as excinfo:
            run(db, "fund", "lock", wallet_id, "Rent", "100", "--yes")

        assert excinfo.value.code == 2
