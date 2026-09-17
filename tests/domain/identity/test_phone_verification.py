"""``PhoneVerification``: a request to prove a number, waiting to be answered.

Four things are pinned here, and they are the same four ``test_password_reset.py``
pins, because the two aggregates share one lifecycle:

- **The window**, which is checked and not enforced, so the boundary is reachable by a
  test rather than only by waiting.
- **``EXPIRED`` is derived**, so there is no state a reader could write.
- **``settled_at`` and the status agree**, so a row whose two halves disagree fails at
  load rather than being interpreted.
- **There is no method that spends this**, which is the one somebody will try to add.

The fifth is specific to this aggregate and is the reason it is not ``PasswordReset``
with the field renamed: **the row has no account on it.** An address change and a reset
both carry a ``user_id``, because they are requests made by an account about an account.
A signup has no account yet - the account is what answering this request creates - so the
subject is the number itself and the number is the key. That is asserted as an absence
below, in the place somebody adding a ``user_id`` column would look first.

The sixth is also specific, and it is a *cost* rather than a feature: the row holds a
``UNIQUE`` slot on a number nobody has claimed. One test states what that means, because
it is the kind of thing a reader finds out the hard way otherwise.

What this file deliberately does *not* test is the claim, because it is the store's -
``tests/infrastructure/repositories/test_sqlite_phone_verification_repository.py`` is
where the single-use property lives, and it is the only place it *can* live.
"""

from datetime import date, datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.identity.exception import (
    InvalidPhoneVerificationExpiresAtError,
    InvalidPhoneVerificationIDError,
    InvalidPhoneVerificationPhoneError,
    InvalidPhoneVerificationRequestedAtError,
    InvalidPhoneVerificationSettledAtError,
    InvalidPhoneVerificationStatusError,
    InvalidPhoneVerificationTokenHashError,
    InvalidPhoneVerificationWindowError,
    InvalidUserPhoneError,
)
from app.domain.identity.phoneVerification import (
    PHONE_VERIFICATION_LIFETIME,
    PhoneVerification,
)
from app.domain.identity.phoneVerificationStatus import PhoneVerificationStatus
from app.domain.identity.session import hash_session_token

REQUESTED = datetime(2026, 3, 2, 12, 0)

#: A number as a person types it, and the one spelling it folds to.
A_NATIONAL_NUMBER = "08012345678"
A_FOLDED_NUMBER = "2348012345678"


def build(**overrides) -> PhoneVerification:
    """A request with every field valid, so a test can spoil exactly one.

    Local rather than shared, for the reason ``test_password_reset.py``'s builder gives:
    what is under test is the aggregate, and this file needs nothing but it.
    """
    fields = {
        "phone_verification_id": uuid4(),
        "phone": A_FOLDED_NUMBER,
        "token_hash": hash_session_token("a-token"),
        "status": PhoneVerificationStatus.AWAITING,
        "requested_at": REQUESTED,
        "expires_at": REQUESTED + PHONE_VERIFICATION_LIFETIME,
    }
    fields.update(overrides)
    return PhoneVerification(**fields)


class TestWhatARequestHolds:
    def test_it_keeps_what_it_was_given(self):
        phone_verification_id = uuid4()

        verification = build(phone_verification_id=phone_verification_id)

        assert verification.phone_verification_id == phone_verification_id
        assert verification.requested_at == REQUESTED
        assert verification.settled_at is None

    def test_the_number_is_the_key_and_no_account_is_on_the_row(self):
        """**The difference between this aggregate and its two siblings.**

        ``EmailChange`` and ``PasswordReset`` carry ``user_id``, and both need it: they
        are requests made *by* an account, and the account is what ties the row to
        everything else in the schema. Here there is deliberately nothing - the number
        is the subject because the account does not exist yet, and the account is what
        answering this request creates.

        This is the assertion that would fail if somebody added a ``user_id`` column on
        the reasonable-sounding grounds that "every other request table has one". The
        consequence of adding it is not tidiness: a nullable ``user_id`` that is always
        ``NULL`` here reads as an account that was deleted, and a non-nullable one
        cannot be written at all.
        """
        assert not hasattr(build(), "user_id")

    def test_it_holds_a_hash_and_cannot_hold_a_token(self):
        """The type is the guarantee, and this is what says so.

        ``token_hash`` is the only form of the credential that appears on this
        aggregate, exactly as on ``Session``, ``EmailChange`` and ``PasswordReset`` -
        and there is no redacted ``repr`` for the same reason: printing a request prints
        a hash, and a hash cannot be presented.

        The plaintext exists only in ``issue``'s return value, which is why that is a
        factory rather than something a caller assembles.
        """
        verification = build()

        assert verification.token_hash == hash_session_token("a-token")
        assert not hasattr(verification, "token")

    def test_it_prints_where_it_has_got_to_and_not_which_number_it_is(self):
        """A log line about a request is for finding the request, and nothing else.

        **The number is deliberately absent**, and this is the one ``__str__`` in the
        codebase whose omission is load-bearing rather than incidental.
        ``EmailChange`` prints the address it is moving to, because the address is the
        payload and a log line about a change that does not say what changed is useless.
        Here the subject *is* a phone number - the smallest enumerable identifier in
        this product - and a log line is exactly the kind of place one leaks out of. The
        status alone is enough to find the row.
        """
        printed = str(build())

        assert printed == "phone verification (awaiting)"
        assert A_FOLDED_NUMBER not in printed
        assert A_NATIONAL_NUMBER not in printed


