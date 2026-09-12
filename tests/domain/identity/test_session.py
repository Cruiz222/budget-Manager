from datetime import date, datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.identity.exception import (
    InvalidSessionExpiresAtError,
    InvalidSessionIDError,
    InvalidSessionIssuedAtError,
    InvalidSessionTokenHashError,
    InvalidSessionWindowError,
    InvalidUserIDError,
)
from app.domain.identity.session import (
    SESSION_LIFETIME,
    TOKEN_BYTES,
    Session,
    hash_session_token,
    new_session_token,
)

ISSUED = datetime(2026, 3, 2, 12, 0)


def build(**overrides) -> Session:
    """A session with every field valid, so a test can spoil exactly one.

    Local rather than shared, for the reason ``test_user.py``'s builder gives: what
    is under test here is the aggregate, and this file needs nothing but it.
    """
    fields = {
        "session_id": uuid4(),
        "user_id": uuid4(),
        "token_hash": hash_session_token("a-token"),
        "issued_at": ISSUED,
        "expires_at": ISSUED + SESSION_LIFETIME,
    }
    fields.update(overrides)
    return Session(**fields)


class TestWhatASessionHolds:
    def test_it_keeps_what_it_was_given(self):
        session_id = uuid4()
        user_id = uuid4()

        session = build(session_id=session_id, user_id=user_id)

        assert session.session_id == session_id
        assert session.user_id == user_id
        assert session.issued_at == ISSUED

    def test_it_holds_a_hash_and_cannot_hold_a_token(self):
        """The type is the guarantee, and this is what says so.

        ``token_hash`` is the only form of the credential that appears on this
        aggregate, which is why there is no redacted ``repr`` here as there is on
        ``PlainPassword`` and ``PasswordCredential``: printing a session prints a
        hash, and a hash cannot be presented to authenticate - the server hashes
        whatever arrives, so the stored value would hash to something that matches
        nothing.

        Asserted as a property of what the object *has* rather than of what it
        prints, because the guarantee is structural. The plaintext token exists
        only in ``issue``'s return value.
        """
        session = build()

        assert session.token_hash == hash_session_token("a-token")
        assert not hasattr(session, "token")


class TestExpiryIsCheckedNotEnforced:
    """``is_expired`` takes the moment, and this is why that matters.

    A session that read ``datetime.now()`` itself would be untestable at its
    boundary, which is the only interesting place to test it - the same rule
    ``Fund.is_matured(as_of)`` follows. With the clock passed in, "expired exactly
    now" is a case this file can state rather than wait for.
    """

    def test_it_is_live_before_it_expires(self):
        session = build()

        assert session.is_expired(session.expires_at - timedelta(seconds=1)) is False

    def test_it_is_expired_at_the_instant_it_expires(self):
        """``>=`` and not ``>``, which is the whole boundary.

        A session is expired *at* the moment it expires, not a moment later. The
        alternative is a session that is valid for an instant its owner was never
        promised, and this single assertion is the difference.
        """
        session = build()

        assert session.is_expired(session.expires_at) is True

    def test_it_is_expired_after(self):
        session = build()

        assert session.is_expired(session.expires_at + timedelta(days=1)) is True

    def test_a_session_is_not_expired_by_a_moment_before_it_was_issued(self):
        """Nonsense in, a sensible answer out - and the reason is worth stating.

        A clock that is behind (a machine with an unsynchronised clock, a test that
        passes the wrong variable) should not make a fresh session look dead. The
        comparison needs no special case for this; saying so is what stops somebody
        adding one.
        """
        session = build()

        assert session.is_expired(ISSUED - timedelta(days=365)) is False


class TestWhatIsNotASession:
    def test_a_non_uuid_session_id_is_refused(self):
        with pytest.raises(InvalidSessionIDError):
            build(session_id="not-a-uuid")

    def test_a_non_uuid_user_id_is_refused(self):
        with pytest.raises(InvalidUserIDError):
            build(user_id="not-a-uuid")

    def test_an_empty_token_hash_is_refused(self):
        """A row that can never match, wearing the shape of one that works.

        It would present as "I keep having to sign in again" with nothing in the
        database looking wrong - the same failure an empty credential hash gives,
        for the same reason.
        """
        with pytest.raises(InvalidSessionTokenHashError):
            build(token_hash="")

    def test_a_non_string_token_hash_is_refused(self):
        with pytest.raises(InvalidSessionTokenHashError):
            build(token_hash=None)

    def test_a_date_where_a_moment_belongs_is_refused(self):
        """The trap every aggregate in this codebase guards, and it is sharper here.

        A session whose timestamps came back as bare dates would compare against a
        moment by promoting midnight - so every session would appear to expire at
        the start of its final day, and it would do so only for rows that had been
        through the store. Refusing the type at the constructor is what turns a
        wrong column mapping into a loud failure at load.
        """
        with pytest.raises(InvalidSessionIssuedAtError):
            build(issued_at=date(2026, 3, 2))

    def test_a_date_where_the_expiry_belongs_is_refused(self):
        with pytest.raises(InvalidSessionExpiresAtError):
            build(expires_at=date(2026, 4, 1))

    def test_a_window_that_closes_before_it_opens_is_refused(self):
        """Born dead, and it is a bug rather than a state.

        Nothing constructs one deliberately, so the only way to get here is a
        negative lifetime or a transposed argument order. Left alone it would
        present as a login that reports success and then does not work, which is
        the hardest kind of failure to trace back to its cause.
        """
        with pytest.raises(InvalidSessionWindowError):
            build(expires_at=ISSUED - timedelta(seconds=1))

    def test_a_zero_length_window_is_refused_too(self):
        """``<=`` rather than ``<``: a session that expires as it is issued is unusable.

        The boundary case, and the reason the check is written the way it is. A
        lifetime of zero is a plausible typo - ``timedelta(days=0)`` computed from
        a configuration value - and it produces a session that is dead on arrival.
        """
        with pytest.raises(InvalidSessionWindowError):
            build(expires_at=ISSUED)


