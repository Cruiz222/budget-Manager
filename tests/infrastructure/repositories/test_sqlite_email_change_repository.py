"""The email-change store: an upsert keyed on the account, and a gate that is one statement.

Two operations, and they are opposites in shape, which is why this file is short
next to ``test_sqlite_confirmation_repository.py`` and worth reading beside it:

- ``save`` is an **upsert on ``user_id``**, so asking twice leaves one row and the
  older code dies the instant the newer one commits. That is what makes "a mistyped
  address can be corrected by asking again" true rather than a race - and it is the
  opposite of the confirmations table, where the key belongs to the client and a
  second write under one key is the *same* request arriving twice.
- ``claim_by_token_hash`` is **the gate**: one ``UPDATE`` guarded on the status and
  the window, so the check and the write are the same operation and two answers
  arriving together cannot both succeed. ``ConfirmationRepository.claim``'s idiom,
  for its reason.

Most of the length below is that gate, and specifically its **boundary**: the store
tests ``expires_at > ?`` while the aggregate reads ``as_of >= expires_at`` as
expired. Those are one rule written twice, and the last test is where they are
pinned against each other.

**Nothing is seeded.** A confirmation needs a real wallet and carries a real foreign
key; an email change is about an account, and its ``user_id`` is a plain column like
every other owner column in this schema. So a request for an account that does not
exist is storable, which one test below states rather than leaves to be discovered.
"""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.identity.emailChange import EMAIL_CHANGE_LIFETIME, EmailChange
from app.domain.identity.emailChangeStatus import EmailChangeStatus
from app.domain.identity.exception import (
    EmailChangeAlreadyUsedError,
    EmailChangeExpiredError,
    InvalidEmailChangeTokenError,
)
from app.domain.identity.session import hash_session_token
from app.infrastructure.persistence.sqlite_unit_of_work import open_sqlite_connection
from app.infrastructure.repositories.sqlite_email_change_repository import (
    SqliteEmailChangeRepository,
)

NOON = datetime(2026, 9, 12, 12, 0, 0)


def build_repository() -> SqliteEmailChangeRepository:
    """A request store on a fresh in-memory database.

    Nothing is seeded, and that is the difference this table has from its siblings:
    there is no foreign key to satisfy, because an account's id in a request row is a
    plain column rather than a reference.
    """
    return SqliteEmailChangeRepository(open_sqlite_connection(":memory:"))


def build_change(user_id=None, **overrides) -> tuple:
    """A request **and the code that answers it**, so a test can present what it stored.

    Returned together rather than as two calls, and that is not tidiness: two calls to
    ``build_change`` produce two different requests with two different codes, so a
    test that saved one and presented the other's code would be testing that a code
    nobody stored is refused. Built through ``EmailChange.issue`` for the reason that
    factory exists - the id, the code, its hash and the two moments have to be
    produced together, and the plaintext leaves by exactly one route.
    """
    kwargs = dict(
        user_id=user_id if user_id is not None else uuid4(),
        new_email="ada@example.com",
        now=NOON,
    )
    kwargs.update(overrides)
    return EmailChange.issue(**kwargs)


def spend(repository, token, as_of=NOON) -> EmailChange:
    """Claim the request ``token`` names, which is the only read the port offers.

    ``EmailChangeRepository`` deliberately has no ``find``: a request is written once
    and answered once, and both operations end holding the row they acted on. So even
    the round-trip tests below cost a spend - the store cannot show a test a row
    without spending it, and that is the port's decision rather than a gap in the
    tests.
    """
    return repository.claim_by_token_hash(hash_session_token(token), as_of)


def rows_for(db_path, user_id) -> int:
    """How many requests an account has, counted from a connection of its own.

    Opened independently rather than asked of the store, because the store has no
    method that would answer - and counting the *table* is the stronger assertion
    anyway: it is the rows being counted, not a method's opinion of them.
    """
    connection = open_sqlite_connection(db_path)
    try:
        return connection.execute(
            "SELECT COUNT(*) FROM email_changes WHERE user_id = ?", (str(user_id),)
        ).fetchone()[0]
    finally:
        connection.close()


