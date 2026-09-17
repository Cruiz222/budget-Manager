"""The phone-verification store: an upsert keyed on the number, and a one-statement gate.

Two operations, and they are the same two ``SqlitePasswordResetRepository`` has - the
upsert and the single-statement claim - because the two tables share one lifecycle. What
this file adds to its sibling is the *key*, and that shows up as much in what is asserted
as in what is not: there is no account anywhere on the row, one test says the column is
not there, and another says why a ``UNIQUE`` slot on a number nobody has claimed yet is a
cost the flow deliberately pays.

Most of the length is the gate, and specifically its **boundary**: the store tests
``expires_at > ?`` while the aggregate reads ``as_of >= expires_at`` as expired. Those are
one rule written twice, and the last test pins them against each other.

**Nothing is seeded.** There is no foreign key to satisfy anywhere - an account's id is a
plain column everywhere else in this schema, and here there is not even one of those - so
a verification for a number is storable with no account in existence, which is the normal
case rather than an edge. One test states that, because it is the whole point of the table.
"""

from datetime import datetime, timedelta

import pytest

from app.domain.identity.exception import (
    InvalidPhoneVerificationTokenError,
    PhoneVerificationAlreadyUsedError,
    PhoneVerificationExpiredError,
)
from app.domain.identity.phoneVerification import (
    PHONE_VERIFICATION_LIFETIME,
    PhoneVerification,
)
from app.domain.identity.phoneVerificationStatus import PhoneVerificationStatus
from app.domain.identity.session import hash_session_token
from app.infrastructure.persistence.sqlite_unit_of_work import open_sqlite_connection
from app.infrastructure.repositories.sqlite_phone_verification_repository import (
    SqlitePhoneVerificationRepository,
)

NOON = datetime(2026, 9, 12, 12, 0, 0)

#: Two spellings of one handset, and the one value they both fold to.
A_NATIONAL_NUMBER = "08012345678"
AN_INTERNATIONAL_NUMBER = "+2348012345678"
A_FOLDED_NUMBER = "2348012345678"

#: A second, different handset - the control every "one row per number" test needs.
#:
#: **The two spellings are written out as a pair rather than derived one from the other,
#: and this pair exists because leaving it out was a real bug in this file.** The store
#: folds on the way in, so a test that types the national form and then asserts the row
#: holds the national form is asserting that the fold did not happen - and the mistake is
#: invisible in any assertion that happens to use the already-folded spelling, which is
#: most of them. Naming both makes it obvious which side of the wire each one is on:
#: whatever a person types goes in the left one, and whatever the column holds is the
#: right one.
ANOTHER_NUMBER = "08098765432"
ANOTHER_FOLDED_NUMBER = "2348098765432"


def build_repository() -> SqlitePhoneVerificationRepository:
    """A verification store on a fresh in-memory database.

    Nothing is seeded, for the reason the module docstring gives: there is no foreign key
    to satisfy and no account to exist first.
    """
    return SqlitePhoneVerificationRepository(open_sqlite_connection(":memory:"))


def build_verification(phone=None, **overrides) -> tuple:
    """A request **and the code that answers it**, so a test can present what it stored.

    Returned together rather than as two calls, and that is not tidiness: two calls to
    ``build_verification`` produce two different requests with two different codes, so a
    test that saved one and presented the other's code would be testing that a code nobody
    stored is refused. Built through ``PhoneVerification.issue`` for the reason that
    factory exists - the id, the code, its hash and the two moments have to be produced
    together, and the plaintext leaves by exactly one route.
    """
    kwargs = dict(
        phone=phone if phone is not None else A_FOLDED_NUMBER,
        now=NOON,
    )
    kwargs.update(overrides)
    return PhoneVerification.issue(**kwargs)


def spend(repository, token, as_of=NOON) -> PhoneVerification:
    """Claim the verification ``token`` names, which is the only read the port offers.

    ``PhoneVerificationRepository`` deliberately has no ``find``: a request is written once
    and answered once, and both operations end holding the row they acted on. So even the
    round-trip tests below cost a spend - the store cannot show a test a row without
    spending it, and that is the port's decision rather than a gap in the tests. There is
    no method here that could leak a row somebody has not proved they hold, which for this
    table means no method that could hand out a number nobody has demonstrated they hold.
    """
    return repository.claim_by_token_hash(hash_session_token(token), as_of)


