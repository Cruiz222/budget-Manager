"""Who an account holder is, as opposed to which account they hold.

This is the KYC-shaped half of identity, and it is deliberately a *separate
aggregate* from ``User`` rather than seven more columns on it. ``User``'s
docstring makes the argument for its own emptiness - it is loaded by every
authenticated request and rendered by ``translate.user_out``, so anything living
on it is one forgotten omission away from being served to whoever asks who they
are. A legal name, a date of birth and a home address are exactly the values that
must not travel that way, and the arrangement is the one ``PasswordCredential``
already uses: the thing that is sensitive goes in a table of its own, keyed by
the same ``user_id``, reached only by code that meant to reach it.

**Every identity field is optional, and that is the design rather than a stage
of work.** A person fills a profile in over more than one sitting, and a given
name with no surname is a real state to be in the middle of. So the aggregate
accepts a partial profile, ``tier_for`` in ``tier`` reads the completeness off
it, and nothing anywhere has to represent "half-filled" as a special case.

**Blank and absent are the same fact, and the aggregate makes them so.** The
optional string fields are normalised to ``None`` when they hold nothing but
whitespace, which is the same move ``checked_email`` makes one module over and
for the same reason: a column holding ``""`` and a column holding ``NULL`` are
two spellings of "not given", and every reader that tested one would be wrong
about the other. Normalising at construction means ``is_complete`` below is a
plain ``is not None`` check rather than a test that has to remember to strip.
"""

from dataclasses import dataclass
from datetime import date, datetime
import uuid

