"""``EmailChange``: a request to move an account's address, waiting to be proven.

Four things are pinned here, and they are pinned together because each one is an
argument for a shape that would otherwise look arbitrary:

- **The window**, which is checked and not enforced, so the boundary is reachable
  by a test rather than only by waiting.
- **The fold**, so one address has one spelling before it reaches a ``UNIQUE``
  column - the rule ``User`` already makes, applied to the address being moved to.
- **``EXPIRED`` is derived**, so there is no state a reader could write.
- **There is no method that spends this**, which is the one somebody will try to
  add. ``Confirmation`` has none either, and the reason is the same: the
  check-and-write that spends a request has to be one statement or two concurrent
  confirms would both see ``AWAITING`` and both apply. A ``settle`` here would be a
  second route to spending, and a route that skips the window test.

What this file deliberately does *not* test is the claim, because it is the store's
- ``tests/infrastructure/repositories/test_sqlite_email_change_repository.py`` is
where the single-use property lives, and it is the only place it *can* live.
"""

from datetime import date, datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.identity.emailChange import EMAIL_CHANGE_LIFETIME, EmailChange
from app.domain.identity.emailChangeStatus import EmailChangeStatus
from app.domain.identity.exception import (
    InvalidEmailChangeExpiresAtError,
    InvalidEmailChangeIDError,
    InvalidEmailChangeNewEmailError,
    InvalidEmailChangeRequestedAtError,
    InvalidEmailChangeSettledAtError,
    InvalidEmailChangeStatusError,
    InvalidEmailChangeTokenHashError,
    InvalidEmailChangeUserIDError,
    InvalidEmailChangeWindowError,
    InvalidUserEmailError,
)
from app.domain.identity.session import hash_session_token

REQUESTED = datetime(2026, 3, 2, 12, 0)


def build(**overrides) -> EmailChange:
    """A request with every field valid, so a test can spoil exactly one.

    Local rather than shared, for the reason ``test_session.py``'s builder gives:
    what is under test is the aggregate, and this file needs nothing but it.
    """
    fields = {
        "email_change_id": uuid4(),
        "user_id": uuid4(),
        "new_email": "ada@example.com",
        "token_hash": hash_session_token("a-token"),
        "status": EmailChangeStatus.AWAITING,
        "requested_at": REQUESTED,
        "expires_at": REQUESTED + EMAIL_CHANGE_LIFETIME,
    }
    fields.update(overrides)
    return EmailChange(**fields)


class TestWhatARequestHolds:
    def test_it_keeps_what_it_was_given(self):
        email_change_id = uuid4()
        user_id = uuid4()

        change = build(email_change_id=email_change_id, user_id=user_id)

        assert change.email_change_id == email_change_id
        assert change.user_id == user_id
        assert change.requested_at == REQUESTED
        assert change.settled_at is None

    def test_it_holds_a_hash_and_cannot_hold_a_token(self):
        """The type is the guarantee, and this is what says so.

        ``token_hash`` is the only form of the credential that appears on this
        aggregate, exactly as on ``Session`` - and there is no redacted ``repr`` for
        the same reason: printing a request prints a hash, and a hash cannot be
        presented. The server hashes whatever arrives, so a stored hash would hash
        to something that matches nothing.

        The plaintext exists only in ``issue``'s return value, which is why that is
        a factory rather than something a caller assembles.
        """
        change = build()

        assert change.token_hash == hash_session_token("a-token")
        assert not hasattr(change, "token")

    def test_it_prints_the_new_address_and_never_the_credential(self):
        """A log line about a request is for finding the request.

        ``Session`` has no redacted repr because a hash is not presentable, and the
        same holds here - but this object *is* printed, so the question is what it
        prints instead. The address being moved to and where the request has got to
        are the two things a reader is looking for, and neither is a secret: the
        address is the thing the person typed into a form.
        """
        change = build(new_email="ada@example.com")

        printed = str(change)

        assert "ada@example.com" in printed
        assert "awaiting" in printed


