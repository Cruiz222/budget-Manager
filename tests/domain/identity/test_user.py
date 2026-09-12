from datetime import date, datetime
from uuid import uuid4

import pytest

from app.domain.identity.exception import (
    InvalidUserCreatedAtError,
    InvalidUserEmailError,
    InvalidUserGoogleSubjectError,
    InvalidUserIDError,
)
from app.domain.identity.user import User, fold_email

MOMENT = datetime(2026, 3, 2, 12, 0)


def build(**overrides) -> User:
    """A user with every field valid, so a test can spoil exactly one.

    Local rather than the conftest fixture on purpose: what is under test here is
    the constructor's own refusals, and a helper that came from the shared
    fixture set would make these tests depend on a file that also carries the
    database fixtures. This file needs nothing but the aggregate.
    """
    fields = {
        "user_id": uuid4(),
        "email": "chinedu@example.com",
        "google_subject": None,
        "created_at": MOMENT,
    }
    fields.update(overrides)
    return User(**fields)


class TestWhatAUserHolds:
    def test_it_keeps_what_it_was_given(self):
        user_id = uuid4()

        user = build(user_id=user_id, email="chinedu@example.com")

        assert user.user_id == user_id
        assert user.email == "chinedu@example.com"
        assert user.google_subject is None
        assert user.created_at == MOMENT

    def test_it_holds_no_credential(self):
        """The absence is the design, so it is worth an assertion.

        A password hash on this dataclass would make every user read - from a
        scoped wallet lookup to a receipt's recipient - carry a secret into
        whatever logs the object. Credentials belong to the adapter that can
        verify them, and the way to keep them out of the domain is to notice if
        one ever arrives.
        """
        user = build()

        assert not hasattr(user, "password")
        assert not hasattr(user, "password_hash")
        assert not hasattr(user, "salt")

    def test_a_google_account_records_its_subject(self):
        user = build(google_subject="114988223156872419036")

        assert user.google_subject == "114988223156872419036"


class TestTheEmailIsFolded:
    """One spelling per address, decided where the value is constructed.

    The rule exists so the ``UNIQUE`` column means what it looks like it means.
    These tests are written as pairs - two spellings, one stored value - because
    that is the claim: not that the email is lowercase, but that *these two are
    the same account*.
    """

    def test_an_uppercase_address_is_stored_lowercase(self):
        assert build(email="Chinedu@Example.com").email == "chinedu@example.com"

    def test_two_spellings_of_one_address_agree(self):
        first = build(email="chinedu@example.com")
        second = build(email="CHINEDU@EXAMPLE.COM")

        assert first.email == second.email

    def test_surrounding_whitespace_is_removed(self):
        """A pasted address arrives with a trailing space more often than not.

        Refusing it would be defensible; folding it is better, because the person
        typing it has done nothing wrong and the address is unambiguous.
        """
        assert build(email="  chinedu@example.com\n").email == "chinedu@example.com"

    def test_the_domain_part_alone_would_not_be_enough(self):
        """The fold is total, not just on the ``@`` half.

        Every mail system treats the domain as case-insensitive, and almost all
        treat the local part as case-*sensitive* in principle - but no provider
        anybody uses actually does, and a person who capitalised their own name
        when signing up would otherwise be unable to log in as themselves. Folding
        the whole address is the choice that matches what users expect.
        """
        assert build(email="Chinedu.Okafor@Example.com").email == (
            "chinedu.okafor@example.com"
        )

    def test_fold_email_is_the_same_rule_the_aggregate_applies(self):
        """The helper is shared with the repository, and this pins that it is shared.

        ``SqliteUserRepository.find_by_email`` looks an address up before a
        ``User`` exists, so it cannot get the fold by constructing the aggregate -
        it calls ``fold_email`` directly. If the two ever drifted, the failure
        would be a person unable to log in to an account that plainly exists:
        the aggregate would store one spelling and the lookup would search for
        another, and nothing in the store would be wrong.
        """
        written = "  Chinedu@Example.com "

        assert build(email=written).email == fold_email(written)


