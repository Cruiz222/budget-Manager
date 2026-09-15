"""``PasswordReset``: a request to replace an account's password, waiting to be proven.

Four things are pinned here, and they are the same four ``test_email_change.py``
pins, because the two aggregates share one lifecycle:

- **The window**, which is checked and not enforced, so the boundary is reachable
  by a test rather than only by waiting.
- **``EXPIRED`` is derived**, so there is no state a reader could write.
- **``settled_at`` and the status agree**, so a row whose two halves disagree fails
  at load rather than being interpreted.
- **There is no method that spends this**, which is the one somebody will try to
  add. Here it would be worse than it is for an address change: the thing a second
  route to spending would authorise is replacing a password.

The fifth is specific to this aggregate and is the reason it is not ``EmailChange``
with the fields renamed: **the row carries no payload.** An address change holds
``new_email`` because the address is what the request authorises; a reset holds
nothing, because a password must be written down exactly once in this system, as an
argon2 hash, and a second copy anywhere would be a second thing to protect for no
gain. That is asserted as an absence below, in the place somebody adding a
``new_password`` column would look first.

What this file deliberately does *not* test is the claim, because it is the store's
- ``tests/infrastructure/repositories/test_sqlite_password_reset_repository.py`` is
where the single-use property lives, and it is the only place it *can* live.
"""

from datetime import date, datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.identity.exception import (
    InvalidPasswordResetExpiresAtError,
    InvalidPasswordResetIDError,
    InvalidPasswordResetRequestedAtError,
    InvalidPasswordResetSettledAtError,
    InvalidPasswordResetStatusError,
    InvalidPasswordResetTokenHashError,
    InvalidPasswordResetUserIDError,
    InvalidPasswordResetWindowError,
)
from app.domain.identity.passwordReset import PASSWORD_RESET_LIFETIME, PasswordReset
from app.domain.identity.passwordResetStatus import PasswordResetStatus
from app.domain.identity.session import hash_session_token

REQUESTED = datetime(2026, 3, 2, 12, 0)


def build(**overrides) -> PasswordReset:
    """A request with every field valid, so a test can spoil exactly one.

    Local rather than shared, for the reason ``test_email_change.py``'s builder
    gives: what is under test is the aggregate, and this file needs nothing but it.
    """
    fields = {
        "password_reset_id": uuid4(),
        "user_id": uuid4(),
        "token_hash": hash_session_token("a-token"),
        "status": PasswordResetStatus.AWAITING,
        "requested_at": REQUESTED,
        "expires_at": REQUESTED + PASSWORD_RESET_LIFETIME,
    }
    fields.update(overrides)
    return PasswordReset(**fields)


