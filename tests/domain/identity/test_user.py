from datetime import date, datetime
from uuid import uuid4

import pytest

from app.domain.identity.exception import (
    InvalidUserCreatedAtError,
    InvalidUserEmailError,
    InvalidUserGoogleSubjectError,
    InvalidUserIDError,
    InvalidUserIdentifierError,
    InvalidUserPhoneError,
)
from app.domain.identity.user import User, fold_email

MOMENT = datetime(2026, 3, 2, 12, 0)


def build(**overrides) -> User:
    """A user with every field valid, so a test can spoil exactly one.

    Local rather than the conftest fixture on purpose: what is under test here is
    the constructor's own refusals, and a helper that came from the shared
    fixture set would make these tests depend on a file that also carries the
    database fixtures. This file needs nothing but the aggregate.

    ``phone`` is ``None`` here rather than a number, mirroring the fixture: these
    tests are about the email shape rule and the other field rules, and a test
    about the number passes ``phone=`` explicitly.
    """
    fields = {
        "user_id": uuid4(),
        "email": "chinedu@example.com",
        "phone": None,
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

    @pytest.mark.parametrize("email", [42, b"chinedu@example.com", ["a@b"]])
    def test_a_non_string_email_is_rejected(self, email):
        """**``None`` is deliberately absent from this list**, and that is a change
        rather than an omission: it used to be here, because every account had to
        hold an address. An address is optional now, so ``None`` means "this
        account has none" and is answered by ``InvalidUserIdentifierError`` only
        when there is no number either. A malformed address is still refused, and
        a *blank* one still is too - see the next test.
        """
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


class TestTheTwoIdentifiers:
    """An account is identified by an address, a number, or both - and at least one.

    The rule is one sentence and this class is the whole of what it means at this
    layer. It is worth a class of its own because it is the *only* invariant on
    ``User`` that is about the pair rather than about either member, and because
    the failure it prevents is not a malformed row - it is a row that is perfectly
    well formed and that nothing in the system can ever reach.
    """

    def test_a_phone_only_account_is_the_shape_the_rule_permits(self):
        """The state the change exists for: a working account with no address.

        Asserted rather than assumed, because for the whole life of this class
        until now it was *unbuildable* - every repository in the codebase
        constructs a ``User`` from its row, so a shape the constructor refuses is a
        shape no store can read back.
        """
        user = build(email=None, phone="08012345678")

        assert user.email is None
        assert user.phone == "2348012345678"

    def test_an_account_may_hold_both(self):
        user = build(email="chinedu@example.com", phone="+2348012345678")

        assert user.email == "chinedu@example.com"
        assert user.phone == "2348012345678"

    @pytest.mark.parametrize(
        "written",
        ("08012345678", "+2348012345678", "2348012345678", "0801 234 5678"),
    )
    def test_a_number_is_folded_like_an_address_is(self, written):
        """Four spellings, one stored value - ``fold_email``'s rule and its reason.

        The ``UNIQUE`` constraint on the column only bounds what it looks like it
        bounds if one number has one spelling by the time it reaches the store, and
        this is where that is guaranteed: at construction, not in whichever adapter
        remembers.
        """
        assert build(phone=written).phone == "2348012345678"

    def test_a_malformed_number_is_refused_as_a_number(self):
        """``InvalidUserPhoneError``, not the pair error.

        The ordering claim: a number that cannot be a number must not be reported
        as a *missing* identifier, or a person who typed something wrong is told
        they supplied nothing.
        """
        with pytest.raises(InvalidUserPhoneError):
            build(email=None, phone="not-a-number")

    @pytest.mark.parametrize("phone", ["", "   ", "()"])
    def test_a_blank_number_is_not_the_same_as_no_number(self, phone):
        """``""`` is refused; ``None`` is how a caller says "no number".

        The opposite of what ``Profile`` does with a blank optional field, and the
        difference is what the value *is*: a profile field is a form entry a person
        may submit empty, and an identifier is a value the store is asked to key
        on. An empty string in a ``UNIQUE`` column would also collide with every
        other one, so the second phone-less account would fail against the first.
        """
        with pytest.raises(InvalidUserPhoneError):
            build(email=None, phone=phone)

    @pytest.mark.parametrize("email", ["", "   "])
    def test_a_blank_address_is_still_not_the_same_as_no_address(self, email):
        """``checked_email`` was not relaxed when the field became optional.

        The conditional in ``__post_init__`` decides *whether* that rule is reached,
        not what it says - which is the distinction between making a field optional
        and weakening a check. A blank address is still refused as blank.
        """
        with pytest.raises(InvalidUserEmailError):
            build(email=email)

    def test_an_account_with_neither_identifier_is_refused(self):
        """The pair rule, and the reason it is an invariant rather than tidiness.

        An account holding neither cannot be logged into, cannot be mailed, cannot
        be texted, and cannot be found by any query this system has - so its owner
        could never prove they own it, and nobody could ever discover it was there.
        """
        with pytest.raises(InvalidUserIdentifierError):
            build(email=None, phone=None)

    def test_a_google_subject_alone_does_not_satisfy_it(self):
        """A subject is an identity Google issued and a login Google performs.

        So an account holding only one is an account this system could never
        deliver a credential to, which is the state the pair rule exists to
        refuse. The subject is therefore not counted as an identifier - which is a
        decision, and this is the test that keeps it from silently becoming one.
        """
        with pytest.raises(InvalidUserIdentifierError):
            build(email=None, phone=None, google_subject="google-subject-123")

    def test_the_pair_rule_runs_after_both_members_are_checked(self):
        """So a bad *supplied* value is never reported as a missing one.

        ``checked_phone`` raises first, and what the caller learns is what is wrong
        with what they typed. If the pair rule ran before the two shape rules, a
        number that folded to nothing would satisfy "at least one identifier" on
        its way to being refused somewhere else entirely - or worse, a value that
        is not a number at all would count as one.
        """
        with pytest.raises(InvalidUserPhoneError):
            build(email=None, phone="()")
