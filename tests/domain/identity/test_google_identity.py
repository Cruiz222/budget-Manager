from dataclasses import FrozenInstanceError

import pytest

from app.domain.identity.exception import (
    InvalidGoogleIdentityEmailError,
    InvalidGoogleIdentityEmailVerifiedError,
    InvalidGoogleIdentitySubjectError,
)
from app.domain.identity.googleIdentity import GoogleIdentity

SUBJECT = "114988223156872419036"
EMAIL = "chinedu@example.com"


def build(**overrides) -> GoogleIdentity:
    """An identity with every field valid, so a test can spoil exactly one.

    Local rather than a conftest fixture, for ``test_user``'s reason: what is under
    test is this value's own refusals, and a shared helper would make the file
    depend on whatever else the shared fixture set carries.
    """
    fields = {"subject": SUBJECT, "email": EMAIL, "email_verified": True}
    fields.update(overrides)
    return GoogleIdentity(**fields)


class TestWhatAnIdentityHolds:
    def test_it_keeps_what_google_asserted(self):
        identity = build()

        assert identity.subject == SUBJECT
        assert identity.email == EMAIL
        assert identity.email_verified is True

    def test_it_is_frozen(self):
        """A value, so nothing downstream can edit what was asserted.

        The reason is that this object is the *evidence* for a decision made a few
        lines later - write this address onto an account, or refuse. A mutable
        one would let a step in between change the address a caller had already
        checked for duplicates, which is the shape of bug that leaves two accounts
        holding one address.

        ``FrozenInstanceError`` rather than ``Exception``, matching every other
        frozen value in this suite: the assertion is that the dataclass is frozen,
        and a bare ``Exception`` would also pass on a typo in the attribute name.
        """
        identity = build()

        with pytest.raises(FrozenInstanceError):
            identity.email = "someone@else.com"

    def test_it_holds_no_token(self):
        """It is what the token *said*, not the token.

        An id_token is a live bearer credential for about an hour. Keeping one on
        the value that travels up through the use case would put it in whatever
        logs or reprs that value - and this object is reported in failures, which
        a token must not be.
        """
        identity = build()

        assert not hasattr(identity, "id_token")
        assert not hasattr(identity, "token")


class TestTheSubject:
    @pytest.mark.parametrize("subject", [42, b"114988", ["114988"], None])
    def test_a_non_string_subject_is_rejected(self, subject):
        with pytest.raises(InvalidGoogleIdentitySubjectError):
            build(subject=subject)

    @pytest.mark.parametrize("subject", ["", "   ", "\t"])
    def test_an_empty_subject_is_rejected(self, subject):
        """Blank is refused, and it is the one value that would collide.

        The subject reaches a ``UNIQUE`` column on ``users``. A blank one stored
        for every account that arrived this way would mean the second Google
        signup failed against the first - a bug that appears only once somebody
        signs up, and only for the second person to do it.
        """
        with pytest.raises(InvalidGoogleIdentitySubjectError):
            build(subject=subject)

    def test_a_subject_is_not_stripped(self):
        """``User``'s rule for the same field, and its reason: nobody typed it.

        A subject is an opaque identifier Google issued. Trimming it here would be
        inventing a normalisation nobody asked for, and would mean a subject
        reported one way was looked up another - which the repository's exact
        comparison exists to avoid.
        """
        assert build(subject=" 114988 ").subject == " 114988 "


