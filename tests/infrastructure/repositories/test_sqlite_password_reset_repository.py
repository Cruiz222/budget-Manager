"""The reset store: an upsert keyed on the account, and a gate that is one statement.

Two operations, and they are the same two ``SqliteEmailChangeRepository`` has - the
upsert on ``user_id`` and the single-statement claim - because the two tables share
one lifecycle. What this file adds to its sibling is the *payload*'s absence, and
that shows up as much in what is missing here as in what is asserted: there is no
column to round-trip beyond the seven, and one test below says so.

Most of the length is the gate, and specifically its **boundary**: the store tests
``expires_at > ?`` while the aggregate reads ``as_of >= expires_at`` as expired.
Those are one rule written twice, and the last test pins them against each other.

**Nothing is seeded.** An email change is about an account and its ``user_id`` is a
plain column like every other owner column in this schema - so a request for an
account that does not exist is storable, which one test states rather than leaves to
be discovered. The same holds here, and it is worth stating separately because this
row authorises something more consequential: a code for an account that does not
exist still stores, and ``ConfirmPasswordReset`` is the layer that refuses it.
"""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.identity.exception import (
    InvalidPasswordResetTokenError,
    PasswordResetAlreadyUsedError,
    PasswordResetExpiredError,
)
from app.domain.identity.passwordReset import PASSWORD_RESET_LIFETIME, PasswordReset
from app.domain.identity.passwordResetStatus import PasswordResetStatus
from app.domain.identity.session import hash_session_token
from app.infrastructure.persistence.sqlite_unit_of_work import open_sqlite_connection
from app.infrastructure.repositories.sqlite_password_reset_repository import (
    SqlitePasswordResetRepository,
)

NOON = datetime(2026, 9, 12, 12, 0, 0)


def build_repository() -> SqlitePasswordResetRepository:
    """A reset store on a fresh in-memory database.

    Nothing is seeded, for the reason the sibling file gives: there is no foreign
    key to satisfy, because an account's id in a request row is a plain column
    rather than a reference.
    """
    return SqlitePasswordResetRepository(open_sqlite_connection(":memory:"))


def build_reset(user_id=None, **overrides) -> tuple:
    """A request **and the code that answers it**, so a test can present what it stored.

    Returned together rather than as two calls, and that is not tidiness: two calls to
    ``build_reset`` produce two different requests with two different codes, so a test
    that saved one and presented the other's code would be testing that a code nobody
    stored is refused. Built through ``PasswordReset.issue`` for the reason that
    factory exists - the id, the code, its hash and the two moments have to be produced
    together, and the plaintext leaves by exactly one route.
    """
    kwargs = dict(
        user_id=user_id if user_id is not None else uuid4(),
        now=NOON,
    )
    kwargs.update(overrides)
    return PasswordReset.issue(**kwargs)


def spend(repository, token, as_of=NOON) -> PasswordReset:
    """Claim the reset ``token`` names, which is the only read the port offers.

    ``PasswordResetRepository`` deliberately has no ``find``: a request is written once
    and answered once, and both operations end holding the row they acted on. So even
    the round-trip tests below cost a spend - the store cannot show a test a row
    without spending it, and that is the port's decision rather than a gap in the
    tests. It is the same shape ``test_sqlite_email_change_repository.py`` has, and it
    is more than a convenience: there is no method here that could leak a row somebody
    has not proved they hold.
    """
    return repository.claim_by_token_hash(hash_session_token(token), as_of)


def rows_for(db_path, user_id) -> int:
    """How many resets an account has, counted from a connection of its own.

    Opened independently rather than asked of the store, because the store has no
    method that would answer - and counting the *table* is the stronger assertion
    anyway: it is the rows being counted, not a method's opinion of them.
    """
    connection = open_sqlite_connection(db_path)
    try:
        return connection.execute(
            "SELECT COUNT(*) FROM password_resets WHERE user_id = ?", (str(user_id),)
        ).fetchone()[0]
    finally:
        connection.close()