def rows_for(db_path, phone) -> int:
    """How many verifications a number has, counted from a connection of its own.

    Opened independently rather than asked of the store, because the store has no method
    that would answer - and counting the *table* is the stronger assertion anyway: it is
    the rows being counted, not a method's opinion of them.
    """
    connection = open_sqlite_connection(db_path)
    try:
        return connection.execute(
            "SELECT COUNT(*) FROM phone_verifications WHERE phone = ?", (phone,)
        ).fetchone()[0]
    finally:
        connection.close()


def test_round_trips_every_column():
    """Read back through the claim, which is the only read there is.

    Seven columns and no eighth: ``settled_at`` comes back as ``NOON`` rather than as
    ``None`` because the claim that read the row also wrote it, and there is nothing else
    to check because there is no payload. The absence is asserted rather than assumed - a
    store that carried an account id across would have to have a column, and there is
    deliberately none to carry.
    """
    verification, token = build_verification()
    repository = build_repository()

    assert repository.save(verification) is verification

    stored = spend(repository, token)
    assert stored.phone_verification_id == verification.phone_verification_id
    assert stored.phone == A_FOLDED_NUMBER
    assert stored.token_hash == hash_session_token(token)
    assert stored.requested_at == NOON
    assert stored.expires_at == NOON + PHONE_VERIFICATION_LIFETIME
    assert stored.settled_at == NOON


def test_the_table_has_no_column_an_account_could_travel_in():
    """**The schema, asserted where somebody would change it.**

    A ``user_id`` on this table would read as natural - every other request table in this
    schema has one - and it would be wrong in a way that is invisible until it matters.
    The account is what answering this request *creates*, so a row written before the
    answer has no account to name: a nullable column would always hold ``NULL`` and would
    read as an account that was deleted, and a non-nullable one could not be written at
    all.

    The absence of a number-shaped payload column is the second half of the same point:
    the number is the *key* here, so there is nothing being moved and nothing to carry.

    It reads the real DDL from ``PRAGMA table_info`` rather than a copy of it, so a column
    added to the schema is caught here instead of in review.
    """
    repository = build_repository()
    columns = {
        row["name"]
        for row in repository._connection.execute(
            "PRAGMA table_info(phone_verifications)"
        )
    }

    assert columns == {
        "phone_verification_id",
        "phone",
        "token_hash",
        "status",
        "requested_at",
        "expires_at",
        "settled_at",
    }


def test_a_verification_for_a_number_with_no_account_is_storable():
    """**There is no account anywhere, and that is the normal case rather than an edge.**

    Every other request table in this schema can be written for an account that does not
    exist, because its ``user_id`` is a plain column rather than a reference. This one goes
    further: nobody has an account on the number yet - creating one is what answering this
    request does - so a store that needed an account first would make signup impossible.

    Worth stating because the sibling suites both say "nothing is seeded" for the opposite
    reason: there, an account id is a value that could have needed a row behind it. Here
    there is no such value at all.
    """
    verification, token = build_verification()

    repository = build_repository()
    repository.save(verification)

    assert spend(repository, token).status is PhoneVerificationStatus.CONFIRMED


def test_a_spent_verification_comes_back_spent():
    """``CONFIRMED`` and its moment, together, from the claim's single ``UPDATE``.

    The aggregate refuses a settled row with no moment and an awaiting row that has one, so
    this pair is a check on that statement rather than on a caller's discipline - and the
    row is rebuilt through the constructor on the way back in, so a store that wrote one
    without the other would fail here rather than travel further wearing a valid shape.
    """
    verification, token = build_verification()
    repository = build_repository()
    repository.save(verification)

    stored = spend(repository, token, NOON + timedelta(minutes=4))

    assert stored.status is PhoneVerificationStatus.CONFIRMED
    assert stored.settled_at == NOON + timedelta(minutes=4)