class TestTheAddressBeingMovedTo:
    def test_it_is_folded_on_the_way_in(self):
        """One address, one spelling, before it reaches a ``UNIQUE`` column.

        The rule ``User`` makes at construction, applied here for the reason it
        exists there: ``Ada@Example.com`` and ``ada@example.com`` are the same
        address - the domain part is case-insensitive everywhere - so they are the
        same account, and a lookup that misses one of them is a duplicate the
        database cannot refuse.

        The consequence is that this is a **mutable** dataclass, unlike it looks.
        Folding requires assignment in ``__post_init__``, the same consequence
        ``User``'s docstring records from the same cause.
        """
        change = build(new_email="  Ada@Example.COM  ")

        assert change.new_email == "ada@example.com"

    def test_a_value_that_is_not_an_address_at_all_is_refused(self):
        """The aggregate's own rule, through the *same function* ``User`` calls.

        ``checked_email`` and not a second implementation of it, so a request can
        never be recorded holding an address the account could not be moved to.
        This is the shape rule only: whether the address is *usable* is a policy
        about minting one, and it belongs to the use case. See
        ``app.domain.identity.emailAddress`` for the whole argument.
        """
        with pytest.raises(InvalidUserEmailError):
            build(new_email="not-an-address")

    def test_an_address_with_no_real_domain_is_accepted_here(self):
        """**The usability rule is deliberately not on this aggregate.**

        A request may name ``nobody@localhost`` and this object holds it happily -
        which looks like a hole until the reason is stated. ``User`` has to accept
        what is already on disk, because the repository builds one from its row, so
        a rule enforced there would make every account registered before the rule
        existed unreadable. This aggregate has the same obligation for the same
        reason: it is constructed from a row too.

        What refuses the address is ``RequestEmailChange``, on the way in, where it
        is a policy about *minting* rather than an invariant of the value. A test
        for that refusal lives beside the use case.
        """
        assert build(new_email="nobody@localhost").new_email == "nobody@localhost"

    def test_a_non_string_address_names_this_row_rather_than_the_user(self):
        """The field's own type guard, before the shared rule under it.

        ``checked_email`` makes this same test and would refuse the value anyway -
        but it refuses it as ``InvalidUserEmailError``, which names an aggregate
        rather than a row and would send a reader to the wrong file. A repository
        that mapped a column wrongly should be told which column.
        """
        with pytest.raises(InvalidEmailChangeNewEmailError):
            build(new_email=42)


class TestWhatIsNotARequest:
    def test_a_non_uuid_request_id_is_refused(self):
        with pytest.raises(InvalidEmailChangeIDError):
            build(email_change_id="not-a-uuid")

    def test_a_non_uuid_user_id_is_refused(self):
        with pytest.raises(InvalidEmailChangeUserIDError):
            build(user_id="not-a-uuid")

    def test_an_empty_token_hash_is_refused(self):
        """A row that can never be matched, wearing the shape of one that works.

        It would present as a mail whose code does nothing when presented - the
        hardest kind of failure to trace back, because everything the person can see
        says the request was made.
        """
        with pytest.raises(InvalidEmailChangeTokenHashError):
            build(token_hash="")

    def test_a_non_string_token_hash_is_refused(self):
        with pytest.raises(InvalidEmailChangeTokenHashError):
            build(token_hash=None)

    def test_a_status_that_is_not_one_of_the_three_is_refused(self):
        """A closed set, so ``status_as_of`` cannot fall through to a word nobody read.

        The enum is the vocabulary and this is what makes it closed. A plain string
        ``"awaiting"`` would compare unequal to every member - so a request would
        read as neither waiting nor spent, and the two places that ask (the claim in
        the store, ``is_settled`` here) would each answer ``False`` and disagree with
        the row.
        """
        with pytest.raises(InvalidEmailChangeStatusError):
            build(status="awaiting")

    def test_a_date_where_a_moment_belongs_is_refused(self):
        """The trap every aggregate in this codebase guards.

        ``datetime`` is a ``date``, so the wider type would accept a bare date as a
        moment - and bare dates are promoted to midnight, so every request made in
        the afternoon would appear to expire at breakfast. Refusing the type at the
        constructor is what turns a wrong column mapping into a loud failure at load
        rather than a quiet one at the deadline.
        """
        with pytest.raises(InvalidEmailChangeRequestedAtError):
            build(requested_at=date(2026, 3, 2))

    def test_a_date_where_the_expiry_belongs_is_refused(self):
        with pytest.raises(InvalidEmailChangeExpiresAtError):
            build(expires_at=date(2026, 3, 3))