def test_round_trips_every_column():
    """Read back through the claim, which is the only read there is.

    Seven columns and no eighth, which is the whole difference from the sibling
    table: ``settled_at`` comes back as ``NOON`` rather than as ``None`` because the
    claim that read the row also wrote it, and there is nothing else to check
    because there is no payload. The absence is asserted rather than assumed - a
    store that carried a password across would have to have a column, and the
    repository's ``_values`` is one element shorter than the email-change one's for
    exactly this reason.
    """
    user_id = uuid4()
    reset, token = build_reset(user_id=user_id)
    repository = build_repository()

    assert repository.save(reset) is reset

    stored = spend(repository, token)
    assert stored.password_reset_id == reset.password_reset_id
    assert stored.user_id == user_id
    assert stored.token_hash == hash_session_token(token)
    assert stored.requested_at == NOON
    assert stored.expires_at == NOON + PASSWORD_RESET_LIFETIME
    assert stored.settled_at == NOON


def test_the_table_has_no_column_a_password_could_travel_in():
    """**The schema, asserted where somebody would change it.**

    A password is written down exactly once in this system, as an argon2 hash in
    ``password_credentials``, and this table is the one an implementer would be
    tempted to add a second copy to - "the value is decided at confirm time, so why
    not write it at request time like the address is". The answer is argued on
    ``PasswordReset``; this is the assertion that makes that argument enforceable
    rather than advisory.

    It reads the real DDL from ``PRAGMA table_info`` rather than a copy of it, so a
    column added to the schema is caught here instead of in review.
    """
    repository = build_repository()
    columns = {
        row["name"]
        for row in repository._connection.execute("PRAGMA table_info(password_resets)")
    }

    assert columns == {
        "password_reset_id",
        "user_id",
        "token_hash",
        "status",
        "requested_at",
        "expires_at",
        "settled_at",
    }


def test_a_reset_for_an_account_that_does_not_exist_is_storable():
    """**The column is not a foreign key, and this is the consequence stated.**

    Every owner column in this schema is a plain ``user_id`` rather than a reference,
    so a reset can be recorded for an account that has been deleted. That is
    deliberate: the refusal belongs at the layer that can *say* something about it,
    and ``ConfirmPasswordReset`` reports it as the code's own refusal rather than as a
    missing user - so that a caller cannot learn from the answer whether the account
    still exists.

    Worth stating for this table more than for its sibling, because a code here
    replaces a password. A store that refused the row would move the refusal down a
    layer, where it could not be given the same words as a code that means nothing.
    """
    reset, token = build_reset(user_id=uuid4())
    repository = build_repository()

    repository.save(reset)

    assert spend(repository, token).status is PasswordResetStatus.CONFIRMED


def test_a_spent_reset_comes_back_spent():
    """``CONFIRMED`` and its moment, together, from the claim's single ``UPDATE``.

    The aggregate refuses a settled row with no moment and an awaiting row that has
    one, so this pair is a check on that statement rather than on a caller's
    discipline - and the row is rebuilt through the constructor on the way back in, so
    a store that wrote one without the other would fail here rather than travel
    further wearing a valid shape.
    """
    reset, token = build_reset()
    repository = build_repository()
    repository.save(reset)

    stored = spend(repository, token, NOON + timedelta(minutes=4))

    assert stored.status is PasswordResetStatus.CONFIRMED
    assert stored.settled_at == NOON + timedelta(minutes=4)


