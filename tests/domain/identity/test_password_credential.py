from datetime import date, datetime
from uuid import uuid4

import pytest

from app.domain.identity.exception import (
    InvalidCredentialHashError,
    InvalidCredentialUpdatedAtError,
    InvalidUserIDError,
)
from app.domain.identity.password_credential import PasswordCredential

MOMENT = datetime(2026, 3, 2, 12, 0)


def build(**overrides) -> PasswordCredential:
    fields = {
        "user_id": uuid4(),
        "password_hash": "$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA",
        "updated_at": MOMENT,
    }
    fields.update(overrides)
    return PasswordCredential(**fields)


class TestWhatACredentialHolds:
    def test_it_keeps_what_it_was_given(self):
        user_id = uuid4()

        credential = build(user_id=user_id)

        assert credential.user_id == user_id
        assert credential.updated_at == MOMENT

    def test_the_hash_is_the_encoded_form(self):
        """Salt, parameters and digest in one string - not a bare digest.

        Storing the parameters alongside the hash is what makes it possible to
        raise argon2's cost factor later without invalidating every existing
        password: an old hash says how it was made, so it can still be verified and
        then re-hashed on the next successful login. The aggregate does not parse
        it - that is the adapter's job - but it must not be the kind of type that
        could only hold a digest.
        """
        encoded = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA"

        assert build(password_hash=encoded).password_hash == encoded

    def test_it_does_not_live_on_the_user(self):
        """Decision 53, asserted as a fact about the other aggregate.

        ``User`` is loaded by every authenticated request, handed to services and
        rendered by ``translate.user_out``. If the hash travelled on it, every one
        of those places would be holding a value that must never be printed, and
        the only thing keeping it out of a response would be somebody remembering
        to omit it. Here the hash is loaded by one use case for the length of one
        login, and a ``User`` cannot leak what it does not have.

        This is a structural assertion rather than a behavioural one, and it is
        worth having because the mistake it prevents - "just put the hash on the
        user, it is simpler" - is a genuinely tempting one.
        """
        from app.domain.identity.user import User

        assert not hasattr(User, "password_hash")
        assert "password_hash" not in User.__dataclass_fields__


class TestWhatIsNotACredential:
    def test_a_non_uuid_user_id_is_refused(self):
        with pytest.raises(InvalidUserIDError):
            build(user_id="not-a-uuid")

    def test_a_non_string_hash_is_refused(self):
        with pytest.raises(InvalidCredentialHashError):
            build(password_hash=None)

    def test_an_empty_hash_is_refused(self):
        """A credential that can never verify anything, wearing the shape of one that works.

        The failure this prevents surfaces as "my password stopped working" with
        nothing in the database looking wrong - the same reasoning as an empty
        Google subject on ``User`` and an empty token hash on ``Session``.
        """
        with pytest.raises(InvalidCredentialHashError):
            build(password_hash="")

    def test_a_date_where_a_moment_belongs_is_refused(self):
        with pytest.raises(InvalidCredentialUpdatedAtError):
            build(updated_at=date(2026, 3, 2))

    def test_a_moment_in_the_future_is_allowed(self):
        """No opinion about the clock, and the absence is deliberate.

        A credential set "tomorrow" is what a machine whose clock disagrees with
        the store's produces, and the aggregate is not the place to have a view
        about it - the same position ``User.created_at`` takes. Adding a check here
        would mean a login refused for a reason nothing in the request explains.
        """
        assert build(updated_at=datetime(2099, 1, 1)).updated_at == datetime(2099, 1, 1)


class TestTheReprDoesNotLeakTheHash:
    """A hash is not the password, and it is still not a thing to print.

    It is the value an offline attack is run against, so a hash in a log file is a
    hash that has left the database it was protected in. The store is the place to
    look at one.
    """

    def test_repr_names_the_user_and_the_moment_and_not_the_hash(self):
        user_id = uuid4()
        encoded = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA"

        shown = repr(build(user_id=user_id, password_hash=encoded))

        assert encoded not in shown
        assert str(user_id) in shown
        assert "<redacted>" in shown

    def test_str_shows_the_same(self):
        encoded = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA"

        assert encoded not in str(build(password_hash=encoded))

    def test_interpolation_shows_the_same(self):
        credential = build()

        assert credential.password_hash not in f"{credential}"