class TestTheWindow:
    """Checked, not enforced by the store, so the boundary is a case and not a wait."""

    def test_a_window_that_closes_before_it_opens_is_refused(self):
        """Born dead, and it is a bug rather than a state.

        Nothing constructs one deliberately, so the only way here is a negative
        lifetime or a transposed argument order - and left alone it would present as
        a mail that arrives already dead.
        """
        with pytest.raises(InvalidEmailChangeWindowError):
            build(expires_at=REQUESTED - timedelta(seconds=1))

    def test_a_zero_length_window_is_refused_too(self):
        """``<=`` rather than ``<``: a request that expires as it is made is unusable.

        A lifetime of zero is a plausible typo - ``timedelta(minutes=0)`` computed
        from a configuration value - and it produces a code that is dead on arrival.
        """
        with pytest.raises(InvalidEmailChangeWindowError):
            build(expires_at=REQUESTED)

    def test_it_is_live_before_it_expires(self):
        change = build()

        assert change.is_expired(change.expires_at - timedelta(seconds=1)) is False

    def test_it_is_expired_at_the_instant_it_expires(self):
        """``>=`` and not ``>``, which is the whole boundary.

        A request is expired *at* the moment it expires, not a moment later. The
        alternative is a code that works for an instant its owner was never promised
        - and the store's claim tests the opposite side of this same boundary with
        ``expires_at > ?``. The pair has to agree, or there is an instant where the
        aggregate says live and the store refuses, or the reverse.
        """
        change = build()

        assert change.is_expired(change.expires_at) is True

    def test_it_is_expired_after(self):
        change = build()

        assert change.is_expired(change.expires_at + timedelta(days=1)) is True

    def test_it_is_not_expired_by_a_moment_before_it_was_made(self):
        """Nonsense in, a sensible answer out - and the reason is worth stating.

        A clock that is behind should not make a fresh request look dead. The
        comparison needs no special case for this; saying so is what stops somebody
        adding one.
        """
        change = build()

        assert change.is_expired(REQUESTED - timedelta(days=365)) is False


class TestExpiryIsDerivedAndNeverStored:
    """``EXPIRED`` is what a *reader* is shown, and no row ever holds it.

    Storing it would be a second record of a fact ``expires_at`` already holds, free
    to disagree with it - and the only thing that could write it is a reader, which
    would make a ``GET`` a write. The stakes are higher than they look for a
    session: a row saying ``AWAITING`` past its window is a token that still works a
    day after it was mailed, and this one moves an account's address.
    """

    def test_an_awaiting_request_past_its_window_reads_expired(self):
        change = build()

        assert change.status_as_of(change.expires_at) is EmailChangeStatus.EXPIRED

    def test_an_awaiting_request_inside_its_window_still_reads_awaiting(self):
        change = build()

        assert (
            change.status_as_of(change.expires_at - timedelta(seconds=1))
            is EmailChangeStatus.AWAITING
        )

    def test_a_confirmed_request_stays_confirmed_however_long_ago_it_was_answered(self):
        """Expiry is about whether a request may still be *answered*.

        One that was answered is not un-answered by the clock. A reader that derived
        ``EXPIRED`` here would report a change that happened as one that did not -
        and would do it more often the older the record got, which is the opposite of
        what an audit trail is for.
        """
        answered = build(
            status=EmailChangeStatus.CONFIRMED,
            settled_at=REQUESTED + timedelta(minutes=2),
        )

        assert answered.status_as_of(REQUESTED + timedelta(days=365)) is (
            EmailChangeStatus.CONFIRMED
        )

    def test_the_stored_status_is_never_the_derived_one(self):
        """The property the whole arrangement rests on, stated on the object itself.

        ``status_as_of`` answers a question; ``status`` is what the row says. A
        request does not become expired by being asked about, and this is the
        assertion that would fail if somebody ever "helpfully" wrote the derived
        value back.
        """
        change = build()

        assert change.status_as_of(change.expires_at) is EmailChangeStatus.EXPIRED
        assert change.status is EmailChangeStatus.AWAITING


