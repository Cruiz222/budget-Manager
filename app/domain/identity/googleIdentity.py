"""What Google asserted, once Google's signature over it has been checked.

**This is a report, not a row, and that is the first thing to know about it.**
Every other aggregate in this package describes state this system owns: something
it wrote, can read back, and can be wrong about. A ``GoogleIdentity`` describes
what a *third party* said about somebody, read once and acted on in the same
breath - it is never stored, never queried and never loaded from a column. That
is why it is frozen where ``User`` is not, and why nothing here has a method.

**Three fields, and each is a different kind of fact.** ``subject`` is the
identifier - Google's ``sub``, opaque, stable for the life of the Google account
and unique to it, which is why it and not the address is what an account is found
by. ``email`` is the address Google reports. ``email_verified`` is whether Google
has *proved* that address, which sounds like a detail and is not - see below.

**``email_verified`` is a field rather than a refusal inside the verifier, and the
arrangement is deliberate.** The port reports facts and the use case applies this
system's policy about them, which is ``PaymentProvider.outcome_for``'s
arrangement one provider over: that adapter reports one of three answers and
``reconcile`` decides what they mean. The reason to keep the split here is that
the policy is not really about Google at all - it is about *this* system's reset
flow, which mails a code to whatever address an account holds and deliberately has
no credential check so that a passwordless account can set its first password. An
address Google had not proved, stored as an account's address, would therefore be
a working account-takeover path via ``RequestPasswordReset``. That argument
belongs in the layer where the reset flow is readable, which is why the refusal
lives in ``SignUpWithGoogle`` and is a named error rather than a line in an
adapter. See ``UnverifiedGoogleEmailError``, which carries it in full.

**The email is not folded here, and that is a decision rather than an omission.**
``fold_email`` is ``User``'s rule, applied on construction, and it is also applied
by ``find_by_email`` - so the one place a fold matters for this slice, the
duplicate lookup, gets it from the query. Folding here as well would be a second
copy of "the same address written two ways is one account", which is the exact
thing ``fold_email``'s own docstring exists to prevent. What this class checks is
the *type*, which is the thing the fold cannot: ``checked_email`` would refuse
``""`` with an ``InvalidUserEmailError`` that reads like a person typed a bad
address, when what happened is that an adapter read the wrong claim out of a
token.

**``subject`` is refused when blank but is not stripped**, matching ``User``'s
treatment of the same value for the same reason: it is an opaque identifier Google
issued, so trimming it would be inventing a normalisation nobody asked for. The
blank test is not that normalisation - it is refusing a claim that is not an
identifier at all, since ``""`` as a subject is the one value that would collide
with another account's on the ``UNIQUE`` column it eventually reaches.
"""

from dataclasses import dataclass

from .exception import (
    InvalidGoogleIdentityEmailError,
    InvalidGoogleIdentityEmailVerifiedError,
    InvalidGoogleIdentitySubjectError,
)


@dataclass(frozen=True)
class GoogleIdentity:
    """One person, as Google described them in a token it signed.

    Frozen because it is a *value*: what was asserted at a moment, which nothing
    in this system has any business editing afterwards. The type-guards in
    ``__post_init__`` mean in practice that the adapter read the wrong claim or
    read one in the wrong shape - which is a bug rather than a caller's mistake,
    exactly as the session and email-change guards are - so none of them is a
    refusal a client could ever see.
    """

    subject: str
    email: str
    email_verified: bool

    def __post_init__(self) -> None:
        if not isinstance(self.subject, str):
            raise InvalidGoogleIdentitySubjectError("invalid google subject")

        # Refused when blank, and *not* stripped when stored - ``User``'s rule for
        # the same field, and its reason: an opaque identifier is not a handle a
        # human types, so normalising it would invent a value Google never issued.
        if not self.subject.strip():
            raise InvalidGoogleIdentitySubjectError("google subject must not be empty")

        if not isinstance(self.email, str):
            raise InvalidGoogleIdentityEmailError("invalid google email")

        # Blank rather than merely empty, so a claim of ``"   "`` is refused here
        # as the non-address it is. Left to ``checked_email`` it would fold to
        # ``""`` and be refused there - but as ``InvalidUserEmailError``, which
        # tells an operator that somebody typed a bad address when the truth is
        # that an adapter read the wrong claim. The sentence a failure produces is
        # the thing that gets this fixed, which is the same argument
        # ``InvalidPhoneVerificationPhoneError`` makes for its split.
        if not self.email.strip():
            raise InvalidGoogleIdentityEmailError("google email must not be empty")

        # ``isinstance`` and not truthiness, which is the entire reason this field
        # has a guard of its own. ``bool("false")`` is ``True``, and Google has
        # shipped this claim as the string ``"false"`` - so a permissive test here
        # would read "Google has not proved this address" as "Google has", and the
        # address would then be a takeover path through the reset flow. The strict
        # test is what forces the adapter to normalise rather than pass the claim
        # through, and it fails at the seam where that adapter is tested.
        if not isinstance(self.email_verified, bool):
            raise InvalidGoogleIdentityEmailVerifiedError(
                "google email_verified must be a boolean"
            )