class TestWhatARequestHolds:
    def test_it_keeps_what_it_was_given(self):
        password_reset_id = uuid4()
        user_id = uuid4()

        reset = build(password_reset_id=password_reset_id, user_id=user_id)

        assert reset.password_reset_id == password_reset_id
        assert reset.user_id == user_id
        assert reset.requested_at == REQUESTED
        assert reset.settled_at is None

    def test_it_holds_a_hash_and_cannot_hold_a_token(self):
        """The type is the guarantee, and this is what says so.

        ``token_hash`` is the only form of the credential that appears on this
        aggregate, exactly as on ``Session`` and ``EmailChange`` - and there is no
        redacted ``repr`` for the same reason: printing a request prints a hash, and
        a hash cannot be presented. The server hashes whatever arrives, so a stored
        hash would hash to something that matches nothing.

        The plaintext exists only in ``issue``'s return value, which is why that is
        a factory rather than something a caller assembles.
        """
        reset = build()

        assert reset.token_hash == hash_session_token("a-token")
        assert not hasattr(reset, "token")

    def test_the_row_carries_no_payload_at_all(self):
        """**The difference between this aggregate and ``EmailChange``.**

        That one carries ``new_email``, and this one must carry nothing, because the
        value it authorises is a password. There is no field here that could hold one
        - not a password, not a hash of one, not a reference to one - and this is the
        assertion that would fail if somebody added a ``new_password`` column on the
        reasonable-sounding grounds that the value should be "decided at request time
        like the address is".

        The reason it must not be is not tidiness. A password is written down exactly
        once in this system, as an argon2 hash in ``password_credentials``, and every
        additional copy is another row to protect, to back up, to log by accident,
        and to keep in step. ``ConfirmPasswordReset`` receives the password with the
        code and holds it in a local variable for the length of one call; that is the
        whole of its lifetime.

        The password is also not the same *kind* of fact as an address, which is why
        the two flows can share a lifecycle and not a shape: an address is an
        identifier compared against other systems, and a password is a secret
        compared against nothing but itself.
        """
        forbidden = (
            "new_password",
            "password",
            "password_hash",
            "plain_password",
            "credential_hash",
        )

        for name in forbidden:
            assert not hasattr(build(), name), (
                f"PasswordReset.{name} would be a second copy of a secret that is "
                f"written down exactly once, in password_credentials - see "
                f"ConfirmPasswordReset, which never stores the value it is given"
            )

    def test_it_prints_where_it_has_got_to_and_nothing_else(self):
        """A log line about a request is for finding the request.

        Shorter than ``EmailChange``'s by exactly the payload it does not have: there
        is no address on this row and no secret, so the status is the only thing left
        to print. ``Session`` has no ``__str__`` at all, and the difference is that a
        session has no state to be in.
        """
        printed = str(build())

        assert printed == "password reset (awaiting)"


class TestWhatIsNotARequest:
    def test_a_non_uuid_request_id_is_refused(self):
        with pytest.raises(InvalidPasswordResetIDError):
            build(password_reset_id="not-a-uuid")

    def test_a_non_uuid_user_id_is_refused(self):
        with pytest.raises(InvalidPasswordResetUserIDError):
            build(user_id="not-a-uuid")

    def test_an_empty_token_hash_is_refused(self):
        """A row that can never be matched, wearing the shape of one that works.

        It would present as a mail whose code does nothing when presented - the
        hardest kind of failure to trace back, because everything the person can see
        says the request was made. The stakes are the highest of the three aggregates
        carrying this shape: what a code here authorises is replacing the account's
        password outright.
        """
        with pytest.raises(InvalidPasswordResetTokenHashError):
            build(token_hash="")

    def test_a_non_string_token_hash_is_refused(self):
        with pytest.raises(InvalidPasswordResetTokenHashError):
            build(token_hash=None)

    def test_a_status_that_is_not_one_of_the_three_is_refused(self):
        """A closed set, so ``status_as_of`` cannot fall through to a word nobody read.

        The enum is the vocabulary and this is what makes it closed. A plain string
        ``"awaiting"`` would compare unequal to every member - so a request would read
        as neither waiting nor spent, and the two places that ask (the claim in the
        store, ``is_settled`` here) would each answer ``False`` and disagree with the
        row.
        """
        with pytest.raises(InvalidPasswordResetStatusError):
            build(status="awaiting")

    def test_a_date_where_a_moment_belongs_is_refused(self):
        """The trap every aggregate in this codebase guards.

        ``datetime`` is a ``date``, so the wider type would accept a bare date as a
        moment - and bare dates are promoted to midnight, so every reset asked for in
        the afternoon would appear to expire at breakfast. Refusing the type at the
        constructor is what turns a wrong column mapping into a loud failure at load
        rather than a quiet one at the deadline.
        """
        with pytest.raises(InvalidPasswordResetRequestedAtError):
            build(requested_at=date(2026, 3, 2))

    def test_a_date_where_the_expiry_belongs_is_refused(self):
        with pytest.raises(InvalidPasswordResetExpiresAtError):
            build(expires_at=date(2026, 3, 3))