def test_round_trips_every_column():
    """Read back through the claim, which is the only read there is.

    ``settled_at`` comes back as ``NOON`` rather than as ``None`` because the claim
    that read the row also wrote it - so this asserts the columns rather than the
    status, and the status is asserted separately below.
    """
    user_id = uuid4()
    change, token = build_change(user_id=user_id)
    repository = build_repository()

    assert repository.save(change) is change

    stored = spend(repository, token)
    assert stored.email_change_id == change.email_change_id
    assert stored.user_id == user_id
    assert stored.new_email == "ada@example.com"
    assert stored.token_hash == hash_session_token(token)
    assert stored.requested_at == NOON
    assert stored.expires_at == NOON + EMAIL_CHANGE_LIFETIME
    assert stored.settled_at == NOON


def test_a_spent_request_comes_back_spent():
    """``CONFIRMED`` and its moment, together, from the claim's single ``UPDATE``.

    The aggregate refuses a settled row with no moment and an awaiting row that has
    one, so this pair is a check on that statement rather than on a caller's
    discipline - and the row is rebuilt through the constructor on the way back in,
    so a store that wrote one without the other would fail here rather than travel
    further wearing a valid shape.
    """
    change, token = build_change()
    repository = build_repository()
    repository.save(change)

    stored = spend(repository, token, NOON + timedelta(minutes=4))

    assert stored.status is EmailChangeStatus.CONFIRMED
    assert stored.settled_at == NOON + timedelta(minutes=4)


def test_a_request_for_an_account_that_does_not_exist_is_storable():
    """**The column is not a foreign key, and this is the consequence stated.**

    Every owner column in this schema is a plain ``user_id`` rather than a
    ``REFERENCES users(user_id)``, and here that matters in a way it does not
    elsewhere: an account is deleted nowhere in this system, so the constraint would
    never fire - while a table that could not be written without first seeding an
    account is a table whose every test starts with two fixtures instead of one.

    This is the assertion that would fail if somebody tidied the schema by adding
    the reference.
    """
    change, token = build_change(user_id=uuid4())
    repository = build_repository()

    repository.save(change)

    assert spend(repository, token).user_id == change.user_id