class TestTheNumberIsFoldedOnTheWayIn:
    """One number, one spelling, which is the whole of what ``UNIQUE(phone)`` rests on.

    The column is ``UNIQUE``, and a ``UNIQUE`` column on a table with no fold is a
    constraint that does not mean what it looks like it means: ``08012345678`` and
    ``+2348012345678`` are one handset, so without this one person could hold two
    pending verifications and go on to hold two accounts, with the database perfectly
    happy about it. ``fold_email``'s argument, one identifier over, and it belongs to
    ``User`` as much as to this row - which is why both call the same function.
    """

    def test_a_national_number_is_stored_internationally(self):
        verification = build(phone=A_NATIONAL_NUMBER)

        assert verification.phone == A_FOLDED_NUMBER

    def test_the_international_form_is_stored_unchanged(self):
        verification = build(phone="+" + A_FOLDED_NUMBER)

        assert verification.phone == A_FOLDED_NUMBER

    def test_two_spellings_of_one_number_are_one_value(self):
        """The property that makes the ``UNIQUE`` column mean what it says."""
        national = build(phone=A_NATIONAL_NUMBER)
        international = build(phone="+" + A_FOLDED_NUMBER)

        assert national.phone == international.phone

    def test_a_number_that_folds_to_nothing_is_refused_as_a_shape_rule(self):
        """``"()"`` folds to ``""``, and it is refused by ``checked_phone`` rather than
        by this aggregate's own type check.

        The division is the one ``EmailChange`` draws around ``new_email``: the shape
        rule is ``User``'s and is deliberately shared, so the error names the rule that
        refused it rather than the row it was going into. A blank reaching here is a
        caller that skipped ``checked_phone``, not a bad number.
        """
        with pytest.raises(InvalidUserPhoneError):
            build(phone="()")


class TestWhatIsNotARequest:
    def test_a_non_uuid_request_id_is_refused(self):
        with pytest.raises(InvalidPhoneVerificationIDError):
            build(phone_verification_id="not-a-uuid")

    def test_a_number_that_is_not_a_string_is_refused_by_its_own_class(self):
        """**The one field check that is this aggregate's rather than the shape rule's.**

        ``checked_phone`` would refuse any of these too - and would raise
        ``InvalidUserPhoneError``, which names the ``User`` aggregate and would send a
        reader to the wrong file. The type check runs first so that a row mapped wrongly
        in the store fails as a fact about *this* row, exactly as
        ``InvalidEmailChangeNewEmailError`` exists for ``new_email``.
        """
        for not_a_number in (None, 2348012345678, ["08012345678"]):
            with pytest.raises(InvalidPhoneVerificationPhoneError):
                build(phone=not_a_number)

    def test_an_empty_token_hash_is_refused(self):
        """A row that can never be matched, wearing the shape of one that works.

        It would present as a text whose code does nothing when presented - the hardest
        kind of failure to trace back, because everything the person can see says the
        request was made.
        """
        with pytest.raises(InvalidPhoneVerificationTokenHashError):
            build(token_hash="")

    def test_a_non_string_token_hash_is_refused(self):
        with pytest.raises(InvalidPhoneVerificationTokenHashError):
            build(token_hash=None)

    def test_a_status_that_is_not_one_of_the_three_is_refused(self):
        """A closed set, so ``status_as_of`` cannot fall through to a word nobody read.

        The enum is the vocabulary and this is what makes it closed. A plain string
        ``"awaiting"`` would compare unequal to every member - so a request would read as
        neither waiting nor spent, and the two places that ask (the claim in the store,
        ``is_settled`` here) would each answer ``False`` and disagree with the row.
        """
        with pytest.raises(InvalidPhoneVerificationStatusError):
            build(status="awaiting")

    def test_a_date_where_a_moment_belongs_is_refused(self):
        """The trap every aggregate in this codebase guards.

        ``datetime`` is a ``date``, so the wider type would accept a bare date as a
        moment - and bare dates are promoted to midnight, so every code texted in the
        afternoon would appear to expire at breakfast. Refusing the type at the
        constructor is what turns a wrong column mapping into a loud failure at load
        rather than a quiet one at the deadline.
        """
        with pytest.raises(InvalidPhoneVerificationRequestedAtError):
            build(requested_at=date(2026, 3, 2))

    def test_a_date_where_the_expiry_belongs_is_refused(self):
        with pytest.raises(InvalidPhoneVerificationExpiresAtError):
            build(expires_at=date(2026, 3, 3))