from .exception import (
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

#: The longest a free-text profile field may be.
#:
#: A ceiling rather than a policy, and it exists because these fields reach a
#: ``TEXT`` column and a mail body. The number is generous enough that no real
#: name, address line or display name approaches it, so it refuses only the
#: value that is not a name at all - the same "narrow rule that catches the
#: obvious mistake" argument ``checked_email`` makes for requiring an ``@``.
MAX_TEXT_LENGTH = 200


def _optional_text(value: str | None, error: type[Exception], field: str) -> str | None:
    """Normalise an optional string field: strip it, and empty means absent.

    The one function every optional text field below goes through, and it is
    shared rather than written four times because the *rule* is shared - the
    four fields differ in nothing but their error class, and four copies of
    "strip, refuse a wrong type, refuse too long" would be four chances for one
    of them to quietly stop stripping.

    ``error`` is passed rather than caught and re-raised, so the caller names
    the field it is checking and this function never has to guess which one it
    is holding.
    """
    if value is None:
        return None

    if not isinstance(value, str):
        raise error(f"{field} must be a string or None")

    stripped = value.strip()
    if not stripped:
        # The normalisation the module docstring describes: a field filled with
        # spaces is a field nobody filled in, and storing the spaces would make
        # ``is_complete`` lie.
        return None

    if len(stripped) > MAX_TEXT_LENGTH:
        raise error(f"{field} must be at most {MAX_TEXT_LENGTH} characters")

    return stripped


@dataclass
class Profile:
    """The identity fields an account holder has given this system.

    **Not frozen**, for ``User``'s reason: normalisation in ``__post_init__``
    requires assignment. Nothing outside this class assigns these fields - the
    two ways they move are ``revise`` below and construction, and construction
    is what a repository does when it builds one from a row.

    **``date_of_birth`` is a ``date`` and not a ``datetime``, which inverts the
    rule every other aggregate in this codebase follows.** ``User``,
    ``Session``, ``Fund`` and ``PlanRun`` all check ``isinstance(x, datetime)``
    and each has a test asserting that a bare ``date`` is *refused*, because a
    moment stamped with a day has lost its time. A birth date is the opposite
    case: it is a calendar fact with no moment attached, nobody knows what time
    of day they were born, and storing a midnight would invent a precision that
    does not exist. So the check here is on ``date`` and the trap is inverted -
    the test asserts a ``datetime`` is refused, and ``datetime`` is refused
    *deliberately* even though it satisfies ``isinstance(x, date)``, because
    accepting it would store the invented midnight the sentence above rejects.

    Note what is deliberately absent: a check that the date is in the past.
    That is a real rule and it does not belong here, for the reason
    ``Fund.is_matured`` takes ``as_of`` rather than reading a clock - an
    aggregate that judged "is this a plausible birth date" against
    ``datetime.now()`` could not be asked what it thought at a particular
    moment, and a repository building a ``Profile`` from a row would be running
    a clock-dependent rule on every load. It is the use case's check, against
    the moment it was handed.
    """

    user_id: uuid.UUID
    display_name: str
    legal_first_name: str | None
    legal_last_name: str | None
    date_of_birth: date | None
    phone: str | None
    country: str | None
    address_line: str | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self):
        if not isinstance(self.user_id, uuid.UUID):
            raise InvalidProfileUserIDError("invalid profile user id")

        # The one required field, and it is required because a profile with
        # nothing in it is a row that exists to say nothing. Everything a tier
        # is derived from is optional; this is what the profile is *for*.
        self.display_name = self._required_text(
            self.display_name, InvalidProfileDisplayNameError, "display name"
        )

        self.legal_first_name = _optional_text(
            self.legal_first_name, InvalidProfileLegalNameError, "legal first name"
        )
        self.legal_last_name = _optional_text(
            self.legal_last_name, InvalidProfileLegalNameError, "legal last name"
        )
        self.phone = _optional_text(
            self.phone, InvalidProfilePhoneError, "phone"
        )
        self.address_line = _optional_text(
            self.address_line, InvalidProfileAddressError, "address line"
        )
        self.country = self._checked_country(self.country)

        if self.date_of_birth is not None:
            # ``datetime`` is refused *first* and explicitly, because
            # ``datetime`` is a subclass of ``date`` and the second test alone
            # would let one through - storing the midnight that the class
            # docstring explains does not exist. The order of the two tests is
            # the whole of the rule here, so neither is folded into the other.
            if isinstance(self.date_of_birth, datetime) or not isinstance(
                self.date_of_birth, date
            ):
                raise InvalidProfileDateOfBirthError(
                    "date of birth must be a date, not a datetime"
                )

        # ``datetime`` and not ``date`` for both, the narrow check every
        # aggregate here makes - and note it is the *opposite* of the check on
        # the field above, which is the one place in this class where two
        # moments and a calendar date sit beside each other.
        if not isinstance(self.created_at, datetime):
            raise InvalidProfileCreatedAtError("invalid created at")

        if not isinstance(self.updated_at, datetime):
            raise InvalidProfileUpdatedAtError("invalid updated at")

        # The same window ``Session`` refuses, and for a sharper version of its
        # reason: a profile that was last updated before it was created is a
        # contradiction that would render as "you have not changed this since
        # before it existed". ``<=`` rather than ``<``, matching ``Session`` -
        # a zero-length window is equally impossible.
        if self.updated_at < self.created_at:
            raise InvalidProfileWindowError(
                "a profile cannot be updated before it was created"
            )

    @staticmethod
    def _required_text(value: str, error: type[Exception], field: str) -> str:
        """Check a field that must be present, using the optional rule underneath.

        Reusing ``_optional_text`` rather than restating strip-and-limit is the
        point: the two rules must agree about what counts as blank, or a
        ``display_name`` of ``"   "`` would be refused as absent here and
        treated as absent by the optional fields one line over - the same
        question answered two ways in one method.
        """
        checked = _optional_text(value, error, field)
        if checked is None:
            raise error(f"{field} must not be empty")
        return checked

    @staticmethod
    def _checked_country(value: str | None) -> str | None:
        """Normalise a country to its two-letter code.

        **A shape rule and not a membership check**, which is ``checked_email``'s
        argument one field over: what this refuses is the value that is not a
        country code at all - ``"Lagos"``, ``"N"``, ``"Nigeria"`` - because
        catching that keeps a typo out of the table rather than out of a
        provider's rejection. Whether ``"ZZ"`` is a real ISO 3166 code is not a
        question this aggregate can answer and not one it should try to: a
        hand-written list of two hundred countries is a list that goes stale,
        and the thing that actually verifies a country is the identity check
        that has not been built yet.

        Uppercased rather than refused when lowercased, for ``fold_email``'s
        reason: ``ng`` and ``NG`` are the same country and the column should
        hold one spelling of it.
        """
        checked = _optional_text(value, InvalidProfileCountryError, "country")
        if checked is None:
            return None

        if len(checked) != 2 or not checked.isalpha() or not checked.isascii():
            raise InvalidProfileCountryError(
                "country must be a two-letter country code, e.g. NG"
            )

        return checked.upper()

    @property
    def is_complete(self) -> bool:
        """Whether this profile says enough for a tier to be derived from it.

        The four fields an identity check would actually confirm: both halves of
        a legal name, a date of birth, a phone number, and a country. A display
        name is *not* among them, and the omission is deliberate - it is a
        handle the holder chose, not a fact about them, and no verification
        provider has ever confirmed anybody's nickname.

        Answered here rather than by ``tier_for`` reading the fields itself, so
        "what makes a profile complete" has exactly one home. ``tier`` asks this
        and nothing else, which is what keeps the tier and the property from
        being two opinions about one question.
        """
        return (
            self.legal_first_name is not None
            and self.legal_last_name is not None
            and self.date_of_birth is not None
            and self.phone is not None
            and self.country is not None
        )

    def revise(
        self,
        display_name: str,
        legal_first_name: str | None,
        legal_last_name: str | None,
        date_of_birth: date | None,
        phone: str | None,
        country: str | None,
        address_line: str | None,
        now: datetime,
    ) -> None:
        """Replace every field of this profile, checking on the way in.

        **A whole replacement rather than a patch**, which is
        ``PlanService.edit_instructions``'s decision applied to a person: a
        merge would need a way to say "leave this one alone", and the only
        values that could carry that meaning are ``None`` and a sentinel - so
        "clear my phone number" and "do not touch my phone number" would be the
        same request. Sending the whole profile is unambiguous, and it is what
        both presentations do: the API's ``PUT`` body carries all seven fields,
        and the CLI merges its flags against the stored profile *before* calling
        this, so that a person changing one field is not silently clearing six.
        Either way, what arrives here is the whole profile - which is the
        property this method is holding up.

        **The check happens before anything is assigned**, by building the
        replacement values into locals first, so a refusal cannot leave this
        profile holding a half-applied edit. That is ``User.change_email``'s
        rule and it matters more here: there are seven fields rather than one,
        so a partially applied edit is seven times the opportunity for a profile
        that is neither what it was nor what was asked for.

        ``now`` is passed in rather than read, for the reason every use case in
        this codebase takes a moment, and ``updated_at`` moves with it - the
        column exists so that "when did this person last change their details"
        is answerable, and it is the only field this method does not take from
        its caller.
        """
        checked = Profile(
            user_id=self.user_id,
            display_name=display_name,
            legal_first_name=legal_first_name,
            legal_last_name=legal_last_name,
            date_of_birth=date_of_birth,
            phone=phone,
            country=country,
            address_line=address_line,
            # Carried over rather than replaced: this is an edit, not a
            # re-creation, and ``created_at`` is a fact about the row that no
            # amount of editing changes.
            created_at=self.created_at,
            updated_at=now,
        )

        self.display_name = checked.display_name
        self.legal_first_name = checked.legal_first_name
        self.legal_last_name = checked.legal_last_name
        self.date_of_birth = checked.date_of_birth
        self.phone = checked.phone
        self.country = checked.country
        self.address_line = checked.address_line
        self.updated_at = checked.updated_at
