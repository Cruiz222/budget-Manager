"""One number, one spelling - and the value that is not a number at all.

The fold under test is four lines long and the interesting thing about it is not
the lines. It is that **a ``UNIQUE`` column means nothing without it**: a person
who registers with ``08012345678`` and later logs in as ``+2348012345678`` has
two accounts, and every database in the world is content with that. ``fold_email``
carries the same argument for case, and this file exists because the phone version
of it is *harder* - an address is written one way by every machine that touches
it, and a number is written three ways by the same person on three occasions.

So the fold is pinned in both directions, following ``test_email_address.py``:
the three spellings collapse to one value, **and** the values that must not be
folded together are asserted not to be. The second list is the one that matters
more. A fold that is too eager is worse than none at all - it silently merges two
different people's numbers into one account, which no later check can detect,
because the row that would have disagreed is gone.

The shape rule is pinned beside it, one test per rule it refuses, because a
refusal nobody asserted is a refusal that can be dropped in a refactor without
anybody noticing the door had widened.
"""

import pytest

from app.domain.identity.exception import IdentityError, InvalidUserPhoneError
from app.domain.identity.phoneNumber import (
    DEFAULT_COUNTRY_CODE,
    MAX_DIGITS,
    MIN_DIGITS,
    checked_phone,
    fold_phone,
)

#: The same number in the three forms a Nigerian handset will produce it in.
#: Written as a tuple of tuples - spellings in, one value out - so the collapse is
#: asserted as a *relation* rather than against a literal that could be edited to
#: match whatever the function happens to return.
SAME_NUMBER = (
    ("08012345678", "2348012345678"),
    ("+2348012345678", "2348012345678"),
    ("2348012345678", "2348012345678"),
    ("0801 234 5678", "2348012345678"),
    ("0801-234-5678", "2348012345678"),
    ("(0801) 234.5678", "2348012345678"),
    ("+234 801 234 5678", "2348012345678"),
    ("8012345678", "2348012345678"),
    ("  08012345678  ", "2348012345678"),
)

#: Numbers that must **not** fold together, each with the reading it pins.
#:
#: The ``+`` cases are the sharp ones. A leading ``+`` is a promise that the
#: country code is already present, so taking it at face value is what keeps a
#: foreign number foreign - fold ``+15417543010`` as though it were national and
#: it becomes a Nigerian number that belongs to nobody, silently, at the login
#: form. The ``+2340801...`` case is the contradiction, and the ``+`` wins
#: because it is the stronger statement of the two.
DISTINCT = (
    ("+15417543010", "15417543010"),  # already international: not reinterpreted
    ("+442071234567", "442071234567"),  # London, likewise
    ("+23408012345678", "23408012345678"),  # a contradiction; the ``+`` wins
    ("2342348012345678", "2342348012345678"),  # never prepends a second time
)

#: The subset of ``DISTINCT`` that said where it was from with a ``+``, which is
#: the only reading under which "the country code was not applied" is a claim that
#: can be asserted directly. The fourth entry above is the opposite case - a
#: number that starts with the country code because it always did - and folding
#: the two into one assertion would make it fail for a reason that is not a bug.
FOREIGN = (
    ("+15417543010", "15417543010"),
    ("+442071234567", "442071234567"),
    ("+23408012345678", "23408012345678"),
)


@pytest.mark.parametrize("written, expected", SAME_NUMBER)
def test_one_number_has_one_spelling(written, expected):
    assert fold_phone(written) == expected


@pytest.mark.parametrize("written, expected", DISTINCT)
def test_a_number_that_says_where_it_is_from_is_not_reinterpreted(written, expected):
    assert fold_phone(written) == expected


@pytest.mark.parametrize("written, expected", FOREIGN)
def test_a_foreign_number_is_never_read_as_a_national_one(written, expected):
    """The refusal that matters most, stated as its own claim.

    ``DISTINCT`` above asserts the value; this asserts the *consequence*, which is
    that the default country code did not get applied to something that had
    already named its own. The two are the same assertion today and are separated
    on purpose: if a later refactor makes the fold prepend unconditionally, the
    ``234234...`` case above would still pass for a number that happened to start
    with ``234``, and this one would not.
    """
    assert not fold_phone(written).startswith(DEFAULT_COUNTRY_CODE + DEFAULT_COUNTRY_CODE)