class TestTheWindow:
    """Checked, not enforced by the store, so the boundary is a case and not a wait."""

    def test_a_window_that_closes_before_it_opens_is_refused(self):
        """Born dead, and it is a bug rather than a state.

        Nothing constructs one deliberately, so the only way here is a negative lifetime
        or a transposed argument order - and left alone it would present as a text that
        arrives already dead.
        """
        with pytest.raises(InvalidPhoneVerificationWindowError):
            build(expires_at=REQUESTED - timedelta(seconds=1))

    def test_a_zero_length_window_is_refused_too(self):
        """``<=`` rather than ``<``: a request that expires as it is made is unusable."""
        with pytest.raises(InvalidPhoneVerificationWindowError):
            build(expires_at=REQUESTED)

    def test_it_is_live_before_it_expires(self):
        verification = build()

        assert (
            verification.is_expired(verification.expires_at - timedelta(seconds=1))
            is False
        )

    def test_it_is_expired_at_the_instant_it_expires(self):
        """``>=`` and not ``>``, which is the whole boundary.

        A request is expired *at* the moment it expires, not a moment later. The
        alternative is a code that works for an instant its owner was never promised -
        and the store's claim tests the opposite side of this same boundary with
        ``expires_at > ?``. The pair has to agree, or there is an instant where the
        aggregate says live and the store refuses, or the reverse.
        """
        verification = build()

        assert verification.is_expired(verification.expires_at) is True

    def test_it_is_expired_after(self):
        verification = build()

        assert verification.is_expired(verification.expires_at + timedelta(days=1))

    def test_it_is_not_expired_by_a_moment_before_it_was_made(self):
        """Nonsense in, a sensible answer out - and the reason is worth stating.

        A clock that is behind should not make a fresh request look dead. The comparison
        needs no special case for this; saying so is what stops somebody adding one.
        """
        verification = build()

        assert (
            verification.is_expired(REQUESTED - timedelta(days=365)) is False
        )


class TestExpiryIsDerivedAndNeverStored:
    """``EXPIRED`` is what a *reader* is shown, and no row ever holds it."""

    def test_an_awaiting_request_past_its_window_reads_expired(self):
        verification = build()

        assert (
            verification.status_as_of(verification.expires_at)
            is PhoneVerificationStatus.EXPIRED
        )

    def test_an_awaiting_request_inside_its_window_still_reads_awaiting(self):
        verification = build()

        assert (
            verification.status_as_of(verification.expires_at - timedelta(seconds=1))
            is PhoneVerificationStatus.AWAITING
        )

    def test_a_confirmed_request_stays_confirmed_however_long_ago_it_was_answered(self):
        """Expiry is about whether a request may still be *answered*.

        One that was answered is not un-answered by the clock. A reader that derived
        ``EXPIRED`` here would report a number as unclaimed when an account holds it.
        """
        answered = build(
            status=PhoneVerificationStatus.CONFIRMED,
            settled_at=REQUESTED + timedelta(minutes=2),
        )

        assert answered.status_as_of(REQUESTED + timedelta(days=365)) is (
            PhoneVerificationStatus.CONFIRMED
        )

    def test_the_stored_status_is_never_the_derived_one(self):
        """The property the whole arrangement rests on, stated on the object itself.

        ``status_as_of`` answers a question; ``status`` is what the row says. A request
        does not become expired by being asked about, and this is the assertion that
        would fail if somebody ever "helpfully" wrote the derived value back.
        """
        verification = build()

        assert (
            verification.status_as_of(verification.expires_at)
            is PhoneVerificationStatus.EXPIRED
        )
        assert verification.status is PhoneVerificationStatus.AWAITING