class TestTheNumberIsFoldedOnTheWayOut:
    """One number, one row - which is the only thing the ``UNIQUE`` column can mean.

    The fold runs in ``__post_init__``, so it covers the way in *and* the way back out: a
    row read from the table is rebuilt through the constructor, so a number that reached
    the table unfolded by some other route still cannot come back out of it unfolded. That
    is ``EmailChange``'s arrangement for ``new_email`` and ``User``'s for its two
    identifiers, and it is the reason ``fold_phone`` is a module function rather than a
    step inside the repository.
    """

    def test_two_spellings_of_one_number_are_one_row(self, tmp_path):
        """**The highest-value assertion in this file**, because it is what ``UNIQUE``
        actually promises and what a missing fold would quietly break.

        ``08012345678`` and ``+2348012345678`` are one handset. Saved separately they must
        leave *one* row, because the second is a newer intention about the same number
        rather than a second number - and the failure this rules out is the one fold_email's
        docstring describes for case: one person holding two pending verifications, and
        then two accounts, with the database perfectly happy about all of it.

        Counted from an independent connection, which is what makes it a count of rows
        rather than a method's opinion.
        """
        db_path = str(tmp_path / "verifications.db")
        repository = SqlitePhoneVerificationRepository(open_sqlite_connection(db_path))
        first, _ = build_verification(phone=A_NATIONAL_NUMBER)
        second, _ = build_verification(phone=AN_INTERNATIONAL_NUMBER)

        repository.save(first)
        repository.save(second)

        assert rows_for(db_path, A_FOLDED_NUMBER) == 1

    def test_the_folded_form_is_what_is_stored(self):
        verification, token = build_verification(phone=A_NATIONAL_NUMBER)
        repository = build_repository()

        repository.save(verification)

        assert spend(repository, token).phone == A_FOLDED_NUMBER