class TestTheWindow:
    """Checked, not enforced by the store, so the boundary is a case and not a wait."""

    def test_a_window_that_closes_before_it_opens_is_refused(self):
        """Born dead, and it is a bug rather than a state.

        Nothing constructs one deliberately, so the only way here is a negative
        lifetime or a transposed argument order - and left alone it would present as a
        mail that arrives already dead.
        """
        with pytest.raises(InvalidPasswordResetWindowError):
            build(expires_at=REQUESTED - timedelta(seconds=1))

    def test_a_zero_length_window_is_refused_too(self):
        """``<=`` rather than ``<``: a request that expires as it is made is unusable.

        A lifetime of zero is a plausible typo - ``timedelta(minutes=0)`` computed
        from a configuration value - and it produces a code that is dead on arrival.
        """
        with pytest.raises(InvalidPasswordResetWindowError):
            build(expires_at=REQUESTED)

    def test_it_is_live_before_it_expires(self):
        reset = build()

        assert reset.is_expired(reset.expires_at - timedelta(seconds=1)) is False

    def test_it_is_expired_at_the_instant_it_expires(self):
        """``>=`` and not ``>``, which is the whole boundary.

        A request is expired *at* the moment it expires, not a moment later. The
        alternative is a code that works for an instant its owner was never promised
        - and the store's claim tests the opposite side of this same boundary with
        ``expires_at > ?``. The pair has to agree, or there is an instant where the
        aggregate says live and the store refuses, or the reverse. Here the direction
        of a disagreement is a code that replaces an account's password one instant
        after it was promised to have stopped working.
        """
        reset = build()

        assert reset.is_expired(reset.expires_at) is True

    def test_it_is_expired_after(self):
        reset = build()

        assert reset.is_expired(reset.expires_at + timedelta(days=1)) is True

    def test_it_is_not_expired_by_a_moment_before_it_was_made(self):
        """Nonsense in, a sensible answer out - and the reason is worth stating.

        A clock that is behind should not make a fresh request look dead. The
        comparison needs no special case for this; saying so is what stops somebody
        adding one.
        """
        reset = build()

        assert reset.is_expired(REQUESTED - timedelta(days=365)) is False


class TestExpiryIsDerivedAndNeverStored:
    """``EXPIRED`` is what a *reader* is shown, and no row ever holds it.

    Storing it would be a second record of a fact ``expires_at`` already holds, free
    to disagree with it - and the only thing that could write it is a reader, which
    would make a ``GET`` a write. The stakes are the highest of the three aggregates
    that carry this state: a row saying ``AWAITING`` past its window is a code that
    still replaces an account's password a day after it was mailed.
    """

    def test_an_awaiting_request_past_its_window_reads_expired(self):
        reset = build()

        assert reset.status_as_of(reset.expires_at) is PasswordResetStatus.EXPIRED

    def test_an_awaiting_request_inside_its_window_still_reads_awaiting(self):
        reset = build()

        assert (
            reset.status_as_of(reset.expires_at - timedelta(seconds=1))
            is PasswordResetStatus.AWAITING
        )

    def test_a_confirmed_request_stays_confirmed_however_long_ago_it_was_answered(self):
        """Expiry is about whether a request may still be *answered*.

        One that was answered is not un-answered by the clock. A reader that derived
        ``EXPIRED`` here would report a password that was replaced as one that was
        not - which is the wrong thing to tell somebody who is trying to work out
        whether their account was taken over.
        """
        answered = build(
            status=PasswordResetStatus.CONFIRMED,
            settled_at=REQUESTED + timedelta(minutes=2),
        )

        assert answered.status_as_of(REQUESTED + timedelta(days=365)) is (
            PasswordResetStatus.CONFIRMED
        )

    def test_the_stored_status_is_never_the_derived_one(self):
        """The property the whole arrangement rests on, stated on the object itself.

        ``status_as_of`` answers a question; ``status`` is what the row says. A
        request does not become expired by being asked about, and this is the
        assertion that would fail if somebody ever "helpfully" wrote the derived value
        back.
        """
        reset = build()

        assert reset.status_as_of(reset.expires_at) is PasswordResetStatus.EXPIRED
        assert reset.status is PasswordResetStatus.AWAITING


