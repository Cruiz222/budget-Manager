"""The CLI saying who it acts for: read a profile, give one, and be re-tiered.

Two commands, and the second one has a *consequence* rather than an effect - which
is this file's subject. ``set-profile`` writes a row and changes nothing else in
the database; what changes is the tier, which is derived from what the row holds
and stored nowhere. So every test that matters here reads the consequence back out
through ``profile``, and the two commands are tested as the pair they are.

Four claims, and the ones about merging are the reason the file exists:

- **``profile`` reads an account that has given nothing.** Every account that
  predates this feature is in exactly that state, so if the command refused an
  empty profile it would be unreadable to everybody on the day it shipped. It
  prints the tier and the ceilings instead, which is more useful than a refusal.
- **``set-profile`` merges: a field that is not mentioned is kept.** ``Profile``
  replaces the whole record and the API's ``PUT`` sends the whole record, so the
  merge lives in this presentation - and its failure mode is the opposite of the
  failure mode of *not* merging. A CLI that replaced wholesale would drop six
  fields and a tier when somebody typed one flag from memory, and the person would
  find out by being refused a withdrawal.
- **A field can be cleared**, including the birth date, which is the one flag whose
  parser would otherwise swallow an empty value.
- **The tier is not settable from here.** There is no flag for it, and the test
  that says so runs the command with one and expects argparse to refuse the
  argument before any of this code runs.

**Nothing is replaced in this file.** No hasher, no channel, no provider: these two
commands read and write one row, and ``signed_in`` supplies the account and the
token. That is the opposite of its siblings, and it is why this is the shortest
presentation file in the suite - there is no socket anywhere near it.
"""

import pytest

from app.domain.identity.tier import Tier, limits_for
from app.domain.money.currency import Currency
from app.presentation.cli import main
from tests.conftest import TEST_USER_EMAIL, session_path_for, signed_in

#: A second account, for the test that says a profile belongs to the token rather
#: than to the database. ``example.com`` because ``signup`` mints addresses and is
#: bound by the entry rule - see ``TEST_USER_EMAIL``.
OTHER_ADDRESS = "somebody.else@example.com"

#: What a complete profile looks like at a terminal. Spelled once and used by the
#: tests that fill one in, so the four fields a tier is derived from are visible in
#: one place rather than inferred from a command line each test builds for itself.
COMPLETE = (
    "--display-name", "Ada",
    "--first-name", "Ada",
    "--last-name", "Lovelace",
    "--birth-date", "1815-12-10",
    "--phone", "+2348000000000",
    "--country", "NG",
)

#: The line ``_given`` prints for a field nobody has filled in.
NOT_GIVEN = "not given"


def run(db, session, *argv):
    """Invoke the CLI in-process, with this test's database and session file."""
    return main(["--db", db, "--session", session, *argv])


@pytest.fixture
def db(tmp_path):
    """The database this file's CLI runs against, and the file is not incidental.

    Every test here runs the CLI *two or three* times - a save and a read back, or
    two saves - and each invocation opens its own connection. A ``:memory:``
    database would be created and discarded per connection, so the second command
    would be looking at a database the first one never wrote to.
    """
    return str(tmp_path / "cli.db")


@pytest.fixture
def session(db):
    """Where the token goes for this database, by the same rule as ``run``."""
    return session_path_for(db)


@pytest.fixture
def signed(db, session):
    """An account with a valid token on disk, and its address.

    A wrapper around the suite's ``signed_in`` whose only job is to carry the two
    arguments every call site here would otherwise repeat, and the returned
    address is what the second-account test needs - it signs in twice and has to
    say as whom.
    """

    def _signed(address=TEST_USER_EMAIL):
        signed_in(db, email=address)
        return address

    return _signed