class TestSavingReplacesRatherThanAdds:
    """One pending request per account, and the key is what makes it structural."""

    def test_asking_twice_leaves_one_row(self, tmp_path):
        """Counted from an independent connection, which is what makes it a count.

        The observable version of this - that the first code stops working - is the
        test below, and it would also pass for a store that kept both rows and only
        ever looked at the newest. Counting the table is the assertion that rules
        that out.
        """
        db_path = str(tmp_path / "changes.db")
        repository = SqliteEmailChangeRepository(open_sqlite_connection(db_path))
        user_id = uuid4()
        first, _ = build_change(user_id=user_id, new_email="ada@example.com")
        second, _ = build_change(user_id=user_id, new_email="grace@example.com")

        repository.save(first)
        repository.save(second)

        assert rows_for(db_path, user_id) == 1

    def test_the_second_code_works_and_the_first_does_not(self):
        """Both halves, because either alone would pass for a store that ignored one.

        Asserting only that the new code works would pass for a store that kept both
        rows; asserting only that the old one fails would pass for a store that wrote
        nothing at all.
        """
        user_id = uuid4()
        first, first_code = build_change(user_id=user_id, new_email="ada@example.com")
        second, second_code = build_change(user_id=user_id, new_email="grace@example.com")
        repository = build_repository()
        repository.save(first)
        repository.save(second)

        with pytest.raises(InvalidEmailChangeTokenError):
            spend(repository, first_code)

        assert spend(repository, second_code).new_email == "grace@example.com"

    def test_the_replaced_code_looks_like_one_that_never_existed(self):
        """Not like an expired one, which is the honest report.

        The row the first code named is *gone*, so the fall-through in ``_refusal``
        finds nothing and answers with the unknown-token error. That is also exactly
        what a guess produces, so somebody holding a stale code cannot learn from the
        refusal that it was ever real - which is worth having, since holding the code
        is the entire authorisation to move an account.
        """
        user_id = uuid4()
        first, first_code = build_change(user_id=user_id, new_email="ada@example.com")
        second, _ = build_change(user_id=user_id, new_email="grace@example.com")
        repository = build_repository()
        repository.save(first)
        repository.save(second)

        with pytest.raises(InvalidEmailChangeTokenError) as refused:
            spend(repository, first_code)

        assert not isinstance(refused.value, EmailChangeExpiredError)

    def test_a_spent_request_does_not_block_a_new_one(self):
        """Asking again after a change has been answered is a real case, not an edge.

        ``save`` writes whatever the aggregate holds, and every new request comes from
        ``issue``, which always produces ``AWAITING``. So a spent row is replaced by a
        live one, and nobody is locked out of moving again by having moved once.
        """
        user_id = uuid4()
        first, first_code = build_change(user_id=user_id, new_email="ada@example.com")
        repository = build_repository()
        repository.save(first)
        spend(repository, first_code)

        second, second_code = build_change(
            user_id=user_id,
            new_email="grace@example.com",
            now=NOON + timedelta(minutes=1),
        )
        repository.save(second)

        claimed = spend(repository, second_code, NOON + timedelta(minutes=2))
        assert claimed.new_email == "grace@example.com"
        assert claimed.status is EmailChangeStatus.CONFIRMED

    def test_the_row_moves_entirely_including_its_own_id(self):
        """The newer request is a *different request*, not a correction to the old one.

        So every column is overwritten rather than merged - and the id is the column
        it would be tempting to keep, since keeping it would make the account's
        request "the same row, updated". It would also throw away the only record of
        what was asked when.
        """
        user_id = uuid4()
        first, _ = build_change(user_id=user_id, new_email="ada@example.com")
        second, second_code = build_change(
            user_id=user_id, new_email="grace@example.com", now=NOON + timedelta(hours=1)
        )
        repository = build_repository()
        repository.save(first)
        repository.save(second)

        stored = spend(repository, second_code, NOON + timedelta(hours=1))

        assert stored.email_change_id == second.email_change_id
        assert stored.email_change_id != first.email_change_id
        assert stored.requested_at == NOON + timedelta(hours=1)
        assert stored.expires_at == NOON + timedelta(hours=1) + EMAIL_CHANGE_LIFETIME

    def test_one_accounts_request_is_not_anothers(self):
        """The key is the account, and this is the control that says so.

        A store that keyed on something global - or ignored the conflict clause and
        overwrote the single row - would pass every test above in a database with one
        account in it. Two accounts, two requests, both alive.
        """
        first_user = uuid4()
        second_user = uuid4()
        first, first_code = build_change(user_id=first_user, new_email="ada@example.com")
        second, second_code = build_change(
            user_id=second_user, new_email="grace@example.com"
        )
        repository = build_repository()
        repository.save(first)
        repository.save(second)

        assert spend(repository, first_code).user_id == first_user
        assert spend(repository, second_code).user_id == second_user