class TestSettledAndSettledAtMustAgree:
    """The rule ``OutboundMessage`` makes, for its reason: a state and its explanation.

    A spent request with no moment cannot say when it was answered; an awaiting one
    carrying a settled moment claims to be finished and unfinished at once. Neither
    has a correct reading, and both would be written by the claim's single ``UPDATE``
    - so a row that disagrees is a bug in that statement rather than a state to
    interpret.
    """

    def test_a_confirmed_request_with_no_settled_moment_is_refused(self):
        with pytest.raises(InvalidPasswordResetSettledAtError):
            build(status=PasswordResetStatus.CONFIRMED)

    def test_an_awaiting_request_carrying_a_settled_moment_is_refused(self):
        with pytest.raises(InvalidPasswordResetSettledAtError):
            build(
                status=PasswordResetStatus.AWAITING,
                settled_at=REQUESTED + timedelta(minutes=1),
            )

    def test_a_settled_moment_that_is_not_a_moment_is_refused(self):
        with pytest.raises(InvalidPasswordResetSettledAtError):
            build(settled_at="yesterday")

    def test_a_confirmed_request_that_records_when_it_settled_is_accepted(self):
        """The only combination a store will ever write, and it must construct."""
        settled_at = REQUESTED + timedelta(minutes=2)

        reset = build(status=PasswordResetStatus.CONFIRMED, settled_at=settled_at)

        assert reset.is_settled is True
        assert reset.settled_at == settled_at

    def test_settled_asks_only_about_confirmed(self):
        """``EXPIRED`` is deliberately not asked about.

        It is derived and never stored, so a row is either waiting or spent. Asking
        ``status_as_of`` instead would make this property depend on a moment it was
        never given - and a property that needs a clock to answer is not a property.
        """
        assert build().is_settled is False


class TestThereIsNoWayToSpendARequestHere:
    """**The design, stated as an absence**, because this is the one somebody adds.

    ``EmailChange`` has no status transition either, for a reason that applies here
    unchanged: the check-and-write that spends a request has to be one statement or
    two concurrent confirms would both see ``AWAITING`` and both replace the password.
    The transition therefore lives in the store's claim, which sets ``status`` and
    ``settled_at`` together inside a single ``UPDATE`` guarded on the status and the
    window.

    A ``settle`` method on this aggregate would be a second route to spending a
    request - and it would be the route that *skips the window test*, since the window
    is a comparison against a moment the aggregate was not given. Two routes where one
    is weaker is how a single-use token becomes a two-use one, and here the thing it
    spends is the authorisation to replace an account's password.
    """

    def test_the_aggregate_has_no_method_that_spends_it(self):
        for forbidden in ("settle", "confirm", "apply", "spend"):
            assert not hasattr(PasswordReset, forbidden), (
                f"PasswordReset.{forbidden} would be a second route to spending a "
                f"request, and the one that skips the window test - see "
                f"SqlitePasswordResetRepository.claim_by_token_hash"
            )

    def test_the_status_field_is_what_a_reader_carries_and_not_a_transition(self):
        """Assigning it changes nothing about whether the store will accept a code.

        The claim is guarded on the row in the *database*, not on the object in
        memory, so a caller that set ``status`` here would be editing a copy that the
        next statement overwrites. Worth stating, because the mutable dataclass makes
        it look otherwise.
        """
        reset = build()

        reset.status = PasswordResetStatus.CONFIRMED
        reset.settled_at = REQUESTED + timedelta(minutes=1)

        assert reset.is_settled is True