def ceiling_line(tier, currency):
    """The limits line ``_print_standing`` prints for one currency at one tier.

    Built from ``limits_for`` rather than typed out, and that is the assertion
    rather than a convenience: a test that hard-coded ``50000.00`` would pass on
    the day the table was edited to publish something else, which is precisely the
    change this command exists to report. The three separators and the two-space
    indent are the format's, so a formatting change fails here - which is intended,
    because a person reads these lines and a misaligned column is a real defect.
    """
    limits = limits_for(tier, currency)
    return (
        f"  {currency.value}  "
        f"per transaction {limits.per_transaction}  "
        f"daily outflow {limits.daily_outflow}  "
        f"max balance {limits.max_balance}"
    )


class TestReadingAProfile:
    def test_an_account_that_has_given_nothing_still_reads(
        self, db, session, signed, capsys
    ):
        """The state every existing account is in, and the command has to survive it."""
        signed()

        assert run(db, session, "profile") == 0

        out = capsys.readouterr().out
        assert f"tier: {Tier.UNVERIFIED.value}" in out
        assert "profile: none given yet" in out

    def test_it_says_which_account_is_reading(self, db, session, signed, capsys):
        """``user_id`` is printed even with no profile, which is why the standing
        carries one - there would otherwise be no object to read the owner off."""
        signed()

        run(db, session, "profile")

        assert "user_id:" in capsys.readouterr().out

    def test_the_limits_printed_are_the_ones_the_table_publishes(
        self, db, session, signed, capsys
    ):
        """All five currencies, at the tier this account is actually at.

        The output *is* ``LIMITS`` rendered, so this is the test that would catch a
        commands that printed the identified ceilings to an unverified account -
        the one mistake here that has money attached to it.
        """
        signed()

        run(db, session, "profile")

        out = capsys.readouterr().out
        for currency in Currency:
            assert ceiling_line(Tier.UNVERIFIED, currency) in out

    def test_it_names_what_is_still_owed_rather_than_saying_incomplete(
        self, db, session, signed, capsys
    ):
        """A status is readable above it; what is needed is the list of fields.

        Asserted as the whole sentence, because the sentence is the feature - a
        person who does not know which field to add next has been told nothing by
        the word "incomplete".
        """
        signed()

        run(db, session, "profile")

        assert (
            "note: the tier becomes identified once a legal first and last name, "
            "a date of birth, a phone number and a country are all given"
        ) in capsys.readouterr().out

    def test_it_declines_without_a_token(self, db, session, capsys):
        """Same routing as every other command that acts as somebody."""
        assert run(db, session, "profile") == 1

        assert "not signed in" in capsys.readouterr().err