class TestSavingReplacesRatherThanAdds:
    """One pending verification per number, and the key is what makes it structural."""

    def test_asking_twice_leaves_one_row(self, tmp_path):
        """The count that rules out two live codes for one handset.

        The observable version of this - that the first code stops working - is the test
        below, and it would also pass for a store that kept both rows and only ever looked
        at the newest. Counting the table is the assertion that rules that out, and it
        matters here for a reason it does not have next door: two live codes for one number
        are two independent ways to claim it, and only one account can hold it.
        """
        db_path = str(tmp_path / "verifications.db")
        repository = SqlitePhoneVerificationRepository(open_sqlite_connection(db_path))
        first, _ = build_verification()
        second, _ = build_verification()

        repository.save(first)
        repository.save(second)

        assert rows_for(db_path, A_FOLDED_NUMBER) == 1

    def test_the_second_code_works_and_the_first_does_not(self):
        """Both halves, because either alone would pass for a store that ignored one.

        Asserting only that the new code works would pass for a store that kept both rows;
        asserting only that the old one fails would pass for a store that wrote nothing at
        all. This is the remedy for a text that never arrived, stated as a behaviour: ask
        again, and the first code is dead before the second is sent.
        """
        first, first_code = build_verification()
        second, second_code = build_verification()
        repository = build_repository()
        repository.save(first)
        repository.save(second)

        with pytest.raises(InvalidPhoneVerificationTokenError):
            spend(repository, first_code)

        assert spend(repository, second_code).phone == A_FOLDED_NUMBER

    def test_the_replaced_code_looks_like_one_that_never_existed(self):
        """Not like an expired one, which is the honest report.

        The row the first code named is *gone*, so the fall-through in ``_refusal`` finds
        nothing and answers with the unknown-token error. That is also exactly what a guess
        produces, so somebody holding a stale code cannot learn from the refusal that it was
        ever real - which is worth having, since holding the code is the entire
        authorisation to claim a number.
        """
        first, first_code = build_verification()
        second, _ = build_verification()
        repository = build_repository()
        repository.save(first)
        repository.save(second)

        with pytest.raises(InvalidPhoneVerificationTokenError) as refused:
            spend(repository, first_code)

        assert not isinstance(refused.value, PhoneVerificationExpiredError)

    def test_a_spent_verification_does_not_block_a_new_one(self):
        """Asking again after a code has been answered is a real case, not an edge.

        The account that was created may be one the person cannot log into - a password
        they mistyped twice, a phone that was wiped before the password manager saved it -
        and this table is keyed on the number, so a spent row sits in the slot the number
        needs. ``save`` writes whatever the aggregate holds, and every new request comes
        from ``issue``, which always produces ``AWAITING``. So a spent row is replaced by a
        live one, and nobody is locked out by having verified once.

        It is the same property the sibling store pins, and it is worth pinning separately
        because the confirm path here *creates* rather than replaces: a store that refused
        to overwrite a spent row would leave the number permanently unusable, which is the
        worst outcome this table can produce.
        """
        first, first_code = build_verification()
        repository = build_repository()
        repository.save(first)
        spend(repository, first_code)

        second, second_code = build_verification(now=NOON + timedelta(minutes=1))
        repository.save(second)

        claimed = spend(repository, second_code, NOON + timedelta(minutes=2))
        assert claimed.phone == A_FOLDED_NUMBER
        assert claimed.status is PhoneVerificationStatus.CONFIRMED

    def test_the_row_moves_entirely_including_its_own_id(self):
        """The newer request is a *different request*, not a correction to the old one.

        So every column is overwritten rather than merged - and the id is the column it
        would be tempting to keep, since keeping it would make the number's request "the
        same row, updated". It would also throw away the only record of what was asked
        when, which in this flow is the record of when somebody asked to prove a number
        they may not have held.
        """
        first, _ = build_verification()
        second, second_code = build_verification(now=NOON + timedelta(hours=1))
        repository = build_repository()
        repository.save(first)
        repository.save(second)

        stored = spend(repository, second_code, NOON + timedelta(hours=1))

        assert stored.phone_verification_id == second.phone_verification_id
        assert stored.phone_verification_id != first.phone_verification_id
        assert stored.requested_at == NOON + timedelta(hours=1)
        assert (
            stored.expires_at == NOON + timedelta(hours=1) + PHONE_VERIFICATION_LIFETIME
        )

    def test_one_numbers_verification_is_not_anothers(self):
        """The key is the number, and this is the control that says so.

        A store that keyed on something global - or ignored the conflict clause and
        overwrote the single row it holds - would pass every test above in a database with
        one number in it. Two numbers, two requests, both alive.

        This is the test that matters most for a table keyed this way, and more than it does
        next door: the failure it rules out is one person's request superseding a
        stranger's, so the code one person is waiting for would be the code that claims
        somebody else's handset.

        Note the two numbers are deliberately *different* rather than two spellings of one -
        the fold suite above is where the same-number case lives, and using the same number
        here would make this test's name a lie.
        """
        first, first_code = build_verification(phone=A_FOLDED_NUMBER)
        second, second_code = build_verification(phone=ANOTHER_NUMBER)
        repository = build_repository()
        repository.save(first)
        repository.save(second)

        assert spend(repository, first_code).phone == A_FOLDED_NUMBER
        assert spend(repository, second_code).phone == ANOTHER_FOLDED_NUMBER


