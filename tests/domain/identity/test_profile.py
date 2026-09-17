"""``Profile``: seven optional facts about a person, and the one that is required.

The interesting thing about this aggregate is not that it validates fields - it
is *which* way round it validates them, because ``Profile`` is the one file in
this codebase where a rule that holds everywhere else is deliberately inverted.

**Every other aggregate refuses a bare ``date``.** ``User``, ``Session``, ``Fund``
and ``PlanRun`` each check ``isinstance(x, datetime)`` and each has a test
asserting that a ``date`` is refused, because a moment stamped with only a day
has lost its time. ``date_of_birth`` is the opposite: it is a calendar fact with
no moment attached, nobody knows what time of day they were born, and storing a
midnight would invent a precision that does not exist. So ``Profile`` checks
``date`` - and must *also* refuse ``datetime``, even though ``datetime``
satisfies ``isinstance(x, date)``, because accepting it would store exactly the
invented midnight the rule exists to avoid.

That pair is why this file tests both directions. A test that only asserted the
acceptance would pass for a check that had been written the ordinary way round,
and the ordinary way round is the bug here.

**The two things this file does not test, and why they are elsewhere.**
``is_complete`` is tested here as a property, but the *consequences* of
completeness - a tier, a limit - are ``test_tier.py``'s. And the check that a
birth date is in the past is deliberately absent from the aggregate entirely: it
needs a clock, an aggregate that read one could not be asked what it thought at a
particular moment, and the rule belongs to the use case that was handed a moment.
The absence is asserted nowhere because there is nothing to assert.
"""

from datetime import date, datetime, timedelta
import uuid

import pytest

from app.domain.identity.exception import (
    InvalidProfileAddressError,
    InvalidProfileCountryError,
    InvalidProfileCreatedAtError,
    InvalidProfileDateOfBirthError,
    InvalidProfileDisplayNameError,
    InvalidProfileLegalNameError,
    InvalidProfilePhoneError,
    InvalidProfileUpdatedAtError,
    InvalidProfileUserIDError,
    InvalidProfileWindowError,
)
from app.domain.identity.profile import MAX_TEXT_LENGTH, Profile

#: A moment to build fixtures from. Naive, like every timestamp in this system -
#: there is no timezone concept anywhere in the codebase and inventing one here
#: would make these fixtures disagree with every stored row.
CREATED = datetime(2026, 1, 31, 9, 0, 0)
UPDATED = datetime(2026, 2, 1, 12, 30, 0)

#: Two dates worth having named, because they straddle the trap.
BIRTH_DATE = date(1990, 5, 17)
#: The *same day* as ``BIRTH_DATE``, with a midnight attached. This is the value
#: the aggregate must refuse, and it is the one a careless caller produces - a
#: ``datetime.now().date()`` used correctly, then fed back through a column that
#: stored it as a datetime.
BIRTH_DATETIME = datetime(1990, 5, 17, 0, 0, 0)


def a_profile(**overrides) -> Profile:
    """A complete, valid profile, with individual fields overridable.

    A helper rather than a fixture so a test can say *which one thing* it is
    changing. Every test below that refuses a field builds it from this, which
    is what makes the refusal attributable to the override rather than to the
    fixture - the failure a hand-built profile per test invites, where a
    two-field mistake reads as a one-field rule.
    """
    fields = dict(
        user_id=uuid.uuid4(),
        display_name="Ada",
        legal_first_name="Ada",
        legal_last_name="Lovelace",
        date_of_birth=BIRTH_DATE,
        phone="+2348000000000",
        country="NG",
        address_line="1 Analytical Engine Way",
        created_at=CREATED,
        updated_at=UPDATED,
    )
    fields.update(overrides)
    return Profile(**fields)


def test_a_complete_profile_is_built_and_keeps_what_it_was_given():
    profile = a_profile()

    assert profile.legal_first_name == "Ada"
    assert profile.legal_last_name == "Lovelace"
    assert profile.date_of_birth == BIRTH_DATE
    assert profile.is_complete is True


