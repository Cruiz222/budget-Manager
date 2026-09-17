import sqlite3
from datetime import datetime
from uuid import uuid4

import pytest

from app.domain.identity.exception import UserNotFoundError
from app.infrastructure.persistence.sqlite_unit_of_work import open_sqlite_connection
from app.infrastructure.repositories.sqlite_user_repository import (
    SqliteUserRepository,
)

MOMENT = datetime(2026, 3, 2, 12, 0)


def build_repository():
    """A user store over its own in-memory database.

    The same shape the wallet repository tests use: ``open_sqlite_connection``
    rather than a raw ``sqlite3.connect``, so these tests run the real schema and
    the real migrations. A user row is the thing every other row's owner is
    checked against, so a store that worked against a hand-built table and not
    against ``SCHEMA`` would be the worst possible thing to have tested.
    """
    return SqliteUserRepository(open_sqlite_connection(":memory:"))


def test_save_and_get_by_id_round_trips_the_user(build_user):
    user = build_user(created_at=MOMENT)
    repository = build_repository()

    repository.save(user)

    stored = repository.get_by_id(user.user_id)
    assert stored.user_id == user.user_id
    assert stored.email == user.email
    assert stored.google_subject is None
    assert stored.created_at == MOMENT


def test_get_by_id_of_a_missing_user_raises(build_user):
    repository = build_repository()
    repository.save(build_user())

    with pytest.raises(UserNotFoundError):
        repository.get_by_id(uuid4())


def test_find_by_email_of_a_missing_address_is_none(build_user):
    """``None`` rather than an exception, because "is this address taken?" is a question.

    The find-or-create path asks it on every CLI invocation, and the answer
    "nobody" is the ordinary one for a new address rather than a failure. An
    exception here would make the caller catch an error to express a branch.
    """
    repository = build_repository()
    repository.save(build_user(email="chinedu@example.com"))

    assert repository.find_by_email("ada@example.com") is None


def test_find_by_email_is_not_fooled_by_a_different_spelling(build_user):
    """The fold, at the point where it is load-bearing.

    ``find_by_email`` cannot obtain the fold by constructing a ``User`` - it looks
    an address up *before* one exists, since deciding whether one exists is the
    question it is asking. So it calls ``fold_email`` directly, and this test is
    what proves the two callers agree. The failure it prevents is a person unable
    to log in to an account that plainly exists, with nothing in the store wrong.
    """
    repository = build_repository()
    repository.save(build_user(email="chinedu@example.com"))

    found = repository.find_by_email("  Chinedu@Example.COM  ")

    assert found is not None
    assert found.email == "chinedu@example.com"


def test_save_overwrites_an_existing_user(build_user):
    user = build_user()
    repository = build_repository()
    repository.save(user)

    user.email = "moved@example.com"
    repository.save(user)

    assert repository.get_by_id(user.user_id).email == "moved@example.com"
    assert repository.find_by_email("test@example.com") is None


def test_find_by_google_subject_returns_the_account_holding_it(build_user):
    repository = build_repository()
    repository.save(build_user(google_subject="114988223156872419036"))

    found = repository.find_by_google_subject("114988223156872419036")

    assert found is not None


def test_a_subject_that_is_not_held_by_anyone_is_none(build_user):
    repository = build_repository()
    repository.save(build_user(google_subject="114988223156872419036"))

    assert repository.find_by_google_subject("999999999999999999999") is None


def test_an_account_with_no_google_identity_is_not_found_by_subject(build_user):
    """The reason the column is nullable rather than defaulted to ``''``.

    SQL's ``=`` is not true of NULL, so a user with no subject simply does not
    match. That is exactly right, and it is the whole argument for ``None`` over
    an empty string: ``''`` *would* match, and every account that arrived without
    a Google identity would be found by the same lookup - the first of them, by
    whatever order the table happened to return.
    """
    repository = build_repository()
    repository.save(build_user(google_subject=None))

    assert repository.find_by_google_subject("") is None


def test_many_accounts_may_have_no_google_subject(build_user):
    """The other half of that argument: ``UNIQUE`` permits any number of NULLs.

    Asserted by writing two of them, because the failure it guards against is a
    constraint violation that would only appear once a second person signed up
    without Google - which is to say, in production and not in a test suite with
    one user in it.

    Two *distinct ids* as well as two addresses: ``save`` upserts on ``user_id``,
    so saving the same id twice would update one row and the test would be
    asserting nothing about the constraint at all. Passing ``uuid4()`` here is
    what makes these two accounts rather than one account edited.
    """
    repository = build_repository()

    repository.save(build_user(user_id=uuid4(), email="first@example.com"))
    repository.save(build_user(user_id=uuid4(), email="second@example.com"))

    assert repository.find_by_email("first@example.com") is not None
    assert repository.find_by_email("second@example.com") is not None


def test_two_accounts_cannot_share_an_address(build_user):
    """The ``UNIQUE`` column as the backstop for a check that is not atomic.

    "Is this address taken?" and the write that takes it are two statements, and
    the find-or-create path is exactly where two callers can interleave between
    them. The fold is what makes the constraint mean what it looks like it means;
    a ``UNIQUE(email)`` without it would happily store both spellings.

    Distinct ids, so the collision that fires is the address and not the key.
    """
    repository = build_repository()
    repository.save(build_user(user_id=uuid4(), email="chinedu@example.com"))

    with pytest.raises(sqlite3.IntegrityError):
        repository.save(build_user(user_id=uuid4(), email="CHINEDU@example.com"))