class TestTheClaimIsTheGate:
    """One statement, so two answers arriving together cannot both succeed."""

    def test_it_spends_the_request_and_returns_it_confirmed(self):
        """Both halves matter, for the reason the confirmation suite gives.

        The object it returns is what the use case reads the number off, and the stored row
        is what stops a second answer. A claim that returned a spent object without writing
        would let the *same* number be claimed twice, and there is exactly one account slot
        for it - so the second claim would fail at the ``UNIQUE`` on ``users.phone``, after
        the use case had already done everything else.
        """
        verification, token = build_verification()
        repository = build_repository()
        repository.save(verification)

        claimed = spend(repository, token)

        assert claimed.status is PhoneVerificationStatus.CONFIRMED
        assert claimed.phone == verification.phone
        with pytest.raises(PhoneVerificationAlreadyUsedError):
            spend(repository, token, NOON + timedelta(seconds=1))

    def test_it_records_the_moment_it_was_answered_with(self):
        """``as_of`` is what gets written, so the row says when it was answered.

        The same instant the window was tested against - so a request cannot be recorded as
        answered at a time it was not allowed to be answered at.
        """
        verification, token = build_verification()
        repository = build_repository()
        repository.save(verification)
        answered_at = NOON + timedelta(minutes=7)

        claimed = spend(repository, token, answered_at)

        assert claimed.settled_at == answered_at
        assert claimed.requested_at == NOON

    def test_answering_twice_is_refused(self):
        """The gate. ``AWAITING`` in the ``WHERE`` is what does it.

        The second claim matches no row - not because anything looked at the status and
        decided, but because the condition is false. That is the difference between this and
        a check-then-write, and it is the difference two concurrent answers would expose:
        with a check-then-write, two confirms arriving in the same instant would both see
        ``AWAITING`` and both go on to claim the number.
        """
        verification, token = build_verification()
        repository = build_repository()
        repository.save(verification)
        spend(repository, token)

        with pytest.raises(PhoneVerificationAlreadyUsedError):
            spend(repository, token)

    def test_answering_past_the_window_is_refused(self):
        verification, token = build_verification()
        repository = build_repository()
        repository.save(verification)

        with pytest.raises(PhoneVerificationExpiredError):
            spend(repository, token, NOON + PHONE_VERIFICATION_LIFETIME)

    def test_a_refused_claim_leaves_the_request_spendable(self):
        """A refusal is a refusal, not a side effect.

        An ``UPDATE`` that matched no row because of the *window* changed nothing, so the
        same request is still there to be answered at a moment the window allows - and this
        is what says the expired branch is not quietly spending what it refused. In this
        flow the consequence is concrete: somebody whose clock was wrong, or who read the
        text a second too early, is not left with a dead code on account of having tried.
        """
        verification, token = build_verification()
        repository = build_repository()
        repository.save(verification)

        with pytest.raises(PhoneVerificationExpiredError):
            spend(repository, token, NOON + PHONE_VERIFICATION_LIFETIME)

        claimed = spend(
            repository, token, NOON + PHONE_VERIFICATION_LIFETIME - timedelta(seconds=1)
        )
        assert claimed.status is PhoneVerificationStatus.CONFIRMED

    def test_a_code_that_names_nothing_is_refused(self):
        with pytest.raises(InvalidPhoneVerificationTokenError):
            build_repository().claim_by_token_hash(
                hash_session_token("never-minted"), NOON
            )

    def test_the_unknown_refusal_says_nothing_at_all(self):
        """**Bare, and the emptiness is the point rather than laziness.**

        A message naming the token hash would make this answer differ from the same answer
        about a hash that names nothing - and the entire authorisation for claiming a number
        is holding the code, so an oracle here would tell a guesser which of their guesses
        was real. ``errors._detail`` turns an empty message into the class name, so a client
        still gets something to read.

        Asserted because this is exactly what a helpful-looking ``f"no verification for
        {token_hash}"`` would remove, and nothing else in the suite would notice.
        """
        with pytest.raises(InvalidPhoneVerificationTokenError) as refused:
            build_repository().claim_by_token_hash(
                hash_session_token("never-minted"), NOON
            )

        assert str(refused.value) == ""

    def test_a_raw_token_is_not_a_hash_and_matches_nothing(self):
        """**This method takes a hash, and that is a contract rather than a hint.**

        The property that makes a plain SHA-256 sufficient is that a stored value is never a
        usable credential - and this is where that property is *produced*: the store hashes
        nothing, so a caller that hands it whatever arrived on the wire is handing it the
        wrong value, and the claim matches no row.

        Asserted here so the layering is explicit rather than discovered: the hashing is the
        *caller's* job, and the caller that must do it is ``ConfirmPhoneSignUp``, which
        hashes a presented code before it gets here.
        """
        verification, token = build_verification()
        repository = build_repository()
        repository.save(verification)

        with pytest.raises(InvalidPhoneVerificationTokenError):
            repository.claim_by_token_hash(token, NOON)

    def test_the_claim_reads_no_column_a_request_supplies(self):
        """**The number is the one the row names, and no argument can widen that.**

        This is ``PasswordResetRepository``'s arrangement, and it is the one place where the
        two sibling claims differ visibly from what a reader expects: the caller *does* know
        the number in this flow - it typed it a moment ago - and the temptation is to scope
        the ``WHERE`` by it as well. The claim takes nothing but a hash and a moment, so a
        code for a different number can never be spent by naming this one.

        The alternative would be worse than it looks. Scoping by number would mean a
        mistyped digit is refused as "that code means nothing", and the three refusals would
        stop falling through exactly: a row matched by code but not by number would be
        reported as expired with an ``expires_at`` in the future. See the port for the whole
        argument.

        Stated as a test because the tempting alternative looks like a tidier interface, and
        nothing else in this file would fail if somebody added the parameter.
        """
        mine, mine_code = build_verification(phone=A_FOLDED_NUMBER)
        theirs, theirs_code = build_verification(phone=ANOTHER_NUMBER)
        repository = build_repository()
        repository.save(mine)
        repository.save(theirs)

        claimed = spend(repository, mine_code)

        assert claimed.phone == A_FOLDED_NUMBER
        assert claimed.phone_verification_id == mine.phone_verification_id
        assert claimed.phone_verification_id != theirs.phone_verification_id
        # And the other one is untouched, still live and still nobody's but its own.
        assert spend(repository, theirs_code).phone == ANOTHER_FOLDED_NUMBER