def test_a_profile_of_nothing_but_a_name_is_a_valid_profile():
    """The design, asserted rather than assumed: partial is not malformed.

    A person fills this in over more than one sitting, and a given name with no
    surname is a real state to be in the middle of. Nothing anywhere has to
    represent "half-filled" as a special case - the tier is derived from which
    fields are present, and ``tier_for`` reads the absence.
    """
    profile = a_profile(
        legal_first_name=None,
        legal_last_name=None,
        date_of_birth=None,
        phone=None,
        country=None,
        address_line=None,
    )

    assert profile.is_complete is False
    assert profile.display_name == "Ada"


# --- the required field -----------------------------------------------------


@pytest.mark.parametrize(
    "display_name",
    ["", "   ", "\t\n", None, 42, "x" * (MAX_TEXT_LENGTH + 1)],
)
def test_a_display_name_that_is_absent_blank_wrong_typed_or_absurd_is_refused(
    display_name,
):
    """Six ways to fail one field, and the last two are the ones worth naming.

    ``42`` is refused rather than stringified, because a display name is text and
    a number there means the caller read the wrong field. The over-length one is
    not a policy about names - it is the ceiling that keeps a value out of a
    ``TEXT`` column and a mail body, and it is set where no real name reaches it.
    """
    with pytest.raises(InvalidProfileDisplayNameError):
        a_profile(display_name=display_name)


def test_a_display_name_is_stripped_but_its_interior_is_left_alone():
    """Stripping is normalisation, not tidying - ``"Ada  Lovelace"`` stays as typed."""
    assert a_profile(display_name="  Ada  ").display_name == "Ada"
    assert a_profile(display_name="Ada  Lovelace").display_name == "Ada  Lovelace"


# --- the optional text fields ----------------------------------------------


@pytest.mark.parametrize(
    "field,error",
    [
        ("legal_first_name", InvalidProfileLegalNameError),
        ("legal_last_name", InvalidProfileLegalNameError),
        ("phone", InvalidProfilePhoneError),
        ("address_line", InvalidProfileAddressError),
    ],
)
def test_a_blank_optional_field_becomes_none(field, error):
    """**Blank and absent are the same fact, and this is where that is made true.**

    A column holding ``""`` and a column holding ``NULL`` are two spellings of
    "not given", and every reader that tested one would be wrong about the other.
    Normalising at construction is what makes ``is_complete`` below a plain
    ``is not None`` check rather than a test that has to remember to strip - and
    the failure this prevents is a profile that reads complete because somebody
    typed a space into the phone field.
    """
    assert getattr(a_profile(**{field: ""}), field) is None
    assert getattr(a_profile(**{field: "   "}), field) is None
    assert getattr(a_profile(**{field: None}), field) is None


@pytest.mark.parametrize(
    "field,error",
    [
        ("legal_first_name", InvalidProfileLegalNameError),
        ("legal_last_name", InvalidProfileLegalNameError),
        ("phone", InvalidProfilePhoneError),
        ("address_line", InvalidProfileAddressError),
    ],
)
def test_an_optional_field_of_the_wrong_type_or_length_is_refused(field, error):
    """``None`` is accepted and *anything else not a string* is not.

    The distinction matters because ``None`` is the field's way of saying "not
    given" and is therefore a value, while ``42`` is a caller that read the wrong
    column. A rule that accepted both would silently store a number where a name
    belongs.
    """
    with pytest.raises(error):
        a_profile(**{field: 42})
    with pytest.raises(error):
        a_profile(**{field: "x" * (MAX_TEXT_LENGTH + 1)})


def test_the_phone_rule_is_a_ceiling_and_not_a_grammar():
    """**Deliberately permissive, and asserted so it cannot drift into a grammar.**

    ``"12"`` is not a dialable number and it is accepted. Whether a number reaches
    a person is a question for the SMS provider that does not exist yet, and a
    regex here would refuse the international formats it had not thought of - the
    same argument ``checked_email`` makes for requiring an ``@`` and nothing more.
    A test that only asserted the refusals would pass for a rule that had grown
    one, and a grammar here is exactly the mistake.
    """
    assert a_profile(phone="12").phone == "12"
    assert a_profile(phone="not a number at all").phone == "not a number at all"


# --- the country ------------------------------------------------------------


@pytest.mark.parametrize("code", ["NG", "ng", "Ng", "GB", "US"])
def test_a_two_letter_country_is_accepted_and_uppercased(code):
    """``ng`` and ``NG`` are the same country and the column holds one spelling.

    ``fold_email``'s reasoning applied to a country: normalising at the boundary
    means no reader has to remember to case-fold, and no query has to compare
    two spellings of one fact.
    """
    assert a_profile(country=code).country == code.upper()