class TestWhatCannotBeBuilt:
    @pytest.mark.parametrize("user_id", ["not-a-uuid", None, 42, "0000"])
    def test_a_user_id_that_is_not_a_uuid_is_rejected(self, user_id):
        with pytest.raises(InvalidUserIDError):
            build(user_id=user_id)

    @pytest.mark.parametrize("email", [None, 42, b"chinedu@example.com", ["a@b"]])
    def test_a_non_string_email_is_rejected(self, email):
        with pytest.raises(InvalidUserEmailError):
            build(email=email)

    @pytest.mark.parametrize("email", ["", "   ", "\t\n"])
    def test_an_empty_email_is_rejected(self, email):
        """Whitespace-only is refused *as empty*, before the fold could store it.

        The order matters and is why this is worth stating: folding first turns
        ``"   "`` into ``""`` and the emptiness test catches it. Testing emptiness
        first would let the whitespace through to be folded into an empty string
        afterwards - a user nobody could ever log in as, sitting in the table
        looking valid.
        """
        with pytest.raises(InvalidUserEmailError):
            build(email=email)

    @pytest.mark.parametrize("email", ["chinedu", "chinedu.example.com", "a b"])
    def test_an_email_with_no_at_sign_is_rejected(self, email):
        """The whole of the grammar, and deliberately so.

        A complete address syntax belongs where an address is *verified* - a
        confirmation mail arrives or it does not, and no regex changes that. What
        is caught here is the value that is obviously not an address at all, so
        that a typo stops at the table rather than at the login form.
        """
        with pytest.raises(InvalidUserEmailError):
            build(email=email)

    @pytest.mark.parametrize("email", ["@", "chinedu@", "@example.com", "a@b@c"])
    def test_the_narrow_check_accepts_what_it_does_not_look_for(self, email):
        """Where the check stops, asserted rather than assumed.

        None of these is a usable address, and every one of them is *accepted* -
        because the rule is "contains an ``@``" and nothing more. Writing that
        down is the point: the alternative is a reader assuming a grammar check
        is happening here, and being wrong about it at the one place where being
        wrong means an address reaches the table unrejected.

        This is not a licence to widen the check. It is the boundary made
        visible, so that widening it later is a decision somebody makes on
        purpose rather than a bug somebody fixes by accident.
        """
        assert build(email=email).email == email

    @pytest.mark.parametrize("subject", [42, b"114988", ["114988"]])
    def test_a_non_string_google_subject_is_rejected(self, subject):
        with pytest.raises(InvalidUserGoogleSubjectError):
            build(google_subject=subject)

    @pytest.mark.parametrize("subject", ["", "   ", "\t"])
    def test_an_empty_google_subject_is_rejected(self, subject):
        """Empty is refused where ``None`` is not, and the difference is the column.

        ``google_subject`` is ``UNIQUE``. Every account that arrived without a
        Google identity would collide on the same empty string, so the second
        signup would fail against the first - a bug that only appears once
        somebody signs up, and only for the second person to do it.

        ``None`` is the honest value for "no Google identity", and SQLite permits
        any number of NULLs in a UNIQUE column. So the two cases stay distinct,
        and this test is what keeps the empty string from being treated as a
        third one.
        """
        with pytest.raises(InvalidUserGoogleSubjectError):
            build(google_subject=subject)

    def test_a_google_subject_is_not_stripped(self):
        """Unlike the email, and for a stated reason: it is not typed by a human.

        A subject is an opaque identifier Google issued. Trimming it would be
        inventing a normalisation nobody asked for, and would mean a subject
        stored one way could be looked up another - which is the bug the
        repository's exact comparison exists to avoid.
        """
        user = build(google_subject=" 114988 ")

        assert user.google_subject == " 114988 "

    def test_created_at_must_be_a_moment_and_not_a_day(self):
        """The trap: ``datetime`` *is* a ``date``, so the check must be narrow.

        Four other aggregates carry this test. The failure it guards against is
        quiet - an account stamped with a day rather than a moment has lost the
        time it was created, and no comparison downstream would notice, because
        the value still subtracts and still sorts.
        """
        assert isinstance(MOMENT, date)  # the trap, still true

        with pytest.raises(InvalidUserCreatedAtError):
            build(created_at=date(2026, 3, 2))

    @pytest.mark.parametrize("created_at", [None, "2026-03-02", 1772452800])
    def test_a_created_at_that_is_not_a_datetime_is_rejected(self, created_at):
        with pytest.raises(InvalidUserCreatedAtError):
            build(created_at=created_at)

    def test_every_refusal_derives_from_the_money_root(self):
        """The rule ``planning`` states and ``notifications`` repeats, asserted.

        The CLI catches ``MoneyError`` once, at the top of ``main``, and turns it
        into ``error: ...`` with exit code 1. Identity is the package where that
        matters most: a user is rejected at the *boundary* - ``--user
        not-an-email`` - which is precisely when a human is watching and a
        traceback is least acceptable.

        Written as a parametrised walk over the package's exceptions rather than
        as one assertion per class, so that an exception added later without the
        right parent fails here rather than as a traceback in someone's terminal.
        """
        from app.domain.identity import exception as identity_exceptions

        from app.domain.money.exception import MoneyError

        classes = [
            value
            for name, value in vars(identity_exceptions).items()
            if isinstance(value, type)
            and issubclass(value, Exception)
            and value.__module__ == identity_exceptions.__name__
        ]

        assert classes, "expected the identity package to define exceptions"
        for cls in classes:
            assert issubclass(cls, MoneyError), f"{cls.__name__} escapes the root"
