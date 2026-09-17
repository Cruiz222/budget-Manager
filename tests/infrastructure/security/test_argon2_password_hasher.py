"""The ``PasswordHasher`` port, answered the way production answers it.

**The only place in this suite that runs argon2.** Every other test that needs a
password hashed uses ``FakePasswordHasher``, and for a good reason: argon2 is
*designed* to be slow, so a suite that hashed on every sign-up would spend most of
its wall clock proving that arithmetic works. That trade is only honest if the
thing being skipped is tested somewhere, and this file is where.

Being slow is also why this module is small. It is not testing the algorithm -
``argon2-cffi`` is a well-tested library and nothing here re-derives a hash by
hand. What it tests is the *adapter*: that it satisfies the port, that its
answers have the properties the rest of the codebase relies on, and in particular
that it makes the two decisions its docstring claims - argon2id with the library's
defaults, and a distinction between "wrong password" and "not a hash at all".
"""

import base64
from datetime import datetime
from uuid import uuid4

import pytest
from argon2.exceptions import InvalidHashError, VerificationError

from app.domain.identity.password import PlainPassword
from app.domain.identity.password_credential import PasswordCredential
from app.domain.identity.password_hasher import DUMMY_HASH, PasswordHasher
from app.infrastructure.security.argon2_password_hasher import Argon2PasswordHasher
from tests.conftest import FakePasswordHasher

PASSWORD = PlainPassword("correct-horse-battery")


def _header(encoded: str) -> str:
    """The ``$argon2id$v=..$m=..,t=..,p=..$`` prefix, which is the parameters.

    Split rather than parsed, because the point is that the two strings *start*
    the same way - the salt and the digest below are meant to differ.
    """
    return "$".join(encoded.split("$")[:4])


@pytest.fixture
def hasher():
    return Argon2PasswordHasher()


class TestHashing:
    def test_the_hash_is_not_the_password(self, hasher):
        """The one property that would make the whole thing pointless.

        Asserted against the plaintext appearing *anywhere* in the encoded string,
        which is stronger than an inequality: a hash that merely differed from the
        password while containing it would pass ``!=`` and fail this.
        """
        encoded = hasher.hash(PASSWORD)

        assert encoded != PASSWORD.secret
        assert PASSWORD.secret not in encoded

    def test_two_hashes_of_one_password_differ(self, hasher):
        """The salt, and what it buys.

        Without it, one cracked password reveals which other accounts share it -
        and a database-wide rainbow table would work. Note this is the property
        ``FakePasswordHasher`` deliberately reproduces, because ``LogIn`` must
        never be written to compare hashes rather than call ``verify``, and that
        mistake is only visible if two hashes of one password differ.
        """
        assert hasher.hash(PASSWORD) != hasher.hash(PASSWORD)

    def test_both_hashes_verify(self, hasher):
        """Different salts, same password, both good - so the salt is in the string.

        The natural wrong implementation stores the salt beside the hash in a single
        column and then loses it; this is what says the salt travels inside the
        encoded value, which is the whole reason the port passes the *whole* encoded
        string to ``verify`` rather than exposing a digest to compare.
        """
        first = hasher.hash(PASSWORD)
        second = hasher.hash(PASSWORD)

        assert hasher.verify(PASSWORD, first) is True
        assert hasher.verify(PASSWORD, second) is True

    def test_it_is_argon2id(self, hasher):
        """Named rather than left to the library's default.

        ``argon2id`` is the hybrid the Password Hashing Competition recommended for
        this job - it resists both the side-channel attacks ``argon2i`` is shaped
        against and the time-memory tradeoffs ``argon2d`` is shaped against. A
        default is a thing that can change under you, so the variant is asserted
        rather than assumed.
        """
        assert hasher.hash(PASSWORD).startswith("$argon2id$")

    def test_the_parameters_travel_with_the_hash(self, hasher):
        """Which is what makes raising the cost factor later a non-event.

        An old hash says how it was made, so it can still be verified after the
        parameters change - and re-hashed on the next successful login. A scheme
        that stored a bare digest would invalidate every password the day the cost
        was raised.
        """
        encoded = hasher.hash(PASSWORD)

        assert "$v=19$" in encoded
        assert "m=" in encoded and "t=" in encoded and "p=" in encoded

    def test_it_satisfies_the_port(self, hasher):
        """Cheap, and it is the assertion that would have caught a stub ``verify``.

        ``PasswordHasher`` is an ABC, so a missing method is already an error at
        construction. What this adds is that the two methods are *implemented*
        rather than inherited placeholders - which is what a half-finished adapter
        looks like.
        """
        assert isinstance(hasher, PasswordHasher)
        assert hasher.hash(PASSWORD)
        assert hasher.verify(PASSWORD, hasher.hash(PASSWORD)) is True