@pytest.mark.parametrize("country", ["Lagos", "Nigeria", "N", "NGA", "N1", "1G"])
def test_a_country_that_is_not_a_two_letter_code_is_refused(country):
    """``"NGA"`` is refused, and that is the shape rule being honest about itself.

    Three letters is a real code in ISO 3166 alpha-3 and this refuses it, because
    the rule here checks one thing: two letters. Widening it to accept both
    spellings would mean the column could hold either, and every comparison would
    have to know which - a membership question dressed as a shape question.

    ``"1G"`` is here for the ``isalpha`` clause, which is the one that would
    otherwise let a digit through: it is two characters long and it is not a
    country, and ``len(checked) != 2`` alone would accept it.
    """
    with pytest.raises(InvalidProfileCountryError):
        a_profile(country=country)


@pytest.mark.parametrize("country", ["", "   ", None])
def test_a_blank_country_becomes_none_rather_than_a_code(country):
    """A country field nobody filled in is absent, not malformed.

    Separate from the refusals above because the *outcome* is different and the
    difference is load-bearing: a blank country leaves the profile unverified,
    while a country of ``"Lagos"`` is refused outright. Collapsing them would mean
    a person who tabbed past the field could not save the rest of their profile.
    """
    assert a_profile(country=country).country is None


def test_a_two_letter_country_that_is_not_real_is_accepted():
    """**The narrowness, asserted rather than left to be assumed.**

    ``"ZZ"`` is not an assigned ISO 3166 code and this accepts it. That is a
    decision: a hand-written list of two hundred countries is a list that goes
    stale, and the thing that actually verifies a country is the identity check
    that has not been built. Catching a *typo* is worth the rule; catching a
    forgery is not a thing a string comparison can do.
    """
    assert a_profile(country="ZZ").country == "ZZ"


# --- the inverted trap ------------------------------------------------------


def test_a_date_of_birth_may_be_a_date():
    assert a_profile(date_of_birth=BIRTH_DATE).date_of_birth == BIRTH_DATE


def test_a_date_of_birth_may_be_absent():
    assert a_profile(date_of_birth=None).date_of_birth is None


def test_a_date_of_birth_that_is_a_datetime_is_refused():
    """**The inversion, pinned, and the single most important test in this file.**

    ``datetime`` *is* a ``date`` - it is a subclass - so a check written the way
    every other aggregate writes its check would accept this value and store
    ``1990-05-17T00:00:00``. That timestamp says the person was born at midnight,
    which nobody was, and it is a precision this system has no business holding.

    Note that the two clauses of the real check are ordered and neither is
    redundant: ``isinstance(x, datetime)`` catches the subclass first, and
    ``not isinstance(x, date)`` catches everything else that is not a date at
    all. Dropping the first clause makes this test fail while the type is still
    nominally "a date".
    """
    with pytest.raises(InvalidProfileDateOfBirthError):
        a_profile(date_of_birth=BIRTH_DATETIME)


@pytest.mark.parametrize("value", ["1990-05-17", 19900517, None])
def test_nothing_but_a_date_or_none_is_a_date_of_birth(value):
    """``None`` is in this list because it is *accepted* and the others are not.

    Written as one test so the boundary is visible in one place: the field takes
    a ``date`` or nothing, and the string spelling of a date is not a date. A
    caller holding text has to parse it, deliberately - parsing is where a
    timezone or a locale gets applied, and that decision belongs at the edge
    rather than inside the aggregate.
    """
    if value is None:
        assert a_profile(date_of_birth=value).date_of_birth is None
    else:
        with pytest.raises(InvalidProfileDateOfBirthError):
            a_profile(date_of_birth=value)


# --- identity and the window ------------------------------------------------


@pytest.mark.parametrize("user_id", ["not-a-uuid", None, 42])
def test_a_user_id_that_is_not_a_uuid_is_refused(user_id):
    with pytest.raises(InvalidProfileUserIDError):
        a_profile(user_id=user_id)


