from datetime import date

import pytest

from app.domain.identity.exception import (
    InvalidPasswordError,
    WeakPasswordError,
)
from app.domain.identity.password import MAXIMUM_LENGTH, MINIMUM_LENGTH, PlainPassword


def build(secret="correct-horse-battery") -> PlainPassword:
    return PlainPassword(secret)


class TestWhatAPasswordHolds:
    def test_it_keeps_what_it_was_given(self):
        assert build("hunter2-and-then-some").secret == "hunter2-and-then-some"

    def test_it_is_not_stripped(self):
        """A trailing space is part of the password, and this is the decision.

        The opposite of what ``User`` does to an address, and the difference is
        the reason it is worth a test. An address is an identifier compared against
        other systems, so normalising it is right; a password is compared against
        nothing but itself, so trimming one would silently alter what a person
        chose - and they would have no way to find out which version they have.

        Asserted at both ends, because a ``strip()`` that was added would most
        likely be written once and a test that only covered the trailing space
        would still catch it - but the leading one is the case a reader is least
        likely to have considered.
        """
        assert build("  spaced out  ").secret == "  spaced out  "

    def test_a_password_of_spaces_is_allowed(self):
        """Length is the rule, and this satisfies it.

        The obvious-looking extra rule - "must contain something that is not a
        space" - is not here, and its absence is deliberate rather than an
        oversight. Every composition rule added to a length rule buys a smaller
        search space for the attacker and a worse password from the user; the
        eight-space password is not one anybody will choose, and refusing it would
        mean the *policy* had an opinion about content, which is the beginning of
        the ``Password1!`` spiral.
        """
        assert build("        ").secret == "        "


class TestTheLengthPolicy:
    def test_the_shortest_allowed_password_is_accepted(self):
        """The boundary from below, so the floor is not accidentally higher.

        ``MINIMUM_LENGTH`` rather than a literal eight: a test that hard-coded the
        number would have to be edited by whoever changed the rule, and would
        therefore be edited to agree with whatever they changed it to.
        """
        assert len(build("x" * MINIMUM_LENGTH).secret) == MINIMUM_LENGTH

    def test_one_character_less_is_refused(self):
        with pytest.raises(WeakPasswordError):
            build("x" * (MINIMUM_LENGTH - 1))

    def test_the_longest_allowed_password_is_accepted(self):
        assert len(build("x" * MAXIMUM_LENGTH).secret) == MAXIMUM_LENGTH

    def test_one_character_more_is_refused(self):
        """The ceiling, and it is not about storage.

        argon2 has no length limit - unlike bcrypt, which silently truncates at 72
        bytes. The limit exists because hashing is *work by design*, so an
        unbounded input turns a login endpoint into a way to make the server burn
        memory and CPU on request. A test that only checked the boundary would miss
        the reason, which is why the module states it.
        """
        with pytest.raises(WeakPasswordError):
            build("x" * (MAXIMUM_LENGTH + 1))

    def test_the_ceiling_is_not_the_storage_limit(self):
        """The two refusals are told apart by name, because they mean different things.

        A password that is too short is a user who needs to be told what to type
        instead; one that is too long is somebody pasting a file into the prompt.
        Both are ``WeakPasswordError`` - the class is about the value, not about
        which end of the range it fell off - but the messages differ, and this is
        what says so.
        """
        with pytest.raises(WeakPasswordError, match="at least"):
            build("short")
        with pytest.raises(WeakPasswordError, match="at most"):
            build("x" * (MAXIMUM_LENGTH + 1))


class TestWhatIsNotAPassword:
    def test_a_non_string_is_refused(self):
        with pytest.raises(InvalidPasswordError):
            build(b"bytes-are-not-text")

    def test_none_is_refused(self):
        with pytest.raises(InvalidPasswordError):
            build(None)

    def test_an_empty_password_is_refused_as_the_wrong_kind_of_error(self):
        """Empty is *not* "too short", and the two classes are why that is checkable.

        ``""`` is a caller that passed nothing - a bug at the boundary - while
        ``"short"`` is a person who chose badly. Collapsing them would make a
        programming error look like a user's mistake, and the caller that has
        something useful to say needs to know which it is holding.
        """
        with pytest.raises(InvalidPasswordError):
            build("")

    def test_the_emptiness_check_runs_before_the_length_check(self):
        """Ordering, asserted directly rather than inferred from the class names.

        Both checks would fire for ``""`` if the length rule were consulted first -
        zero is less than eight - so the fact that ``InvalidPasswordError`` comes
        out is the only evidence that the order is what the docstring says.
        """
        with pytest.raises(InvalidPasswordError) as raised:
            build("")

        assert not isinstance(raised.value, WeakPasswordError)


class TestTheReprDoesNotLeakThePassword:
    """The safety property, and the reason this class is not a plain dataclass.

    Three assertions, because there are three ways a secret gets out of an
    object: through ``repr``, through ``str``, and through f-string interpolation -
    and the codebase already has two places that turn an exception into text a
    user sees (``errors._detail`` and ``cli._describe``), which is how a password
    would travel from here into a log or a response body.
    """

    def test_repr_shows_that_it_is_a_password_and_not_which_one(self):
        assert repr(build("correct-horse-battery")) == "PlainPassword(<redacted>)"

    def test_str_shows_the_same(self):
        assert str(build("correct-horse-battery")) == "PlainPassword(<redacted>)"

    def test_interpolation_shows_the_same(self):
        """``f"{password}"``, which is the form that appears in a real traceback.

        Written as an f-string rather than calling ``format`` explicitly, because
        the mistake this guards against is somebody writing an f-string.
        """
        password = build("correct-horse-battery")

        assert f"{password}" == "PlainPassword(<redacted>)"

    def test_repr_does_not_leak_the_length_either(self):
        """A redaction that leaks a little is harder to trust than one that leaks nothing.

        A length narrows the search space for anyone attacking a hash, and it is
        the kind of detail that looks harmless when it is added.
        """
        assert "8" not in repr(build("12345678"))
        assert "24" not in repr(build("x" * 24))

    def test_the_value_is_still_readable_by_its_owner(self):
        """Redacted from strangers, not from the hasher.

        Without this, the tests above would pass for a class that simply refused to
        hand its value to anybody - and argon2 would have nothing to hash.
        """
        assert build("correct-horse-battery").secret == "correct-horse-battery"


def test_a_datetime_is_not_a_password():
    """The type check is a type check, not a truthiness one.

    ``PlainPassword(datetime.now())`` is a caller that has mixed up an argument
    order - and a check written as ``if not self.secret`` would let a *truthy*
    wrong type straight through, to fail later inside argon2 with a message about
    bytes. Named separately from the other wrong-type cases because a ``date``
    slipping in here is exactly the mistake ``User`` and ``Session`` both guard
    against on their own fields.
    """
    with pytest.raises(InvalidPasswordError):
        PlainPassword(date(2026, 1, 1))