class TestGivingAProfile:
    def test_a_display_name_alone_is_enough_for_a_first_save(
        self, db, session, signed, capsys
    ):
        """The one required field. Everything a tier is derived from is optional,
        so somebody can be recorded without being tiered."""
        signed()

        assert run(db, session, "set-profile", "--display-name", "Ada") == 0

        out = capsys.readouterr().out
        assert "saved profile for" in out
        assert "display name: Ada" in out
        assert f"tier: {Tier.UNVERIFIED.value}" in out

    def test_a_first_save_with_no_display_name_is_refused(
        self, db, session, signed, capsys
    ):
        """argparse cannot express "required only the first time", so the handler
        does - and it has to name the flag, because the message is the fix."""
        signed()

        assert run(db, session, "set-profile", "--phone", "+2348000000000") == 1

        err = capsys.readouterr().err
        assert "error:" in err
        assert "--display-name" in err

    def test_the_four_fields_of_an_identity_move_the_tier(
        self, db, session, signed, capsys
    ):
        """The consequence, and the whole point of the feature: nothing sets a tier,
        so completing the fields is what moves one."""
        signed()

        assert run(db, session, "set-profile", *COMPLETE) == 0

        assert f"tier: {Tier.IDENTIFIED.value}" in capsys.readouterr().out

    def test_the_printed_limits_follow_the_tier(self, db, session, signed, capsys):
        """The same command, the same table, higher ceilings - and the note gone."""
        signed()
        run(db, session, "set-profile", *COMPLETE)
        capsys.readouterr()

        run(db, session, "profile")

        out = capsys.readouterr().out
        for currency in Currency:
            assert ceiling_line(Tier.IDENTIFIED, currency) in out
            # And the lower row is *gone*, not merely printed beside it: a command
            # that listed both would leave a person unable to tell which applies.
            assert ceiling_line(Tier.UNVERIFIED, currency) not in out
        assert "note: the tier becomes" not in out

    def test_the_fields_read_back_as_they_were_given(
        self, db, session, signed, capsys
    ):
        """The round trip, including the two normalisations the aggregate makes:
        the country comes back uppercased and the birth date comes back ISO."""
        signed()
        run(
            db,
            session,
            "set-profile",
            "--display-name",
            "Ada",
            "--first-name",
            "Ada",
            "--last-name",
            "Lovelace",
            "--birth-date",
            "1815-12-10",
            "--phone",
            "+2348000000000",
            # Lowercase on purpose: the aggregate stores one spelling of a country.
            "--country",
            "ng",
        )
        capsys.readouterr()

        run(db, session, "profile")

        out = capsys.readouterr().out
        assert "first name: Ada" in out
        assert "last name: Lovelace" in out
        assert "date of birth: 1815-12-10" in out
        assert "phone: +2348000000000" in out
        assert "country: NG" in out

    def test_a_partial_profile_says_which_fields_are_still_missing(
        self, db, session, signed, capsys
    ):
        """A first name and a date of birth is a real state to be in the middle of."""
        signed()

        run(db, session, "set-profile", "--display-name", "Ada",
            "--first-name", "Ada", "--birth-date", "1815-12-10")

        out = capsys.readouterr().out
        assert "first name: Ada" in out
        assert "last name: not given" in out
        assert "phone: not given" in out
        assert f"tier: {Tier.UNVERIFIED.value}" in out

    def test_a_country_that_is_not_a_code_is_refused_in_the_domains_words(
        self, db, session, signed, capsys
    ):
        """The aggregate's refusal, not a check of this command's - which is why
        the message arrives through the same ``error:`` line as everything else."""
        signed()

        assert run(db, session, "set-profile", "--display-name", "Ada",
                   "--country", "Nigeria") == 1

        err = capsys.readouterr().err
        assert "error:" in err
        assert "country" in err

    def test_a_birth_date_typed_wrongly_is_a_usage_error(
        self, db, session, signed, capsys
    ):
        """Exit 2 from argparse rather than 1 from the domain, because the value
        never became a date - the same refusal ``--from`` and ``--until`` give."""
        signed()

        with pytest.raises(SystemExit) as excinfo:
            run(db, session, "set-profile", "--display-name", "Ada",
                "--birth-date", "10/12/1815")
        assert excinfo.value.code == 2

        assert "invalid date" in capsys.readouterr().err