class TestSettledAndSettledAtMustAgree:
    """The rule ``OutboundMessage`` makes, for its reason: a state and its explanation.

    A spent request with no moment cannot say when it was answered; an awaiting one
    carrying a settled moment claims to be finished and unfinished at once. Neither has
    a correct reading, and both would be written by the claim's single ``UPDATE`` - so a
    row that disagrees is a bug in that statement rather than a state to interpret.
    """

    def test_a_confirmed_request_with_no_settled_moment_is_refused(self):
        with pytest.raises(InvalidPhoneVerificationSettledAtError):
            build(status=PhoneVerificationStatus.CONFIRMED)

    def test_an_awaiting_request_carrying_a_settled_moment_is_refused(self):
        with pytest.raises(InvalidPhoneVerificationSettledAtError):
            build(
                status=PhoneVerificationStatus.AWAITING,
                settled_at=REQUESTED + timedelta(minutes=1),
            )

    def test_a_settled_moment_that_is_not_a_moment_is_refused(self):
        with pytest.raises(InvalidPhoneVerificationSettledAtError):
            build(settled_at="yesterday")

    def test_a_confirmed_request_that_records_when_it_settled_is_accepted(self):
        """The only combination a store will ever write, and it must construct."""
        settled_at = REQUESTED + timedelta(minutes=2)

        verification = build(
            status=PhoneVerificationStatus.CONFIRMED, settled_at=settled_at
        )

        assert verification.is_settled is True
        assert verification.settled_at == settled_at

    def test_settled_asks_only_about_confirmed(self):
        """``EXPIRED`` is deliberately not asked about.

        It is derived and never stored, so a row is either waiting or spent. Asking
        ``status_as_of`` instead would make this property depend on a moment it was
        never given - and a property that needs a clock to answer is not a property.
        """
        assert build().is_settled is False


class TestThereIsNoWayToSpendARequestHere:
    """**The design, stated as an absence**, because this is the one somebody adds.

    The check-and-write that spends a request has to be one statement or two concurrent
    confirms would both see ``AWAITING`` and both go on to claim the number - and the
    second would find the ``UNIQUE`` slot taken only after having done everything else,
    which is the same shape of half-done write as a password replaced twice.

    The transition therefore lives in the store's claim, which sets ``status`` and
    ``settled_at`` together inside a single ``UPDATE`` guarded on the status and the
    window.

    A ``settle`` method on this aggregate would be a second route to spending a request -
    and it would be the route that *skips the window test*, since the window is a
    comparison against a moment the aggregate was not given.
    """

    def test_the_aggregate_has_no_method_that_spends_it(self):
        for forbidden in ("settle", "confirm", "apply", "spend"):
            assert not hasattr(PhoneVerification, forbidden), (
                f"PhoneVerification.{forbidden} would be a second route to spending a "
                f"request, and the one that skips the window test - see "
                f"SqlitePhoneVerificationRepository.claim_by_token_hash"
            )

    def test_the_status_field_is_what_a_reader_carries_and_not_a_transition(self):
        """Assigning it changes nothing about whether the store will accept a code.

        The claim is guarded on the row in the *database*, not on the object in memory,
        so a caller that set ``status`` here would be editing a copy that the next
        statement overwrites. Worth stating, because the mutable dataclass makes it look
        otherwise.
        """
        verification = build()

        verification.status = PhoneVerificationStatus.CONFIRMED
        verification.settled_at = REQUESTED + timedelta(minutes=1)

        assert verification.is_settled is True


