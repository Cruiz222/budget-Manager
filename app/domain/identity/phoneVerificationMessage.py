"""The one message a phone signup produces.

**One rather than two, and the asymmetry with ``passwordResetMessage`` is the
whole of what this module has to say.** That pair is a credential-in-transit and a
warning-about-something-already-done, because a reset *changes* an account that
already exists. A verification creates the account, so there is no later moment to
announce - the second text a person gets about this number, if they get one at
all, is whatever the product chooses to send a new account holder, and that is not
this module's to write. Sending a "your number is verified" text here would be
telling somebody a fact they learned by answering the code.

**The text carries the token verbatim, and that is the decision step 3 left
open.** ``PhoneVerification.issue`` names the two readings - a link that hands the
token back, or a short numeric code traded for the token at the gate - and refuses
to choose between them, because both are about the *message*. This is where the
message is written, so this is where it is answered: **the token itself travels**.

The reasoning is ``reset_message``'s, and it is worth restating rather than
pointing at, because the temptation to shorten is much stronger for a text than
for a mail:

- **There is no public-URL setting to build a link from.** ``passwordResetMessage``
  states it as a decision rather than an unfinished feature: any URL here would be
  invented, right on one installation and wrong on every other. A text with a link
  has exactly the same problem and one more - a phone resolves it by opening a
  browser, so a wrong host is not a sentence somebody reads past, it is a dead end
  with a browser in front of it.
- **A short code would trade away the only secret guarding a unique
  identifier.** Six digits are brute-forceable by anybody who can send requests,
  and there is still **no rate limiter in this system**. That is the same reason
  the token is 256 bits, and shortening it at the last step - in the message, after
  the row has already hashed the real thing - would leave the length intact in the
  table and irrelevant in practice.
- **The honest cost is length**, and it is not hidden here: a 64-character token
  plus the sentences around it runs past a single 160-character SMS segment, so
  this arrives as a multi-part message on Termii's `generic` channel. That is a
  bill and a small awkwardness, and it is the cheaper side of the trade against a
  guessable code.

What the module deliberately does **not** carry: the number it was sent to. The
message is addressed by the channel rather than naming its destination, for
``__str__``'s reason on the aggregate - a phone number is the smallest enumerable
identifier in this product, and a text is stored, forwarded and screenshotted in
ways a mail is not.
"""

from dataclasses import dataclass

from .phoneVerification import PhoneVerification


@dataclass(frozen=True)
class PhoneVerificationSms:
    """One composed message, in the two fields an SMS channel reads.

    **Two fields and not ``Deliverable``'s three**, which is the entire reason
    ``SmsMessage`` exists as a second port rather than the mail one being reused:
    an SMS has no subject, and ``subject=""`` would be inventing a field to satisfy
    a Protocol whose whole purpose is to ask for the shape rather than the family.
    This class satisfies ``SmsMessage`` structurally, like every other message
    record in the system satisfies the shape it is passed as.

    Frozen because it is a *value*: composed once, handed to a channel, never
    edited.
    """

    recipient: str
    body: str


def verification_code_message(
    verification: PhoneVerification, token: str
) -> PhoneVerificationSms:
    """The message that proves the sender is holding the handset, carrying ``token``.

    The token is the whole of the authorisation to create the account, so this text
    is a credential in transit - which is why the caller sends it synchronously and
    lets a failure propagate, exactly as ``reset_message``'s caller does. Letting
    that failure pass quietly would leave the person watching a handset for a text
    that is not coming, with no account and no way to ask again for a different
    reason.

    The recipient is the *stored* folded number rather than the string that was
    typed, which matters the moment ``fold_phone`` changed anything: the row holds
    the folded spelling and that is what the provider is handed - see
    ``RequestPhoneVerification``, which passes ``verification.phone``.

    The deadline is printed from ``verification.expires_at`` rather than from
    ``PHONE_VERIFICATION_LIFETIME``, for ``reset_message``'s reason: what a reader
    needs is not "ten minutes" but *when*, and a person holding a phone is not
    holding a stopwatch. It is also the one place the ten-minute window is worth
    being short in - a text that arrives while somebody is at the handset is
    readable immediately, so a deadline an hour out would only be a number nobody
    is looking at.

    **The last paragraph says what has not happened, and it is true in a way the
    reset mail's equivalent is not.** That one has to reassure a reader that a
    change they did not ask for was not applied; here the stronger fact holds -
    nothing exists to be applied *to*, because the account is what answering this
    creates. Somebody who receives this and ignores it has an account they never
    asked for only after they type the code back.
    """
    deadline = verification.expires_at.isoformat(timespec="minutes")
    return PhoneVerificationSms(
        recipient=verification.phone,
        body=(
            f"Somebody asked to create an account with this number.\n\n"
            f"If that was you, present this code to create your account:\n\n"
            f"    {token}\n\n"
            f"The code can be used once, and expires at {deadline}.\n\n"
            f"If you did not ask for this, nothing has happened and nothing will: "
            f"no account exists until this code comes back. You can ignore this "
            f"message.\n"
        ),
    )