class TestSavingReplacesRatherThanAdds:
    """One pending reset per account, and the key is what makes it structural."""

    def test_asking_twice_leaves_one_row(self, tmp_path):
        """Counted from an independent connection, which is what makes it a count.

        The observable version of this - that the first code stops working - is the
        test below, and it would also pass for a store that kept both rows and only
        ever looked at the newest. Counting the table is the assertion that rules that
        out, and it is the one that matters most in this flow: two live reset codes
        for one account would be two independent ways in, with only the newer one
        superseding the older by accident of lookup.
        """
        db_path = str(tmp_path / "resets.db")
        repository = SqlitePasswordResetRepository(open_sqlite_connection(db_path))
        user_id = uuid4()
        first, _ = build_reset(user_id=user_id)
        second, _ = build_reset(user_id=user_id)

        repository.save(first)
        repository.save(second)

        assert rows_for(db_path, user_id) == 1

    def test_the_second_code_works_and_the_first_does_not(self):
        """Both halves, because either alone would pass for a store that ignored one.

        Asserting only that the new code works would pass for a store that kept both
        rows; asserting only that the old one fails would pass for a store that wrote
        nothing at all. This is the remedy for a mail that never arrived, stated as a
        behaviour: ask again, and the first code is dead before the second is sent.
        """
        user_id = uuid4()
        first, first_code = build_reset(user_id=user_id)
        second, second_code = build_reset(user_id=user_id)
        repository = build_repository()
        repository.save(first)
        repository.save(second)

        with pytest.raises(InvalidPasswordResetTokenError):
            spend(repository, first_code)

        assert spend(repository, second_code).user_id == user_id

    def test_the_replaced_code_looks_like_one_that_never_existed(self):
        """Not like an expired one, which is the honest report.

        The row the first code named is *gone*, so the fall-through in ``_refusal``
        finds nothing and answers with the unknown-token error. That is also exactly
        what a guess produces, so somebody holding a stale code cannot learn from the
        refusal that it was ever real - which is worth having, since holding the code
        is the entire authorisation to replace a password.
        """
        user_id = uuid4()
        first, first_code = build_reset(user_id=user_id)
        second, _ = build_reset(user_id=user_id)
        repository = build_repository()
        repository.save(first)
        repository.save(second)

        with pytest.raises(InvalidPasswordResetTokenError) as refused:
            spend(repository, first_code)

        assert not isinstance(refused.value, PasswordResetExpiredError)

    def test_a_spent_reset_does_not_block_a_new_one(self):
        """Asking again after a reset has been answered is a real case, not an edge.

        Somebody resets a password, forgets the new one, and resets again - which is
        the second-most-likely thing to happen to this feature after the first reset.
        ``save`` writes whatever the aggregate holds, and every new request comes from
        ``issue``, which always produces ``AWAITING``. So a spent row is replaced by a
        live one, and nobody is locked out of resetting by having reset once.
        """
        user_id = uuid4()
        first, first_code = build_reset(user_id=user_id)
        repository = build_repository()
        repository.save(first)
        spend(repository, first_code)

        second, second_code = build_reset(user_id=user_id, now=NOON + timedelta(minutes=1))
        repository.save(second)

        claimed = spend(repository, second_code, NOON + timedelta(minutes=2))
        assert claimed.user_id == user_id
        assert claimed.status is PasswordResetStatus.CONFIRMED

    def test_the_row_moves_entirely_including_its_own_id(self):
        """The newer request is a *different request*, not a correction to the old one.

        So every column is overwritten rather than merged - and the id is the column
        it would be tempting to keep, since keeping it would make the account's
        request "the same row, updated". It would also throw away the only record of
        what was asked when, which in this flow is the record of when somebody who may
        not have been the owner asked for a way in.
        """
        user_id = uuid4()
        first, _ = build_reset(user_id=user_id)
        second, second_code = build_reset(user_id=user_id, now=NOON + timedelta(hours=1))
        repository = build_repository()
        repository.save(first)
        repository.save(second)

        stored = spend(repository, second_code, NOON + timedelta(hours=1))

        assert stored.password_reset_id == second.password_reset_id
        assert stored.password_reset_id != first.password_reset_id
        assert stored.requested_at == NOON + timedelta(hours=1)
        assert stored.expires_at == NOON + timedelta(hours=1) + PASSWORD_RESET_LIFETIME

    def test_one_accounts_reset_is_not_anothers(self):
        """The key is the account, and this is the control that says so.

        A store that keyed on something global - or ignored the conflict clause and
        overwrote the single row - would pass every test above in a database with one
        account in it. Two accounts, two requests, both alive. This is the test that
        matters most for a table keyed this way: the failure it rules out is one
        account's request superseding another's, which would hand a stranger's reset
        code to the wrong person's account.
        """
        first_user = uuid4()
        second_user = uuid4()
        first, first_code = build_reset(user_id=first_user)
        second, second_code = build_reset(user_id=second_user)
        repository = build_repository()
        repository.save(first)
        repository.save(second)

        assert spend(repository, first_code).user_id == first_user
        assert spend(repository, second_code).user_id == second_user