class TestTheNumberIsHeldUntilTheRequestIsAnswered:
    """**The cost this table deliberately pays**, stated where it is incurred.

    ``UNIQUE(phone)`` is what stops two people signing up with one handset. It is also what
    means a *pending* request holds the number: while a verification is awaiting, nobody can
    claim that number - including the person the code was sent to, if they ask again and the
    second request supersedes the first rather than adding to it.

    That is the whole remedy, and it is worth a test because the alternative looks
    friendlier and is worse. Deciding at request time whether the number is free would be
    both racy - two requests arriving together would both see it free - and an enumeration
    oracle, since "that number is taken" answers a question the endpoint is meant to answer
    identically for every identifier. So the slot is held, and the window is ten minutes
    rather than fifteen partly because of it. See ``PhoneVerificationStatus.EXPIRED``.
    """

    def test_an_awaiting_request_is_the_only_row_for_that_number(self, tmp_path):
        db_path = str(tmp_path / "verifications.db")
        repository = SqlitePhoneVerificationRepository(open_sqlite_connection(db_path))

        repository.save(build_verification()[0])
        repository.save(build_verification()[0])
        repository.save(build_verification(phone=A_NATIONAL_NUMBER)[0])

        assert rows_for(db_path, A_FOLDED_NUMBER) == 1

    def test_superseding_leaves_exactly_one_live_code(self):
        """The property the held slot has to be read with.

        "The number is taken" is true only for the life of one request, because asking again
        replaces it. So the cost is bounded by the window rather than by an account that
        might never be created - which is what keeps this a nuisance rather than a
        lockout.
        """
        first, first_code = build_verification()
        second, second_code = build_verification(now=NOON + timedelta(seconds=30))
        repository = build_repository()
        repository.save(first)
        repository.save(second)

        with pytest.raises(InvalidPhoneVerificationTokenError):
            spend(repository, first_code)

        assert spend(repository, second_code).phone == A_FOLDED_NUMBER


def test_the_window_closes_at_the_instant_it_closes():
    """**The boundary, pinned against ``PhoneVerification.is_expired``.**

    ``expires_at > ?`` here and ``as_of >= expires_at`` in the aggregate are one rule
    written twice, and they must agree - or the two would disagree about the last instant of
    a request's life. The direction of that disagreement is worth naming in this flow: a
    code that claims a unique identifier, working for one moment longer than it was texted
    for.

    So both moments either side of the boundary are spent, and the aggregate's answer at
    each is asserted beside them. A change to either comparison that was not mirrored fails
    one of these four assertions.
    """
    expired, expired_code = build_verification()
    live, live_code = build_verification(phone=ANOTHER_NUMBER)
    repository = build_repository()
    repository.save(expired)
    repository.save(live)
    just_inside = NOON + PHONE_VERIFICATION_LIFETIME - timedelta(microseconds=1)

    with pytest.raises(PhoneVerificationExpiredError):
        spend(repository, expired_code, NOON + PHONE_VERIFICATION_LIFETIME)

    claimed = spend(repository, live_code, just_inside)

    assert claimed.status is PhoneVerificationStatus.CONFIRMED
    assert expired.is_expired(NOON + PHONE_VERIFICATION_LIFETIME) is True
    assert live.is_expired(just_inside) is False