class TestIssuing:
    def test_it_returns_a_request_and_a_token_that_match(self):
        reset, token = PasswordReset.issue(user_id=uuid4(), now=REQUESTED)

        assert reset.token_hash == hash_session_token(token)

    def test_the_token_is_not_the_stored_hash(self):
        """The property that makes a plain SHA-256 sufficient, asserted here too.

        The same guarantee ``Session``'s tests pin: presenting the stored value would
        hash the hash, which matches nothing. It matters more for this table than for
        any other, because what a copy of it would let somebody do is replace an
        account's password.
        """
        reset, token = PasswordReset.issue(user_id=uuid4(), now=REQUESTED)

        assert reset.token_hash != token

    def test_the_request_belongs_to_the_account_it_was_made_for(self):
        user_id = uuid4()

        reset, _ = PasswordReset.issue(user_id=user_id, now=REQUESTED)

        assert reset.user_id == user_id

    def test_it_starts_awaiting_and_nothing_has_changed(self):
        """The whole point of a pending row: the account still has its old password.

        Nothing in ``issue`` touches a credential - there is no credential to touch,
        only an id - and this is what says so. The mail that follows changes nothing
        either, which is why ``reset_message`` says so in as many words.
        """
        reset, _ = PasswordReset.issue(user_id=uuid4(), now=REQUESTED)

        assert reset.status is PasswordResetStatus.AWAITING
        assert reset.settled_at is None

    def test_it_lasts_the_standard_lifetime(self):
        """Fifteen minutes, and see ``PASSWORD_RESET_LIFETIME`` for why it is its own
        constant rather than a reuse of ``EMAIL_CHANGE_LIFETIME``.

        Pinned rather than left implicit because the three windows agree today and the
        agreement is a coincidence of three arguments - so a future change to either
        of the others should have to come here and say so.
        """
        reset, _ = PasswordReset.issue(user_id=uuid4(), now=REQUESTED)

        assert reset.expires_at == REQUESTED + PASSWORD_RESET_LIFETIME

    def test_the_lifetime_can_be_overridden(self):
        """So the expiry rule can be exercised without waiting fifteen minutes.

        The same reason the moment is passed in at all.
        """
        reset, _ = PasswordReset.issue(
            user_id=uuid4(), now=REQUESTED, lifetime=timedelta(seconds=1)
        )

        assert reset.expires_at == REQUESTED + timedelta(seconds=1)

    def test_one_request_cannot_have_two_ideas_of_when_it_was_made(self):
        """``now`` is passed in rather than read, and this is the consequence.

        The requested-at moment, the expiry and (later, in the store) the moment the
        claim records all come from one instant - and the same instant later becomes
        the ``updated_at`` on the new credential and the deadline printed in the mail.
        A factory that read the clock twice would produce requests whose two timestamps
        disagreed by however long the first half took, which is invisible until it is
        not.
        """
        reset, _ = PasswordReset.issue(user_id=uuid4(), now=REQUESTED)

        assert reset.requested_at == REQUESTED

    def test_two_requests_issued_in_the_same_moment_are_two_requests(self):
        """Different ids *and* different tokens, from one account at one instant.

        The id half is obvious; the token half is the one that matters. Asking twice
        in the same second must not mint the same credential - and the second request
        supersedes the first, so a shared token would make "the old code no longer
        works" untrue in exactly the case superseding exists for.
        """
        user_id = uuid4()

        first, first_token = PasswordReset.issue(user_id=user_id, now=REQUESTED)
        second, second_token = PasswordReset.issue(user_id=user_id, now=REQUESTED)

        assert first.password_reset_id != second.password_reset_id
        assert first_token != second_token
        assert first.token_hash != second.token_hash

    def test_the_token_is_the_same_kind_of_value_a_session_gets(self):
        """**The third caller of one discipline**, and the reason is the same one.

        256 bits of CSPRNG output hashed with SHA-256 is the whole of it, and this is
        the assertion that would fail if somebody gave this flow a code of its own - a
        six-digit number, say, which reads as friendlier and is brute-forceable at
        roughly a million attempts. Decision 167 makes that argument for the address
        change and it holds here with more force: a short code is guessable, there is
        still no rate limiter, and what a guess buys here is the account.

        ``token_urlsafe`` draws on ``/`` and ``-``, which is the property
        ``_prompt_code`` relies on when it strips what was pasted - no whitespace can
        occur in one of these, so stripping cannot damage a real code.
        """
        _, token = PasswordReset.issue(user_id=uuid4(), now=REQUESTED)

        assert len(token) >= 43
        assert not any(character.isspace() for character in token)