class TestTheClaimIsTheGate:
    """One statement, so two answers arriving together cannot both succeed."""

    def test_it_spends_the_request_and_returns_it_confirmed(self):
        """Both halves matter, for the reason the confirmation suite gives.

        The object it returns is what the service reads the account id off, and the
        stored row is what stops a second answer. A claim that returned a spent object
        without writing would replace the password once and allow it again - which in
        this flow is a code that can set a password twice.
        """
        reset, token = build_reset()
        repository = build_repository()
        repository.save(reset)

        claimed = spend(repository, token)

        assert claimed.status is PasswordResetStatus.CONFIRMED
        assert claimed.user_id == reset.user_id
        with pytest.raises(PasswordResetAlreadyUsedError):
            spend(repository, token, NOON + timedelta(seconds=1))

    def test_it_records_the_moment_it_was_answered_with(self):
        """``as_of`` is what gets written, so the row says when it was answered.

        The same instant the window was tested against - so a request cannot be
        recorded as answered at a time it was not allowed to be answered at. That
        moment is what ``password_changed_notice`` prints, so a wrong one here is a
        wrong sentence in a mail that is meant to tell somebody when their password
        changed.
        """
        reset, token = build_reset()
        repository = build_repository()
        repository.save(reset)
        answered_at = NOON + timedelta(minutes=7)

        claimed = spend(repository, token, answered_at)

        assert claimed.settled_at == answered_at
        assert claimed.requested_at == NOON

    def test_answering_twice_is_refused(self):
        """The gate. ``AWAITING`` in the ``WHERE`` is what does it.

        The second claim matches no row - not because anything looked at the status
        and decided, but because the condition is false. That is the difference
        between this and a check-then-write, and it is the difference two concurrent
        answers would expose: with a check-then-write, two confirms arriving in the
        same instant would both see ``AWAITING`` and both replace the password, the
        second silently overwriting the first.
        """
        reset, token = build_reset()
        repository = build_repository()
        repository.save(reset)
        spend(repository, token)

        with pytest.raises(PasswordResetAlreadyUsedError):
            spend(repository, token)

    def test_answering_past_the_window_is_refused(self):
        reset, token = build_reset()
        repository = build_repository()
        repository.save(reset)

        with pytest.raises(PasswordResetExpiredError):
            spend(repository, token, NOON + PASSWORD_RESET_LIFETIME)

    def test_a_refused_claim_leaves_the_request_spendable(self):
        """A refusal is a refusal, not a side effect.

        An ``UPDATE`` that matched no row because of the *window* changed nothing, so
        the same request is still there to be answered at a moment the window allows -
        and this is what says the expired branch is not quietly spending what it
        refused. In this flow the consequence is concrete: somebody whose clock was
        wrong, or who opened the mail a second too early, is not left with a dead code
        on account of having tried.
        """
        reset, token = build_reset()
        repository = build_repository()
        repository.save(reset)

        with pytest.raises(PasswordResetExpiredError):
            spend(repository, token, NOON + PASSWORD_RESET_LIFETIME)

        claimed = spend(
            repository, token, NOON + PASSWORD_RESET_LIFETIME - timedelta(seconds=1)
        )
        assert claimed.status is PasswordResetStatus.CONFIRMED

    def test_a_code_that_names_nothing_is_refused(self):
        with pytest.raises(InvalidPasswordResetTokenError):
            build_repository().claim_by_token_hash(hash_session_token("never-minted"), NOON)

    def test_the_unknown_refusal_says_nothing_at_all(self):
        """**Bare, and the emptiness is the point rather than laziness.**

        A message naming the token hash would make this answer differ from the same
        answer about a hash that names nothing - and the entire authorisation for
        replacing a password is holding the code, so an oracle here would tell a
        guesser which of their guesses was real. ``errors._detail`` turns an empty
        message into the class name, so a client still gets something to read.

        Asserted because this is exactly what a helpful-looking ``f"no reset for
        {token_hash}"`` would remove, and nothing else in the suite would notice.
        """
        with pytest.raises(InvalidPasswordResetTokenError) as refused:
            build_repository().claim_by_token_hash(hash_session_token("never-minted"), NOON)

        assert str(refused.value) == ""

    def test_a_raw_token_is_not_a_hash_and_matches_nothing(self):
        """**This method takes a hash, and that is a contract rather than a hint.**

        The property that makes a plain SHA-256 sufficient is that a stored value is
        never a usable credential - and this is where that property is *produced*: the
        store hashes nothing, so a caller that hands it whatever arrived on the wire is
        handing it the wrong value, and the claim matches no row.

        Asserted here so the layering is explicit rather than discovered: the hashing
        is the *caller's* job, and the caller that must do it is
        ``ConfirmPasswordReset``, which hashes a presented code before it gets here.
        The complementary test - that presenting the stored hash is useless - belongs to
        that use case, because that is the layer where the hash is applied and therefore
        the layer where the claim is true.
        ``tests/application/identity/test_confirm_password_reset.py`` carries it.
        """
        reset, token = build_reset()
        repository = build_repository()
        repository.save(reset)

        with pytest.raises(InvalidPasswordResetTokenError):
            repository.claim_by_token_hash(token, NOON)

    def test_the_claim_reads_no_column_a_request_supplies(self):
        """**The account is the one the row names, and no argument can widen that.**

        Two accounts, one code, and a claim that is handed the *other* account's id
        nowhere - because the claim takes nothing but a hash and a moment. This is the
        signature that guarantees it, and it is why the port's two parameters are both
        values the store derives rather than values a caller chooses.

        Stated as a test because the tempting alternative - a claim that also took a
        ``user_id`` so the use case could pass "whoever is asking" - would be a way to
        name somebody else's account, and it would look like a tidier interface.
        """
        mine = uuid4()
        theirs = uuid4()
        repository = build_repository()
        mine_reset, mine_code = build_reset(user_id=mine)
        theirs_reset, theirs_code = build_reset(user_id=theirs)
        repository.save(mine_reset)
        repository.save(theirs_reset)

        claimed = spend(repository, mine_code)

        assert claimed.user_id == mine
        assert claimed.password_reset_id == mine_reset.password_reset_id
        assert claimed.password_reset_id != theirs_reset.password_reset_id
        # And the other one is untouched, still live and still nobody's but its own.
        assert spend(repository, theirs_code).user_id == theirs


def test_the_window_closes_at_the_instant_it_closes():
    """**The boundary, pinned against ``PasswordReset.is_expired``.**

    ``expires_at > ?`` here and ``as_of >= expires_at`` in the aggregate are one rule
    written twice, and they must agree - or the two would disagree about the last
    instant of a request's life. The direction of that disagreement is worth naming in
    this flow: a code that replaces an account's password, working for one moment
    longer than it was mailed for.

    So both moments either side of the boundary are spent, and the aggregate's answer
    at each is asserted beside them. A change to either comparison that was not
    mirrored fails one of these four assertions.
    """
    expired, expired_code = build_reset()
    live, live_code = build_reset()
    repository = build_repository()
    repository.save(expired)
    repository.save(live)
    just_inside = NOON + PASSWORD_RESET_LIFETIME - timedelta(microseconds=1)

    with pytest.raises(PasswordResetExpiredError):
        spend(repository, expired_code, NOON + PASSWORD_RESET_LIFETIME)

    claimed = spend(repository, live_code, just_inside)

    assert claimed.status is PasswordResetStatus.CONFIRMED
    assert expired.is_expired(NOON + PASSWORD_RESET_LIFETIME) is True
    assert live.is_expired(just_inside) is False