class TestVerifying:
    def test_the_right_password_verifies(self, hasher):
        assert hasher.verify(PASSWORD, hasher.hash(PASSWORD)) is True

    def test_a_wrong_password_is_false(self, hasher):
        encoded = hasher.hash(PASSWORD)

        assert hasher.verify(PlainPassword("something-else"), encoded) is False

    def test_a_password_that_differs_only_in_case_is_still_wrong(self, hasher):
        """No normalisation anywhere in the path, which is the point.

        A password is compared against nothing but itself, so unlike an address it
        is not folded, trimmed or lowercased - and this is the one test that would
        notice if somebody "helpfully" added that to the adapter.
        """
        encoded = hasher.hash(PASSWORD)

        assert hasher.verify(
            PlainPassword(PASSWORD.secret.upper()), encoded
        ) is False

    def test_a_password_differing_only_in_a_trailing_space_is_still_wrong(self, hasher):
        """Every character counts, including the ones that look like nothing.

        ``PlainPassword`` refuses to strip; this is the other half of that rule,
        and it is the half that lives in the hasher - argon2 hashes the bytes it
        is given, so a space at the end changes the answer.
        """
        encoded = hasher.hash(PlainPassword("correct-horse-battery "))

        assert hasher.verify(PASSWORD, encoded) is False

    def test_a_value_that_is_not_a_hash_raises_rather_than_returning_false(self, hasher):
        """Corruption and a wrong password are different things, and this is the claim.

        ``VerifyMismatchError`` means the hash was well-formed and the password
        simply did not match - the ordinary case, answered with ``False``. Anything
        else means the row is not a hash at all, and swallowing that as ``False``
        would present database corruption as a user who cannot remember their
        password: they would try again and again while the store went on looking
        fine. So it propagates, and the API's catch-all reports it as the bug it is.

        ``InvalidHashError`` and not ``VerificationError``, which is the surprise
        this test was written wrong about the first time. The hierarchy in
        ``argon2.exceptions`` is:

        * ``Argon2Error`` - ``VerificationError`` - ``VerifyMismatchError``, plus
          ``HashingError`` beside it;
        * ``InvalidHashError``, which descends from **``ValueError``** and is not an
          ``Argon2Error`` at all.

        Both propagate through ``verify`` untouched, so the behaviour was right
        either way - but they are caught by different ``except`` clauses, and an
        adapter that ever wanted to handle "the row is not a hash" as one case would
        need both. The sibling test below covers the other shape.
        """
        with pytest.raises(InvalidHashError):
            hasher.verify(PASSWORD, "not-a-hash-at-all")

    def test_a_truncated_hash_raises_too(self, hasher):
        """The realistic corruption, rather than the absurd one.

        A column cut short by a bad migration is the way this actually happens, and
        it is worth its own case because a truncated value can still *look* like a
        hash - ``$argon2id$v=19$m=65`` is recognised by its header and only fails
        once argon2 tries to parse the body. That is the state an adapter catching
        too broadly would mistake for a wrong password.

        This is the ``VerificationError`` shape, where the test above is the
        ``InvalidHashError`` one: a header argon2 does not know is rejected before
        the library is called, and a header it does know with an unreadable body
        fails inside it. Two classes, two code paths, one rule.
        """
        truncated = hasher.hash(PASSWORD)[:20]

        with pytest.raises(VerificationError):
            hasher.verify(PASSWORD, truncated)