class TestSettledAndSettledAtMustAgree:
    """The rule ``OutboundMessage`` makes, for its reason: a state and its explanation.

    A spent request with no moment cannot say when it was answered; an awaiting one
    carrying a settled moment claims to be finished and unfinished at once. Neither
    has a correct reading, and both would be written by the claim's single
    ``UPDATE`` - so a row that disagrees is a bug in that statement rather than a
    state to interpret.
    """

    def test_a_confirmed_request_with_no_settled_moment_is_refused(self):
        with pytest.raises(InvalidEmailChangeSettledAtError):
            build(status=EmailChangeStatus.CONFIRMED)

    def test_an_awaiting_request_carrying_a_settled_moment_is_refused(self):
        with pytest.raises(InvalidEmailChangeSettledAtError):
            build(
                status=EmailChangeStatus.AWAITING,
                settled_at=REQUESTED + timedelta(minutes=1),
            )

    def test_a_settled_moment_that_is_not_a_moment_is_refused(self):
        with pytest.raises(InvalidEmailChangeSettledAtError):
            build(settled_at="yesterday")

    def test_a_confirmed_request_that_records_when_it_settled_is_accepted(self):
        """The only combination a store will ever write, and it must construct."""
        settled_at = REQUESTED + timedelta(minutes=2)

        change = build(status=EmailChangeStatus.CONFIRMED, settled_at=settled_at)

        assert change.is_settled is True
        assert change.settled_at == settled_at

    def test_settled_asks_only_about_confirmed(self):
        """``EXPIRED`` is deliberately not asked about.

        It is derived and never stored, so a row is either waiting or spent. Asking
        ``status_as_of`` instead would make this property depend on a moment it was
        never given - and a property that needs a clock to answer is not a property.
        """
        assert build().is_settled is False


class TestThereIsNoWayToSpendARequestHere:
    """**The design, stated as an absence**, because this is the one somebody adds.

    ``Confirmation`` has no status transition either, for a reason that applies here
    unchanged: the check-and-write that spends a request has to be one statement or
    two concurrent confirms would both see ``AWAITING`` and both apply. The
    transition therefore lives in the store's claim, which sets ``status`` and
    ``settled_at`` together inside a single ``UPDATE`` guarded on the status and the
    window.

    A ``settle`` method on this aggregate would be a second route to spending a
    request - and it would be the route that *skips the window test*, since the
    window is a comparison against a moment the aggregate was not given. Two
    routes where one is weaker is how a single-use token becomes a two-use one.

    This is asserted rather than commented so that adding the method on the
    reasonable-sounding grounds of "the aggregate should own its own transition"
    fails here, with the argument attached, instead of silently opening the hole.
    """

    def test_the_aggregate_has_no_method_that_spends_it(self):
        for forbidden in ("settle", "confirm", "apply", "spend"):
            assert not hasattr(EmailChange, forbidden), (
                f"EmailChange.{forbidden} would be a second route to spending a "
                f"request, and the one that skips the window test - see "
                f"SqliteEmailChangeRepository.claim_by_token_hash"
            )

    def test_the_status_field_is_what_a_reader_carries_and_not_a_transition(self):
        """Assigning it changes nothing about whether the store will accept a token.

        The claim is guarded on the row in the *database*, not on the object in
        memory, so a caller that set ``status`` here would be editing a copy that
        the next statement overwrites. Worth stating, because the mutable dataclass
        makes it look otherwise.
        """
        change = build()

        change.status = EmailChangeStatus.CONFIRMED
        change.settled_at = REQUESTED + timedelta(minutes=1)

        assert change.is_settled is True