@pytest.mark.parametrize("created_at", [None, "2026-01-31", date(2026, 1, 31)])
def test_created_at_must_be_a_datetime_and_a_bare_date_is_refused(created_at):
    """The ordinary check, in the file where the ordinary check is inverted once.

    ``date(2026, 1, 31)`` is refused here *and* accepted as a ``date_of_birth``
    three tests above. That is not an inconsistency - it is the same rule read
    against two different kinds of fact, and having both in one file is what
    makes the distinction legible rather than mysterious.
    """
    with pytest.raises(InvalidProfileCreatedAtError):
        a_profile(created_at=created_at)


@pytest.mark.parametrize("updated_at", [None, "2026-02-01", date(2026, 2, 1)])
def test_updated_at_must_be_a_datetime(updated_at):
    with pytest.raises(InvalidProfileUpdatedAtError):
        a_profile(updated_at=updated_at)


def test_a_profile_updated_before_it_was_created_is_refused():
    """``Session``'s window rule, and it is sharper here.

    "You have not changed this since before it existed" is not a message any
    interface can render honestly, and the pair of columns exists so that "when
    did this person last change their details" is answerable. A row where the
    second is before the first is a contradiction a reader could not interpret,
    so it is refused at construction - and the schema refuses it a second time
    with a ``CHECK``, for a row written by something that did not come through
    here.
    """
    with pytest.raises(InvalidProfileWindowError):
        a_profile(updated_at=CREATED - timedelta(seconds=1))


def test_a_zero_length_window_is_allowed():
    """``<`` and not ``<=``, and this is the case that tells them apart.

    A profile created and saved in the same instant - which is what a single
    ``ProfileService.save`` on a fresh account produces - has ``created_at ==
    updated_at`` and is entirely ordinary. ``Session`` makes the same choice for
    the same reason, and the boundary is worth pinning in both because it is the
    only one a test can get wrong without noticing.
    """
    profile = a_profile(created_at=CREATED, updated_at=CREATED)

    assert profile.created_at == profile.updated_at


# --- completeness -----------------------------------------------------------


def test_a_profile_with_the_four_checked_facts_is_complete():
    assert a_profile().is_complete is True


@pytest.mark.parametrize(
    "missing",
    ["legal_first_name", "legal_last_name", "date_of_birth", "phone", "country"],
)
def test_each_of_the_four_facts_is_necessary(missing):
    """Five fields, four facts - and both halves of the name count separately.

    The parametrisation is over the *fields* rather than the facts because that is
    what a caller can omit: "a legal name" is two columns, and dropping either one
    leaves a name that cannot be checked against anything.
    """
    assert a_profile(**{missing: None}).is_complete is False


def test_a_display_name_is_not_one_of_the_four():
    """**The omission, asserted, because it is the counter-intuitive one.**

    A display name is a handle the holder chose, not a fact about them, and no
    verification provider has ever confirmed anybody's nickname. It is required
    on the aggregate - a profile with nothing in it is a row that exists to say
    nothing - and it is deliberately *not* required for completeness, so a person
    who gives their real name and leaves the handle defaulted is identified.

    The two assertions are the whole shape of the decision read together: the
    field cannot be absent, *and* its presence is not what completeness is made
    of. Only the first would be satisfied by a check that counted it; only the
    second would be satisfied by a profile that did not require it at all.
    """
    with pytest.raises(InvalidProfileDisplayNameError):
        a_profile(display_name=None)

    assert a_profile(display_name="Ada").is_complete is True


def test_an_address_is_not_one_of_the_four():
    """And this one is subtler than the display name.

    An address *is* a fact about a person and it is still not required, because
    the four fields are the ones an identity check would confirm - and an address
    is confirmed by mailing something to it, which is a different operation
    against a different system. Requiring it here would refuse the identified
    tier to somebody who had given everything a check could actually verify.
    """
    assert a_profile(address_line=None).is_complete is True


# --- revise -----------------------------------------------------------------


def test_revise_replaces_every_field():
    profile = a_profile()

    profile.revise(
        display_name="Ada L",
        legal_first_name="Augusta",
        legal_last_name="Byron",
        date_of_birth=date(1815, 12, 10),
        phone="+440000000000",
        country="gb",
        address_line="Newstead Abbey",
        now=UPDATED + timedelta(days=1),
    )

    assert profile.display_name == "Ada L"
    assert profile.legal_first_name == "Augusta"
    assert profile.legal_last_name == "Byron"
    assert profile.date_of_birth == date(1815, 12, 10)
    assert profile.phone == "+440000000000"
    assert profile.country == "GB"
    assert profile.address_line == "Newstead Abbey"
    assert profile.updated_at == UPDATED + timedelta(days=1)