class TestItIsInterchangeableWithTheDouble:
    """The seam the whole suite depends on, stated once and in one direction.

    Two hundred tests hash with ``FakePasswordHasher`` and one file hashes with
    argon2, and that is only sound if both can be handed to the same code. The
    adapter is the one that matters here: a ``PasswordCredential`` built from its
    output must be what ``LogIn`` stores in production.
    """

    def test_its_output_fits_the_credential_the_domain_stores(self, hasher):
        encoded = hasher.hash(PASSWORD)

        credential = PasswordCredential(
            user_id=uuid4(),
            password_hash=encoded,
            updated_at=datetime(2026, 3, 2),
        )

        assert credential.password_hash == encoded
        assert hasher.verify(PASSWORD, credential.password_hash) is True

    def test_the_double_and_the_adapter_are_not_interchangeable_at_the_value_level(self):
        """And that is correct rather than a problem.

        A hash written by one cannot be verified by the other, because each refuses
        a value tagged with the other's scheme - the adapter raises, the double
        returns ``False``. Worth stating so that nobody "fixes" it: the constraint
        is that both satisfy the *port*, and a stored hash is only ever read back by
        whichever hasher wrote it.
        """
        double = FakePasswordHasher()

        assert double.verify(PASSWORD, double.hash(PASSWORD)) is True
        assert double.verify(PASSWORD, Argon2PasswordHasher().hash(PASSWORD)) is False


class TestTheDecoyHash:
    """``DUMMY_HASH`` - the constant ``LogIn`` spends when no credential was found.

    **This class exists to answer one question no other test can: is the decoy a
    value both adapters *accept*?** ``LogIn._settle`` hands it to ``verify`` on the
    path where no account matched, and the difference between "returns ``False``"
    and "raises" is the difference between a 401 and a 500 on the login form - for
    every unknown identifier, which is to say on the path an attacker is *supposed*
    to be able to walk. ``Argon2PasswordHasher.verify`` propagates anything that is
    not a ``VerifyMismatchError``, deliberately (see the class above), so a decoy
    that argon2 cannot parse would turn the enumeration defence into an outage.
    That is why the value is generated rather than written by hand, and why the
    claim is pinned here rather than trusted.

    **The last test is about drift, and it is the reason the constant is a literal
    at all.** A hash encodes its own cost parameters, so the decoy must be made with
    the *same* ones the real hashes are - otherwise the decoy comparison answers
    faster than a genuine one and the timing gap reopens in the opposite direction.
    ``argon2.PasswordHasher()`` uses the library's current defaults, so a literal
    cannot follow them if they move. Rather than a comment asking somebody to
    notice, the mismatch fails a test.
    """

    def test_the_real_adapter_refuses_it_rather_than_raising(self, hasher):
        """``False`` and not an exception, which is the whole claim.

        ``DUMMY_HASH`` is the value a wrong password is compared against when there
        is no stored hash to compare it with. "The password was wrong" is exactly
        what it must be able to say.
        """
        assert hasher.verify(PASSWORD, DUMMY_HASH) is False

    def test_the_double_refuses_it_too(self):
        """Both adapters, because both are handed it - the suite runs on the double.

        ``FakePasswordHasher`` returns ``False`` for any value it did not tag with
        its own scheme, so this passes by construction *today* - and it is asserted
        anyway because the construct is what makes the two interchangeable, and a
        double that raised here would make every non-argon2 test of the decoy
        path fail for a reason with nothing to do with the decoy.
        """
        assert FakePasswordHasher().verify(PASSWORD, DUMMY_HASH) is False

    def test_it_is_a_real_encoding_and_not_a_stub(self):
        """Structurally a hash, not merely a string that starts like one.

        The digest is decoded and measured, because a hand-written literal is
        exactly the thing that would look right and parse wrong: ``argon2-cffi``
        reads the length off the encoding, so a four-byte digest is a hash whose
        parameters claim one thing and whose body holds another. Thirty-two bytes
        is argon2's default output length.
        """
        assert DUMMY_HASH.startswith("$argon2id$")

        digest = DUMMY_HASH.split("$")[5]
        assert len(base64.b64decode(digest + "==")) == 32

    def test_it_carries_the_parameters_the_hasher_uses_now(self, hasher):
        """The drift alarm, and the reason this file can keep a frozen literal.

        The decoy was generated with ``argon2.PasswordHasher()``'s defaults on the
        day it was written. If ``argon2-cffi`` raises those - a thing it has done
        before and will do again - every hash it produces will carry a new header
        while ``DUMMY_HASH`` keeps the old one, and the decoy comparison will
        quietly stop costing what a real comparison costs. This test turns that
        silence into a failure, and the fix is to regenerate the literal.

        It compares the *header* only: salt and digest must differ, since one hash
        of one password is never another's.
        """
        assert _header(DUMMY_HASH) == _header(hasher.hash(PASSWORD))