class TestIssuing:
    def test_it_returns_a_request_and_a_token_that_match(self):
        change, token = EmailChange.issue(
            user_id=uuid4(), new_email="ada@example.com", now=REQUESTED
        )

        assert change.token_hash == hash_session_token(token)

    def test_the_token_is_not_the_stored_hash(self):
        """The property that makes a plain SHA-256 sufficient, asserted here too.

        The same guarantee ``Session``'s tests pin: presenting the stored value would
        hash the hash, which matches nothing.
        """
        change, token = EmailChange.issue(
            user_id=uuid4(), new_email="ada@example.com", now=REQUESTED
        )

        assert change.token_hash != token

    def test_the_request_belongs_to_the_account_it_was_made_for(self):
        user_id = uuid4()

        change, _ = EmailChange.issue(
            user_id=user_id, new_email="ada@example.com", now=REQUESTED
        )

        assert change.user_id == user_id

    def test_it_starts_awaiting_and_nothing_has_changed(self):
        """The whole point of a pending row: the account still holds the old address.

        Nothing in ``issue`` touches a ``User`` - there is no user to touch, only an
        id - and this is what says so.
        """
        change, _ = EmailChange.issue(
            user_id=uuid4(), new_email="ada@example.com", now=REQUESTED
        )

        assert change.status is EmailChangeStatus.AWAITING
        assert change.settled_at is None

    def test_it_lasts_the_standard_lifetime(self):
        """Fifteen minutes, and see ``EMAIL_CHANGE_LIFETIME`` for why it is its own
        constant rather than a reuse of ``CONFIRMATION_LIFETIME``.

        Pinned rather than left implicit because the two numbers agree today and the
        agreement is a coincidence of two arguments - so a future change to either
        should have to come here and say so.
        """
        change, _ = EmailChange.issue(
            user_id=uuid4(), new_email="ada@example.com", now=REQUESTED
        )

        assert change.expires_at == REQUESTED + EMAIL_CHANGE_LIFETIME

    def test_the_lifetime_can_be_overridden(self):
        """So the expiry rule can be exercised without waiting fifteen minutes.

        The same reason the moment is passed in at all.
        """
        change, _ = EmailChange.issue(
            user_id=uuid4(),
            new_email="ada@example.com",
            now=REQUESTED,
            lifetime=timedelta(seconds=1),
        )

        assert change.expires_at == REQUESTED + timedelta(seconds=1)

    def test_one_request_cannot_have_two_ideas_of_when_it_was_made(self):
        """``now`` is passed in rather than read, and this is the consequence.

        The requested-at moment, the expiry and (later, in the store) the moment the
        claim records all come from one instant. A factory that read the clock twice
        would produce requests whose two timestamps disagreed by however long the
        first half took - which is invisible until it is not.
        """
        change, _ = EmailChange.issue(
            user_id=uuid4(), new_email="ada@example.com", now=REQUESTED
        )

        assert change.requested_at == REQUESTED

    def test_two_requests_issued_in_the_same_moment_are_two_requests(self):
        """Different ids *and* different tokens, from one account at one instant.

        The id half is obvious; the token half is the one that matters. Asking twice
        in the same second must not mint the same credential - and the second request
        supersedes the first, so a shared token would make "the old one no longer
        works" untrue in exactly the case superseding exists for.
        """
        user_id = uuid4()

        first, first_token = EmailChange.issue(
            user_id=user_id, new_email="ada@example.com", now=REQUESTED
        )
        second, second_token = EmailChange.issue(
            user_id=user_id, new_email="grace@example.com", now=REQUESTED
        )

        assert first.email_change_id != second.email_change_id
        assert first_token != second_token
        assert first.token_hash != second.token_hash

    def test_the_address_is_folded_when_issued(self):
        """``issue`` is the only route that mints a request, so the fold has to hold
        here as well as on direct construction - otherwise a caller could record an
        unfolded address by taking the factory's path rather than the constructor's.
        """
        change, _ = EmailChange.issue(
            user_id=uuid4(), new_email="  Ada@Example.COM  ", now=REQUESTED
        )

        assert change.new_email == "ada@example.com"