class TestTheClaimIsTheGate:
    """One statement, so two answers arriving together cannot both succeed."""

    def test_it_spends_the_request_and_returns_it_confirmed(self):
        """Both halves matter, for the reason the confirmation suite gives.

        The object it returns is what the service reads the new address off; the stored
        row is what stops a second answer. A claim that returned a spent object without
        writing would move the account once and allow it again.
        """
        change, token = build_change()
        repository = build_repository()
        repository.save(change)

        claimed = spend(repository, token)

        assert claimed.status is EmailChangeStatus.CONFIRMED
        assert claimed.new_email == "ada@example.com"
        with pytest.raises(EmailChangeAlreadyUsedError):
            spend(repository, token, NOON + timedelta(seconds=1))

    def test_it_records_the_moment_it_was_answered_with(self):
        """``as_of`` is what gets written, so the row says when it was answered.

        The same instant the window was tested against - so a request cannot be
        recorded as answered at a time it was not allowed to be answered at.
        """
        change, token = build_change()
        repository = build_repository()
        repository.save(change)
        answered_at = NOON + timedelta(minutes=7)

        claimed = spend(repository, token, answered_at)

        assert claimed.settled_at == answered_at
        assert claimed.requested_at == NOON

    def test_answering_twice_is_refused(self):
        """The gate. ``AWAITING`` in the ``WHERE`` is what does it.

        The second claim matches no row - not because anything looked at the status and
        decided, but because the condition is false. That is the difference between
        this and a check-then-write, and it is the difference two concurrent answers
        would expose.
        """
        change, token = build_change()
        repository = build_repository()
        repository.save(change)
        spend(repository, token)

        with pytest.raises(EmailChangeAlreadyUsedError):
            spend(repository, token)

    def test_answering_past_the_window_is_refused(self):
        change, token = build_change()
        repository = build_repository()
        repository.save(change)

        with pytest.raises(EmailChangeExpiredError):
            spend(repository, token, NOON + EMAIL_CHANGE_LIFETIME)

    def test_a_refused_claim_leaves_the_request_spendable(self):
        """A refusal is a refusal, not a side effect.

        An ``UPDATE`` that matched no row because of the *window* changed nothing, so
        the same request is still there to be answered at a moment the window allows -
        and this is what says the expired branch is not quietly spending what it
        refused.
        """
        change, token = build_change()
        repository = build_repository()
        repository.save(change)

        with pytest.raises(EmailChangeExpiredError):
            spend(repository, token, NOON + EMAIL_CHANGE_LIFETIME)

        claimed = spend(repository, token, NOON + EMAIL_CHANGE_LIFETIME - timedelta(seconds=1))
        assert claimed.status is EmailChangeStatus.CONFIRMED

    def test_a_token_that_names_nothing_is_refused(self):
        with pytest.raises(InvalidEmailChangeTokenError):
            build_repository().claim_by_token_hash(hash_session_token("never-minted"), NOON)

    def test_the_unknown_refusal_says_nothing_at_all(self):
        """**Bare, and the emptiness is the point rather than laziness.**

        A message naming the token hash would make this answer differ from the same
        answer about a hash that names nothing - and the entire authorisation for
        moving an account is holding the code, so an oracle here would tell a guesser
        which of their guesses was real. ``errors._detail`` turns an empty message into
        the class name, so a client still gets something to read.

        Asserted because this is exactly what a helpful-looking ``f"no request for
        {token_hash}"`` would remove, and nothing else in the suite would notice.
        """
        with pytest.raises(InvalidEmailChangeTokenError) as refused:
            build_repository().claim_by_token_hash(hash_session_token("never-minted"), NOON)

        assert str(refused.value) == ""

    def test_a_raw_token_is_not_a_hash_and_matches_nothing(self):
        """**This method takes a hash, and that is a contract rather than a hint.**

        The property that makes a plain SHA-256 sufficient is that a stored value is
        never a usable credential - and this is where that property is *produced*: the
        store hashes nothing, so a caller that hands it whatever arrived on the wire is
        handing it the wrong value, and the claim matches no row.

        Asserted here so the layering is explicit rather than discovered: the hashing
        is the *caller's* job, and the caller that must do it is ``ConfirmEmailChange``,
        which hashes a presented code before it gets here. The complementary test - that
        presenting the stored hash is useless - belongs to that use case, because that
        is the layer where the hash is applied and therefore the layer where the claim
        is true. ``tests/application/identity/test_confirm_email_change.py`` carries it.
        """
        change, token = build_change()
        repository = build_repository()
        repository.save(change)

        with pytest.raises(InvalidEmailChangeTokenError):
            repository.claim_by_token_hash(token, NOON)


def test_the_window_closes_at_the_instant_it_closes():
    """**The boundary, pinned against ``EmailChange.is_expired``.**

    ``expires_at > ?`` here and ``as_of >= expires_at`` in the aggregate are one rule
    written twice, and they must agree - or the two would disagree about the last
    instant of a request's life, in the direction of a code that moves an account
    working for one moment longer than it was mailed for.

    So both moments either side of the boundary are spent, and the aggregate's answer
    at each is asserted beside them. A change to either comparison that was not
    mirrored fails one of these four assertions.
    """
    expired, expired_code = build_change(new_email="ada@example.com")
    live, live_code = build_change(new_email="grace@example.com")
    repository = build_repository()
    repository.save(expired)
    repository.save(live)
    just_inside = NOON + EMAIL_CHANGE_LIFETIME - timedelta(microseconds=1)

    with pytest.raises(EmailChangeExpiredError):
        spend(repository, expired_code, NOON + EMAIL_CHANGE_LIFETIME)

    claimed = spend(repository, live_code, just_inside)

    assert claimed.status is EmailChangeStatus.CONFIRMED
    assert expired.is_expired(NOON + EMAIL_CHANGE_LIFETIME) is True
    assert live.is_expired(just_inside) is False