class TestChangingOneField:
    def test_a_field_that_is_not_mentioned_is_kept(
        self, db, session, signed, capsys
    ):
        """The merge, and the failure it prevents.

        A whole-replacement version of this command would clear five fields and
        drop the tier, and the person would learn that by being refused a
        withdrawal. This asserts all four of the tier's fields survive a save that
        mentioned none of them.
        """
        signed()
        run(db, session, "set-profile", *COMPLETE)
        capsys.readouterr()

        assert run(db, session, "set-profile", "--address", "12 Analytical Way") == 0

        out = capsys.readouterr().out
        assert "address: 12 Analytical Way" in out
        assert "first name: Ada" in out
        assert "last name: Lovelace" in out
        assert "date of birth: 1815-12-10" in out
        assert "phone: +2348000000000" in out
        assert "country: NG" in out
        assert f"tier: {Tier.IDENTIFIED.value}" in out

    def test_a_field_is_cleared_by_passing_nothing(
        self, db, session, signed, capsys
    ):
        """The third state a flag can be in.

        Clearing the phone number takes the account back to the lower tier, and
        that is the honest answer rather than a bug: the tier is derived from the
        fields, so a field that is no longer there cannot support it.
        """
        signed()
        run(db, session, "set-profile", *COMPLETE)
        capsys.readouterr()

        run(db, session, "set-profile", "--phone", "")

        out = capsys.readouterr().out
        assert "phone: not given" in out
        assert f"tier: {Tier.UNVERIFIED.value}" in out
        # And nothing else moved with it.
        assert "first name: Ada" in out
        assert "country: NG" in out

    def test_a_birth_date_is_cleared_the_same_way(self, db, session, signed, capsys):
        """The flag whose parser would otherwise eat the empty string.

        ``--birth-date ""`` is the one value ``_date`` cannot read, so without
        ``_optional_date`` this would exit 2 and a birth date typed in wrong would
        have no way to be removed - while every text field beside it could be.
        """
        signed()
        run(db, session, "set-profile", *COMPLETE)
        capsys.readouterr()

        assert run(db, session, "set-profile", "--birth-date", "") == 0

        out = capsys.readouterr().out
        assert "date of birth: not given" in out
        assert f"tier: {Tier.UNVERIFIED.value}" in out

    def test_a_country_is_cleared_the_same_way(self, db, session, signed, capsys):
        """A blank is absent for every optional field, not only the free text ones."""
        signed()
        run(db, session, "set-profile", *COMPLETE)
        capsys.readouterr()

        run(db, session, "set-profile", "--country", "  ")

        assert "country: not given" in capsys.readouterr().out

    def test_a_refused_edit_leaves_the_stored_profile_alone(
        self, db, session, signed, capsys
    ):
        """A refusal has to be a refusal of the *whole* save.

        The aggregate builds the replacement before it assigns anything, so a
        country this system will not accept cannot half-apply an edit that also
        carried a new phone number - which is the failure ``Profile.revise`` is
        arranged to make impossible, asserted here through the command a person
        would actually type.
        """
        signed()
        run(db, session, "set-profile", *COMPLETE)
        capsys.readouterr()

        assert run(db, session, "set-profile", "--phone", "+2348000000001",
                   "--country", "Nigeria") == 1
        capsys.readouterr()

        run(db, session, "profile")

        out = capsys.readouterr().out
        assert "phone: +2348000000000" in out
        assert "country: NG" in out


class TestAProfileIsNotASecretAndATierIsNotARequest:
    def test_a_profile_belongs_to_the_account_the_token_names(
        self, db, session, signed, capsys
    ):
        """Two accounts, one database, and neither can read the other's details.

        The token is the only input either command has, so this is what the scoping
        reduces to at a terminal: signing in as somebody else reads somebody else's
        row, and there is no flag anywhere that could aim either command at a third
        account.
        """
        signed(OTHER_ADDRESS)
        run(db, session, "set-profile", *COMPLETE)
        capsys.readouterr()

        signed()  # a different account entirely

        run(db, session, "profile")

        out = capsys.readouterr().out
        assert "profile: none given yet" in out
        assert "Ada" not in out
        assert f"tier: {Tier.UNVERIFIED.value}" in out

    def test_there_is_no_flag_that_sets_a_tier(self, db, session, signed, capsys):
        """The boundary, asserted the way ``test_boundary.py`` asserts one.

        A tier is derived and stored nowhere, so there is nothing for this flag to
        write. argparse refuses the argument before any of this feature's code
        runs, and that is the strongest form of "no client-supplied tier": the
        command line has no such input, and neither does the HTTP body.
        """
        signed()

        with pytest.raises(SystemExit) as excinfo:
            run(db, session, "set-profile", "--display-name", "Ada",
                "--tier", "identified")
        assert excinfo.value.code == 2

        assert "unrecognized arguments" in capsys.readouterr().err