@pytest.mark.parametrize("written", ("", "   ", "()", "+", "0", "-", ".."))
def test_something_that_is_not_a_number_folds_to_nothing(written):
    """Not to a number, and specifically not to the country code on its own.

    This is the difference between ``checked_phone`` refusing a blank *as a blank*
    and refusing it as a three-digit number that is too short. Prepending ``234``
    to the empty string would produce ``"234"`` - a value that looks like a number
    this system could hold, and is nobody's.
    """
    assert fold_phone(written) == ""


@pytest.mark.parametrize("written, expected", SAME_NUMBER)
def test_the_checked_value_is_the_folded_one(written, expected):
    """A caller cannot end up holding a checked-but-unfolded number."""
    assert checked_phone(written) == expected


class TestWhatTheShapeRuleRefuses:
    """One test per rule, so each refusal is a claim somebody made."""

    @pytest.mark.parametrize("written", (None, 2348012345678, b"08012345678", 1.5))
    def test_a_value_that_is_not_a_string(self, written):
        with pytest.raises(InvalidUserPhoneError):
            checked_phone(written)

    @pytest.mark.parametrize("written", ("", "   ", "()", "+", "0"))
    def test_a_value_with_no_number_in_it(self, written):
        with pytest.raises(InvalidUserPhoneError, match="empty"):
            checked_phone(written)

    @pytest.mark.parametrize(
        "written",
        (
            "not-a-number",
            "0801abc5678",
            "0801 234 5678 ext 12",
            # ``str.isdigit`` answers True for both of these, which is why the
            # character check is a membership test on a literal instead. Stored,
            # they are numbers that are not what anybody typed.
            "0801²345678",
            "٠٨٠١٢٣٤٥٦٧٨",
        ),
    )
    def test_a_value_with_something_other_than_digits_in_it(self, written):
        with pytest.raises(InvalidUserPhoneError, match="digits"):
            checked_phone(written)

    def test_a_number_shorter_than_any_plan_assigns(self):
        """``234`` alone: the country code with nothing after it.

        The floor exists to catch truncation, not to hold a policy about which
        national plans are real - so it refuses very little, and this is the case
        it is for.
        """
        with pytest.raises(InvalidUserPhoneError, match="between"):
            checked_phone("234")

    def test_a_number_longer_than_an_international_one_can_be(self):
        """Sixteen digits, and E.164 allows fifteen - so this cannot be a number.

        Already country-coded, so the length is the only thing wrong with it and
        the refusal is unambiguously the ceiling rather than the fold.
        """
        assert len("2341234567890123") == MAX_DIGITS + 1

        with pytest.raises(InvalidUserPhoneError, match="between"):
            checked_phone("2341234567890123")

    def test_the_shortest_number_that_is_allowed(self):
        """The floor is inclusive, asserted rather than assumed.

        A bound nobody tested from the inside is a bound that can be off by one in
        the direction of refusing a real number, which is the failure mode that
        costs somebody an account.
        """
        shortest = "234" + "1" * (MIN_DIGITS - len(DEFAULT_COUNTRY_CODE))

        assert len(shortest) == MIN_DIGITS
        assert checked_phone(shortest) == shortest

    def test_the_longest_number_that_is_allowed(self):
        longest = "234" + "1" * (MAX_DIGITS - len(DEFAULT_COUNTRY_CODE))

        assert len(longest) == MAX_DIGITS
        assert checked_phone(longest) == longest


def test_every_refusal_is_an_identity_error():
    """One base class, so a caller can catch the family rather than each member.

    ``InvalidProfilePhoneError`` carries the same parent for the same reason, and
    the two are deliberately *different* classes - this one guards an identifier,
    the other a free-text KYC field nothing is ever sent to. See ``exception``.
    """
    with pytest.raises(IdentityError):
        checked_phone("not-a-number")