class TestIssuing:
    def test_it_returns_a_request_and_a_token_that_match(self):
        verification, token = PhoneVerification.issue(
            phone=A_FOLDED_NUMBER, now=REQUESTED
        )

        assert verification.token_hash == hash_session_token(token)

    def test_the_token_is_not_the_stored_hash(self):
        """The property that makes a plain SHA-256 sufficient, asserted here too.

        The same guarantee ``Session``'s tests pin: presenting the stored value would
        hash the hash, which matches nothing.
        """
        verification, token = PhoneVerification.issue(
            phone=A_FOLDED_NUMBER, now=REQUESTED
        )

        assert verification.token_hash != token

    def test_the_request_is_for_the_number_it_was_issued_for(self):
        verification, _ = PhoneVerification.issue(
            phone=A_NATIONAL_NUMBER, now=REQUESTED
        )

        assert verification.phone == A_FOLDED_NUMBER

    def test_it_starts_awaiting_and_no_account_exists(self):
        """The whole point of a pending row: nothing has been claimed.

        Nothing in ``issue`` touches ``users`` - there is no user to touch, only a
        number - and this is what says so.
        """
        verification, _ = PhoneVerification.issue(
            phone=A_FOLDED_NUMBER, now=REQUESTED
        )

        assert verification.status is PhoneVerificationStatus.AWAITING
        assert verification.settled_at is None

    def test_it_lasts_ten_minutes_rather_than_the_mail_flows_fifteen(self):
        """**The one lifetime here that is deliberately not its siblings'.**

        ``EmailChange`` and ``PasswordReset`` argue for fifteen as "long enough to walk
        to another device", and that argument does not transfer: the device a text
        arrives on is the device that must be in the person's hand to read it, so
        nothing has to be walked to. A window held open longer than the flow can
        plausibly take buys nothing and prolongs the ``UNIQUE`` slot the request holds
        on a number nobody has claimed yet.

        Pinned rather than left implicit because the three windows nearly agree, and
        this is the one that does not - so a future change to either of the others
        should have to come here and say so.
        """
        assert PHONE_VERIFICATION_LIFETIME == timedelta(minutes=10)

        verification, _ = PhoneVerification.issue(
            phone=A_FOLDED_NUMBER, now=REQUESTED
        )

        assert verification.expires_at == REQUESTED + PHONE_VERIFICATION_LIFETIME

    def test_the_lifetime_can_be_overridden(self):
        """So the expiry rule can be exercised without waiting ten minutes.

        The same reason the moment is passed in at all.
        """
        verification, _ = PhoneVerification.issue(
            phone=A_FOLDED_NUMBER, now=REQUESTED, lifetime=timedelta(seconds=1)
        )

        assert verification.expires_at == REQUESTED + timedelta(seconds=1)

    def test_one_request_cannot_have_two_ideas_of_when_it_was_made(self):
        """``now`` is passed in rather than read, and this is the consequence.

        The requested-at moment, the expiry and (later, in the store) the moment the
        claim records all come from one instant - and the same instant later becomes the
        deadline the message body is built around. A factory that read the clock twice
        would produce requests whose two timestamps disagreed by however long the first
        half took, which is invisible until it is not.
        """
        verification, _ = PhoneVerification.issue(
            phone=A_FOLDED_NUMBER, now=REQUESTED
        )

        assert verification.requested_at == REQUESTED

    def test_two_requests_for_one_number_in_the_same_moment_are_two_requests(self):
        """Different ids *and* different tokens, for one number at one instant.

        The id half is obvious; the token half is the one that matters. Asking twice in
        the same second must not mint the same credential - and the second request
        supersedes the first, so a shared token would make "the old code no longer works"
        untrue in exactly the case superseding exists for.
        """
        first, first_token = PhoneVerification.issue(
            phone=A_FOLDED_NUMBER, now=REQUESTED
        )
        second, second_token = PhoneVerification.issue(
            phone=A_FOLDED_NUMBER, now=REQUESTED
        )

        assert first.phone_verification_id != second.phone_verification_id
        assert first_token != second_token
        assert first.token_hash != second.token_hash

    def test_the_token_is_the_same_kind_of_value_a_session_gets(self):
        """**The fourth caller of one discipline**, and the reason is the same one.

        256 bits of CSPRNG output hashed with SHA-256 is the whole of it. Two hundred
        and fifty-six bits is also *not a code anybody can type*, and this test says so
        rather than leaving it to be discovered when the message body is written: the
        plaintext is sixty-odd characters of URL-safe base64, so the text that carries it
        either hands it back as a link or the gate trades a short code for it. What this
        class guarantees is the part that does not depend on which - the stored value is
        a hash and the plaintext has exactly one route out.

        The tension is real and is not resolved here, and the reason it is kept rather
        than traded away is decision 167's: a short code is brute-forceable and there is
        still no rate limiter, so the length stays and the delivery problem is solved by
        whoever composes the message.

        ``token_urlsafe`` draws on ``/`` and ``-``, which is the property ``_prompt_code``
        relies on when it strips what was pasted - no whitespace can occur in one of
        these, so stripping cannot damage a real code.
        """
        _, token = PhoneVerification.issue(phone=A_FOLDED_NUMBER, now=REQUESTED)

        assert len(token) >= 43
        assert not any(character.isspace() for character in token)