def test_revise_clears_a_field_by_passing_none():
    """**The reason this is a whole replacement and not a patch.**

    A merge would need a way to say "leave this one alone", and the only values
    that could carry that meaning are ``None`` and a sentinel - so "clear my phone
    number" and "do not touch my phone number" would be the same request. Sending
    the whole profile makes both expressible: ``None`` means clear, and a value
    means set. Here it clears.
    """
    profile = a_profile(phone="+2348000000000", country="NG")

    profile.revise(
        display_name="Ada",
        legal_first_name="Ada",
        legal_last_name="Lovelace",
        date_of_birth=BIRTH_DATE,
        phone=None,
        country=None,
        address_line=None,
        now=UPDATED,
    )

    assert profile.phone is None
    assert profile.country is None
    assert profile.is_complete is False


def test_revise_carries_created_at_across_and_moves_updated_at():
    """Two timestamps, two different meanings, and the distinction is the point.

    ``created_at`` is when the person first gave us this; ``updated_at`` is when
    they last changed it. An edit is not a re-creation, so the first is carried
    over and never taken from ``now`` - writing ``now`` into both would collapse
    the two columns into one field spelled twice.
    """
    profile = a_profile(created_at=CREATED, updated_at=CREATED)
    later = CREATED + timedelta(days=30)

    profile.revise(
        display_name="Ada",
        legal_first_name="Ada",
        legal_last_name="Lovelace",
        date_of_birth=BIRTH_DATE,
        phone="+2348000000000",
        country="NG",
        address_line=None,
        now=later,
    )

    assert profile.created_at == CREATED
    assert profile.updated_at == later


def test_a_refused_revise_leaves_the_profile_exactly_as_it_was():
    """**The half-applied edit, which is why the check runs before any assignment.**

    ``User.change_email``'s rule, and it matters more here because there are
    seven fields rather than one - so a partially applied edit is seven times the
    opportunity for a profile that is neither what it was nor what was asked for.

    The values below are chosen so that the *last* field is the one that fails,
    which is the case a naive implementation gets wrong: an implementation that
    assigned as it checked would have already overwritten the display name, the
    legal name and the birth date by the time the country was refused. Asserting
    the display name specifically is what catches that, because it is the first
    field and therefore the one a running-assignment version would have moved.
    """
    profile = a_profile()

    with pytest.raises(InvalidProfileCountryError):
        profile.revise(
            display_name="Changed",
            legal_first_name="Changed",
            legal_last_name="Changed",
            date_of_birth=date(1800, 1, 1),
            phone="+000000000000",
            country="Lagos",  # the one that fails
            address_line="Somewhere else",
            now=UPDATED + timedelta(days=1),
        )

    assert profile.display_name == "Ada"
    assert profile.legal_first_name == "Ada"
    assert profile.legal_last_name == "Lovelace"
    assert profile.date_of_birth == BIRTH_DATE
    assert profile.phone == "+2348000000000"
    assert profile.country == "NG"
    assert profile.address_line == "1 Analytical Engine Way"
    assert profile.updated_at == UPDATED


def test_a_refused_revise_does_not_move_the_window_either():
    """The same test read from the other end: a refusal is not an edit.

    Worth separating from the assertions above because ``updated_at`` is the one
    field ``revise`` sets from its own argument rather than from the caller's
    body, so it is the one a partial implementation could move without any of the
    seven fields changing - leaving a profile that looks untouched and claims to
    have been changed today.
    """
    profile = a_profile(created_at=CREATED, updated_at=CREATED)

    with pytest.raises(InvalidProfileDateOfBirthError):
        profile.revise(
            display_name="Ada",
            legal_first_name="Ada",
            legal_last_name="Lovelace",
            date_of_birth=BIRTH_DATETIME,  # the one that fails
            phone=None,
            country=None,
            address_line=None,
            now=CREATED + timedelta(days=365),
        )

    assert profile.updated_at == CREATED