def test_two_accounts_cannot_share_a_google_subject(build_user):
    """**Two distinct addresses as well as two distinct ids**, and that is a fix.

    Both accounts used to be written with the default ``TEST_USER_EMAIL``, so the
    second save raised on the *address* - and ``pytest.raises(IntegrityError)``
    cannot tell which constraint fired. The test passed, its name was a lie, and
    the ``UNIQUE`` it exists to pin could have been dropped without it noticing.
    That is ``test_many_accounts_may_have_no_google_subject``'s warning about
    ``user_id`` arriving one column over: the identity the upsert keys on is not
    the only thing that has to differ for a constraint test to be about the
    constraint it names.
    """
    repository = build_repository()
    repository.save(
        build_user(
            user_id=uuid4(),
            email="first@example.com",
            google_subject="114988223156872419036",
        )
    )

    with pytest.raises(sqlite3.IntegrityError):
        repository.save(
            build_user(
                user_id=uuid4(),
                email="second@example.com",
                google_subject="114988223156872419036",
            )
        )


def test_save_and_get_by_id_round_trips_a_phone_only_account(build_user):
    """``NULL`` in the address column, and a number in the one beside it.

    The state the whole change exists to permit, asserted at the store rather than
    at the aggregate - because the aggregate accepting it and the store reading it
    back are two different claims, and the second is the one that fails if the
    repository ever writes ``None`` as ``''``.
    """
    account = build_user(email=None, phone="08012345678")
    repository = build_repository()

    repository.save(account)

    stored = repository.get_by_id(account.user_id)
    assert stored.email is None
    assert stored.phone == "2348012345678"


def test_find_by_phone_returns_the_account_holding_it(build_user):
    repository = build_repository()
    repository.save(build_user(phone="08012345678"))

    found = repository.find_by_phone("08012345678")

    assert found is not None
    assert found.phone == "2348012345678"


def test_find_by_phone_is_not_fooled_by_a_different_spelling(build_user):
    """The fold, and it is load-bearing in a way the address fold is not.

    An address is written one way by every machine that touches it. A number is
    written ``08012345678``, ``+2348012345678`` and ``2348012345678`` by *the same
    person on three occasions*, so without one canonical spelling the ``UNIQUE``
    column bounds nothing and a login typed in the wrong form fails against an
    account that plainly exists. ``find_by_phone`` cannot obtain the fold by
    constructing a ``User`` - it is asking whether one exists - so it calls
    ``fold_phone`` directly, and this is what proves the two callers agree.
    """
    repository = build_repository()
    repository.save(build_user(phone="08012345678"))

    for written in ("08012345678", "+2348012345678", "2348012345678", "0801 234 5678"):
        found = repository.find_by_phone(written)
        assert found is not None, written
        assert found.phone == "2348012345678"


def test_a_number_that_is_held_by_nobody_is_none(build_user):
    repository = build_repository()
    repository.save(build_user(phone="08012345678"))

    assert repository.find_by_phone("08099999999") is None


def test_an_account_with_no_number_is_not_found_by_phone(build_user):
    """``NULL`` never matches, for ``find_by_google_subject``'s reason.

    An address-only account is simply not found by any number, which is correct -
    and it is why the column is nullable rather than defaulted to ``''``, which
    *would* match and would find the same account for every lookup.
    """
    repository = build_repository()
    repository.save(build_user(email="chinedu@example.com", phone=None))

    assert repository.find_by_phone("08012345678") is None


def test_find_by_email_answers_none_rather_than_raising_on_none(build_user):
    """The guard, and it is not defensive padding.

    Since an address became optional, ``None`` is a value a caller could plausibly
    reach this method with - "look up the account's address" reads naturally when
    the account may not have one. Handed to ``fold_email`` it raises
    ``AttributeError``, which reports as a 500. An account with no address is found
    by *no* address, so ``None`` is both the correct answer and the one that does
    not crash.
    """
    repository = build_repository()
    repository.save(build_user(phone="08012345678"))

    assert repository.find_by_email(None) is None


def test_find_by_phone_answers_none_rather_than_raising_on_none(build_user):
    repository = build_repository()
    repository.save(build_user(phone="08012345678"))

    assert repository.find_by_phone(None) is None


def test_many_accounts_may_have_no_number(build_user):
    """``UNIQUE`` permits any number of NULLs, asserted the same way as before.

    Written as two accounts rather than one, because the failure it guards against
    is a constraint violation that appears only when the *second* phone-less
    person signs up - which is to say, in production and not in a suite with one
    user in it. This is the test that would have caught ``''`` being written
    instead of ``None``.
    """
    repository = build_repository()

    repository.save(build_user(user_id=uuid4(), email="first@example.com"))
    repository.save(build_user(user_id=uuid4(), email="second@example.com"))

    assert repository.find_by_email("first@example.com") is not None
    assert repository.find_by_email("second@example.com") is not None


def test_two_accounts_cannot_share_a_number(build_user):
    """The ``UNIQUE`` column, and the fold is what makes it mean anything.

    Without the fold the second save would *succeed*: two accounts would hold
    ``08012345678`` and ``+2348012345678``, which are the same number, and no
    query in this system would ever be able to tell that they were one person.
    """
    repository = build_repository()
    repository.save(
        build_user(user_id=uuid4(), email="first@example.com", phone="08012345678")
    )

    with pytest.raises(sqlite3.IntegrityError):
        repository.save(
            build_user(
                user_id=uuid4(),
                email="second@example.com",
                phone="+2348012345678",
            )
        )
