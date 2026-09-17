"""What verifying a Google id_token must do - the second outbound port.

Read with ``app.domain.notifications.smsChannel``, because the two are the same
design stated for two providers. Everything that port says about *direction* - a
repository is something the application asks for data, a channel is something it
tells to act - applies here with one word changed, and everything it says about
**raising being the interface rather than an error path** applies verbatim.

**An ABC here, where ``Deliverable`` is a ``Protocol``, for ``SmsChannel``'s
reason.** ``Deliverable`` is a Protocol because the records that get mailed are
unrelated to each other and a shared base would invent a relationship they do not
have. A verifier is the opposite case: every implementation of this is a Google
verifier - the real one and the fake in the tests are the only two, and both
would say so in their bases.

**``verify`` returns an identity or raises, and the return type is where that is
decided.** It is typed ``GoogleIdentity`` rather than ``GoogleIdentity | None``,
which is the shape worth arguing because ``None`` would have been the obvious
one. There is no such thing as "no identity" here: a token either verifies, in
which case Google asserted three facts about somebody, or it does not, in which
case there is nothing to report and the caller must refuse. A ``None`` return
would collapse those into one value and leave the caller unable to tell a forged
token from a bug - and it would put the refusal in every call site rather than
in the adapter that knows why the token failed.

**Two ways to raise, and they are different kinds of fact.** An
``InvalidGoogleTokenError`` is about *the token presented* - malformed, expired,
signed by something else, or minted for a different application - and a caller
answers it with "your proof is no good". A ``GoogleProviderError`` is about *the
installation*: Google's keys could not be fetched, so the token was never read at
all. Those are a 401 and a 503 respectively, and the difference is why this port
cannot report both as one failure. See ``GoogleProviderError`` for why the second
is graded with the missing-account errors rather than with the SMS provider's.

**The port reports facts and the use case applies policy.** ``GoogleIdentity``
carries ``email_verified`` as a field rather than this port refusing an unproved
address itself, and that split is deliberate - see ``googleIdentity``, which
carries the argument in full. The short version is that the reason to care about
that flag is a property of *this* system's password-reset flow rather than of
Google, so the rule belongs in the layer that can read the reset flow beside it.
"""

from abc import ABC, abstractmethod

from app.domain.identity.googleIdentity import GoogleIdentity


class GoogleIdentityVerifier(ABC):
    """Defines what verifying a Google id_token must do - the inbound port."""

    @abstractmethod
    def verify(self, id_token: str) -> GoogleIdentity:
        """Check this token's signature and claims, or raise.

        Returning normally means Google signed this token, it was minted for *this*
        application, it is inside its validity window, and the three facts on the
        returned identity are what it asserted. Raising means none of that is
        established - and never means the token was merely uninteresting.

        **The signature and the claims are one operation rather than two**, which is
        why there is no ``signature_is_valid`` beside this. A caller that checked a
        signature and then read claims separately would be a caller that could
        forget the second half, and the half that would get forgotten is the
        audience check - the one whose absence turns every Google token on the
        internet into a credential for this system.

        Takes the raw token and not a set of decoded claims. Decoding is the part
        that must not happen before verification, so accepting claims here would
        invite a caller to decode first - and a decoded-but-unverified token is
        attacker-controlled input wearing the shape of a trusted value.
        """
        pass