class TestIssuing:
    def test_it_returns_a_session_and_a_token_that_match(self):
        session, token = Session.issue(uuid4(), ISSUED)

        assert session.token_hash == hash_session_token(token)

    def test_the_session_belongs_to_the_user_it_was_issued_for(self):
        user_id = uuid4()

        session, _ = Session.issue(user_id, ISSUED)

        assert session.user_id == user_id

    def test_it_lasts_the_standard_lifetime(self):
        """Absolute, not sliding - see ``SESSION_LIFETIME`` for the argument.

        Pinned here rather than left implicit, because the choice between absolute
        and sliding is the kind that gets revisited: a sliding window writes to the
        database on every authenticated read, turning a pure read into a write and
        making every request contend for SQLite's single writer.
        """
        session, _ = Session.issue(uuid4(), ISSUED)

        assert session.expires_at == ISSUED + SESSION_LIFETIME

    def test_the_lifetime_can_be_overridden(self):
        """For the tests and, later, for a remember-me or a short-lived session.

        The parameter exists so that an expiry rule can be exercised without
        waiting a month - which is the same reason the moment is passed in at all.
        """
        session, _ = Session.issue(uuid4(), ISSUED, lifetime=timedelta(minutes=5))

        assert session.expires_at == ISSUED + timedelta(minutes=5)

    def test_two_sessions_issued_in_the_same_moment_are_two_sessions(self):
        """Different ids *and* different tokens, from the same user at the same instant.

        The id half is obvious; the token half is the one that matters. Two logins
        in the same second - a person on a laptop and a phone - must not produce
        the same credential, or signing out of one would sign out of both and the
        session table's ``UNIQUE`` on ``token_hash`` would refuse the second row.
        """
        user_id = uuid4()

        first, first_token = Session.issue(user_id, ISSUED)
        second, second_token = Session.issue(user_id, ISSUED)

        assert first.session_id != second.session_id
        assert first_token != second_token
        assert first.token_hash != second.token_hash


class TestTokens:
    def test_a_token_is_long_enough_to_be_unguessable(self):
        """256 bits, which is the size at which guessing stops being an attack.

        ``token_urlsafe`` encodes three bytes into four characters, so the length
        is a consequence of ``TOKEN_BYTES`` rather than a rule of its own - and
        asserting the relationship rather than the number is what keeps this test
        true if the entropy is ever raised.
        """
        token = new_session_token()

        assert len(token) >= TOKEN_BYTES * 4 // 3

    def test_tokens_do_not_repeat(self):
        """A thousand of them, which is a smoke test for the source rather than a proof.

        ``secrets`` reads the OS CSPRNG; a duplicate here would mean the source had
        been swapped for something seeded from the clock. A test cannot prove
        randomness, and this one does not claim to - it catches the mistake that
        would actually be made.
        """
        assert len({new_session_token() for _ in range(1000)}) == 1000

    def test_a_token_is_url_safe(self):
        """It travels in a header, in a shell command and in a file.

        ``token_urlsafe`` uses ``-`` and ``_`` rather than ``+`` and ``/``, so the
        token needs no escaping anywhere it is used - which is why nothing
        downstream has to be careful with it.
        """
        token = new_session_token()

        assert set(token) <= set(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        )


class TestHashingAToken:
    """The derivation, and the property that makes storing it safe."""

    def test_the_hash_is_what_gets_stored_and_is_not_the_token(self):
        token = "a-token"

        assert hash_session_token(token) != token

    def test_the_same_token_always_hashes_the_same_way(self):
        """A pure function with exactly two callers, and this is why it must be one.

        ``LogIn`` stores the hash and ``ResolveActorFromSession`` looks one up. If
        the derivation could differ between those two, a token would work at login
        and never again - and the failure would look like a store problem.
        """
        assert hash_session_token("a-token") == hash_session_token("a-token")

    def test_different_tokens_hash_differently(self):
        assert hash_session_token("one") != hash_session_token("two")

    def test_the_stored_hash_is_not_a_usable_credential(self):
        """The property that makes a plain SHA-256 sufficient here.

        The server hashes whatever token arrives and looks *that* up. Presenting
        the stored hash therefore hashes the hash, which matches nothing - so
        somebody who reads the session table learns which sessions exist and cannot
        use a single one of them. That is exactly what a password hash does *not*
        give you, and it is why this one needs no salt.
        """
        token = new_session_token()
        stored = hash_session_token(token)

        assert hash_session_token(stored) != stored
        assert hash_session_token(stored) != token

    def test_it_is_a_sha256_digest(self):
        """Sixty-four hex characters, so a column that truncates is noticed.

        Stated as a shape rather than checked against a known vector: what matters
        to this codebase is that the value is a fixed-size hex string, which is what
        makes the ``UNIQUE`` index on the column meaningful and what makes a
        truncated write detectable at all.
        """
        digest = hash_session_token("a-token")

        assert len(digest) == 64
        assert all(character in "0123456789abcdef" for character in digest)