class TestTheEmail:
    @pytest.mark.parametrize("email", [42, b"a@b.com", ["a@b.com"], None])
    def test_a_non_string_email_is_rejected(self, email):
        with pytest.raises(InvalidGoogleIdentityEmailError):
            build(email=email)

    @pytest.mark.parametrize("email", ["", "   ", "\t"])
    def test_a_blank_email_is_rejected(self, email):
        """Refused as a type-guard rather than left to ``checked_email``, and the
        sentence is the reason.

        ``checked_email`` would fold this to ``""`` and refuse it too - but as
        ``InvalidUserEmailError``, which reads as "somebody typed an address that
        is not one". What actually happened is that an adapter read the wrong
        claim out of a token, and the sentence a failure produces is what gets it
        fixed. ``InvalidPhoneVerificationPhoneError`` splits the same two apart for
        the same reason.
        """
        with pytest.raises(InvalidGoogleIdentityEmailError):
            build(email=email)

    @pytest.mark.parametrize("email", ["nonsense", "@", "a@b"])
    def test_the_address_rule_is_not_checked_here(self, email):
        """**This class checks the type and not the address**, and the boundary is
        asserted rather than left to be inferred.

        Whether an address has an ``@`` is ``checked_email``'s rule and is
        deliberately shared - ``User`` applies it, ``change_email`` applies it, and
        ``SignUpWithGoogle`` applies it to this value's email before anything is
        written. What this value must not do is *also* enforce it, because a second
        copy is a second answer to "is this an address", and the two would drift.

        So these shapes are accepted here and refused one layer up, where the
        refusal carries the actionable sentence. This test is the boundary made
        visible, exactly as ``test_user``'s wide-email test is.
        """
        assert build(email=email).email == email

    def test_an_email_is_not_folded_here(self):
        """``fold_email`` is ``User``'s rule and is applied where the value is stored.

        Folding here as well would be a second implementation of "the same address
        written two ways is one account" - which is precisely what
        ``fold_email``'s own docstring exists to prevent. The one place a fold
        matters for this slice is the duplicate lookup, and ``find_by_email``
        applies it.
        """
        assert build(email="Chinedu@Example.com").email == "Chinedu@Example.com"

    @pytest.mark.parametrize("email", ["  chinedu@example.com  ", " chinedu@example.com\n"])
    def test_surrounding_whitespace_is_not_stripped_either(self, email):
        """Stated separately from the fold, because it is the half that looks
        harmless.

        Trimming an address is not a normalisation anybody would argue with - but
        it is still a second place where "what this address is" gets decided, and
        the caller folds it a line later anyway.
        """
        assert build(email=email).email == email


class TestWhetherTheAddressIsProved:
    """The field that turns an address into a credential, or refuses to.

    ``email_verified`` is not a detail. ``RequestPasswordReset`` mails a code to
    whatever address an account holds and deliberately has no credential check, so
    an account holding an address Google had not proved would have a working reset
    code sent to whoever controls that address. That is why the adapter must
    normalise this claim rather than pass it through, and why the type check here
    is strict rather than truthy.
    """

    def test_true_and_false_are_both_accepted(self):
        assert build(email_verified=True).email_verified is True
        assert build(email_verified=False).email_verified is False

    @pytest.mark.parametrize("value", ["true", "false", "", "yes", 1, 0, None])
    def test_a_non_boolean_is_rejected(self, value):
        """**``"false"`` is the case this exists for**, and it is not hypothetical.

        Google has shipped this claim as a string. ``bool("false")`` is ``True``, so
        an adapter that passed the claim through unnormalised - or a check here
        written as ``if not self.email_verified`` - would read "Google has not
        proved this address" as "Google has". The address would then be a working
        takeover path through the reset flow, and nothing downstream would notice,
        because every value in that path is a legitimate one.
        """
        with pytest.raises(InvalidGoogleIdentityEmailVerifiedError):
            build(email_verified=value)

    def test_the_string_false_does_not_read_as_true(self):
        """The trap named directly, so the failure is legible when it happens.

        A parametrised rejection above is the enforcement; this is the sentence
        somebody reads when they broke it. ``bool`` is the whole point - the
        assertion below is about Python rather than about this class.
        """
        assert bool("false") is True  # the trap, still true

        with pytest.raises(InvalidGoogleIdentityEmailVerifiedError):
            build(email_verified="false")
