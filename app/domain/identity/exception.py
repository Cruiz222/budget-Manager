"""Errors raised by the identity side of the domain.

Deriving from ``MoneyError`` rather than from ``Exception`` continues the rule
``app.domain.planning.exception`` states and ``app.domain.notifications.exception``
repeats: **a new exception belongs under the existing root, or every catch site
in the codebase has to be revisited.** The CLI catches ``MoneyError`` once, at
the top of ``main``, and turns it into ``error: ...`` with exit code 1.

This package is where that rule earns its keep rather than merely inheriting it.
A user is rejected at the *boundary* - a sign-up for ``not-an-email``, a session
whose subject no longer resolves - which is exactly the point at which a human is
watching and a traceback is least acceptable. An identity root outside the tree
would make the most user-facing refusals in the codebase the only ones that
escaped the handler.

The name ``MoneyError`` describes this least of all: there is no money in a
malformed email address. The same note as the notifications package applies -
renaming the root is worth doing on a day when nothing else is in flight, not in
the middle of a phase that touches every read in the codebase.
"""

from app.domain.money.exception import MoneyError
from app.domain.money.money import Money

from .limitKind import LimitKind


class IdentityError(MoneyError):
    """Base for every rejection the identity domain makes."""


# --- User (who a wallet belongs to) -----------------------------------------
# One class per refusal rather than a shared "invalid user", for the reason the
# notifications module gives: the caller that cares needs to tell them apart.
# ``InvalidUserEmailError`` in particular is raised at the CLI boundary, where
# the difference between "that is not an address" and "no such account" is the
# difference between fixing a typo and signing up.
class InvalidUserIDError(IdentityError):
    pass


class InvalidUserEmailError(IdentityError):
    pass


class InvalidUserPhoneError(IdentityError):
    """The number is blank, the wrong type, not digits, or implausibly long.

    **Deliberately not a phone number grammar**, following
    ``InvalidProfilePhoneError`` below: whether a number is *dialable* is a
    question for the SMS provider, and a regex here would refuse the
    international formats it had not thought of - the same argument
    ``checked_email`` makes for its ``@`` and nothing more.

    The distinction from ``InvalidProfilePhoneError`` is worth stating, because
    the two look alike and are not. That one guards a free-text KYC field nothing
    is ever sent to. This one guards an **identifier**: the value is folded to one
    canonical spelling, carries a ``UNIQUE`` constraint, and is the address a
    credential is delivered to. So it is stricter about *shape* - digits only, a
    plausible length - while remaining exactly as permissive about dialability as
    its sibling.
    """


class InvalidUserIdentifierError(IdentityError):
    """An account was built with neither an email address nor a phone number.

    **The one invariant on ``User`` that is about the pair rather than either
    member.** An account holding neither cannot be logged into, cannot be mailed,
    cannot be texted, and cannot be found by any query this system has - there is
    no flow that could ever reach it and no way for its owner to prove they are
    its owner. So it is not a state worth representing, and refusing it at
    construction means the repository's load path refuses it too.

    Note what is *not* required: both. An account with either identifier is
    reachable, and ``google_subject`` is deliberately not counted as one - it is
    an identity Google issued and a login Google performs, so on its own it is not
    something this system can deliver a credential to.
    """


#: The address is a well-formed address that cannot work.
#:
#: **The sibling of ``InvalidUserEmailError`` and deliberately not the same class.**
#: That one is the aggregate's shape rule - folded, non-empty, an ``@`` present -
#: and it answers "that is not an address". This one is the *minting* policy, and it
#: answers "that is an address, and it is one nobody can reach". The two are raised
#: in that order, always, so a person who typed ``not-an-address`` is told the thing
#: they can act on rather than the stricter thing that is also true of it.
#:
#: Raised by ``refuse_unusable_email``, whose module carries the full argument for
#: why the same narrow rule is a guard at the two sites that mint an address and
#: only a courtesy at ``InitiateDeposit``. It is *not* raised by ``User`` - not even
#: by ``User.change_email`` - because ``__post_init__`` also runs when a row is
#: loaded, so a rule enforced there would make every account already holding such an
#: address unreadable rather than merely stranded. See ``app.domain.identity.emailAddress``.
class UnusableEmailError(IdentityError):
    pass


class InvalidUserGoogleSubjectError(IdentityError):
    pass


class InvalidUserCreatedAtError(IdentityError):
    pass


class UserNotFoundError(IdentityError):
    pass


#: The address is already held by an account.
#:
#: Raised by sign-up, and it is the one refusal on this side that *does* confirm
#: an account exists - which is unavoidable, since the alternative is letting two
#: people register one address and discover it at the login form. ``LogIn``
#: deliberately does not behave this way; see ``InvalidCredentialsError``.
class DuplicateEmailError(IdentityError):
    pass


#: The number is already held by an account.
#:
#: ``DuplicateEmailError`` one identifier over, and it is raised from the same
#: place in the same shape - ``UNIQUE`` caught at the door rather than at the
#: store's integrity error, because the caller needs a sentence and not a
#: ``sqlite3.IntegrityError``.
#:
#: **Where it is raised is the interesting difference, and it is a whole step
#: later than its sibling.** An address is refused at the *request*, because
#: ``SignUp`` writes the account in the same breath as it names the address; a
#: number is refused at the **confirm**, because the request half deliberately
#: does not ask whether the number is taken. That is not an oversight to be tidied
#: up - asking would turn an unauthenticated endpoint into a cheap oracle over a
#: space as small as a phone number's, and answering it would cost nothing an
#: attacker does not already have while *telling* them costs the whole of the
#: answer. By the time this is raised the person has proved they hold the handset,
#: so the sentence it carries is one they are entitled to read.
class DuplicatePhoneError(IdentityError):
    pass


# --- PlainPassword (a password as it arrives, before it is hashed) -----------
# Two classes rather than one, and the split is the one ``User`` already makes
# between a value of the wrong *type* and a value of the wrong *shape*: "not a
# string at all" is a programming error at the boundary, while "too short" is a
# person who needs to be told what to type instead. A caller with something useful
# to say needs to know which it is holding.
class InvalidPasswordError(IdentityError):
    pass


class WeakPasswordError(IdentityError):
    pass


# --- PasswordCredential and Session (what is stored, and what proves it) -----
# The group below is type-guards rather than policy: every one of them catches a
# value of the wrong *kind* arriving where a moment or a hash belongs, which in
# practice means a repository that mapped a column wrongly. They are separate
# classes per field for the reason the top of this file gives, and because the
# class name is what a client sees (``errors._body`` puts it in ``error``) - so a
# shared ``InvalidMomentError`` would tell somebody debugging a 400 less than the
# field it names.
class InvalidCredentialHashError(IdentityError):
    pass


class InvalidCredentialUpdatedAtError(IdentityError):
    pass


class InvalidSessionIDError(IdentityError):
    pass


class InvalidSessionTokenHashError(IdentityError):
    pass


class InvalidSessionIssuedAtError(IdentityError):
    pass


class InvalidSessionExpiresAtError(IdentityError):
    pass


class InvalidSessionWindowError(IdentityError):
    pass


#: The token is unknown, malformed, or belongs to a session that has expired.
#:
#: One class for all three, and the reason is the same one decision 55 gives for
#: wallets: a caller who can tell "that token never existed" from "that token
#: expired an hour ago" has an oracle, and the useful half of that oracle is
#: learning which tokens were real. There is nothing a client can *do*
#: differently in the two cases either - both mean sign in again.
class InvalidSessionError(IdentityError):
    pass


#: The proof offered does not match an account.
#:
#: **Deliberately one refusal for two situations** - no account holds that
#: address, or the password is wrong for the account that does. ``LogIn`` raises
#: this for both, because a login that answers "no such account" is an
#: account-enumeration oracle: a caller can walk a list of addresses and learn
#: which ones are registered without ever guessing a password. This is decision 55
#: - a foreign wallet reports as a missing one - applied to the thing that hands
#: out identities rather than the things that hold money.
#:
#: **A third situation joined it with Google sign-in, and it is raised by
#: ``LogInWithGoogle``.** A verified Google token whose subject names no account
#: here is the same fact as an unknown address - the caller offered proof of an
#: identity this system does not have - so it gets the same sentence, and the two
#: use cases share the string rather than each composing one. The oracle argument
#: above is weaker for the Google path (a token is not guessable, so a caller
#: cannot walk a list of subjects) and the refusal is shared anyway, because a
#: second copy of a refusal sentence is a second thing to keep in step and there
#: is no reading under which the caller should learn which of the two failed.
class InvalidCredentialsError(IdentityError):
    pass


# --- EmailChange (an account's address, moved by somebody who proved it) -----
# The aggregate's own invariants, and the group mirrors the ``Session`` block above
# because the two aggregates are the same shape: a token, hashed at rest, with a
# window it is usable inside. Every one of these is a type-guard rather than a
# policy, and in practice means a repository that mapped a column wrongly.
#
# The settled/``settled_at`` pairing is the one exception to "type-guard" and is
# here anyway: it is the same pairing ``OutboundMessage`` validates, and the reason
# is the same - a row that says a request is finished while carrying no moment is a
# row whose two halves disagree, and the disagreement has no correct resolution. It
# is written out rather than shared with that aggregate for the reason
# ``instruction.py`` gives about coupling two aggregates to deduplicate a few lines.
class InvalidEmailChangeIDError(IdentityError):
    pass


class InvalidEmailChangeUserIDError(IdentityError):
    pass


class InvalidEmailChangeNewEmailError(IdentityError):
    pass


class InvalidEmailChangeTokenHashError(IdentityError):
    pass


class InvalidEmailChangeStatusError(IdentityError):
    pass


class InvalidEmailChangeRequestedAtError(IdentityError):
    pass


class InvalidEmailChangeExpiresAtError(IdentityError):
    pass


class InvalidEmailChangeWindowError(IdentityError):
    pass


class InvalidEmailChangeSettledAtError(IdentityError):
    pass


#: The new address is the one the account already holds.
#:
#: Refused rather than applied as a no-op, and what makes the difference worth an
#: exception is the mail. A request to move an address to itself would mint a
#: credential-bearing token and send it to the address its owner is already reading
#: mail at, for a change that would do nothing - and the confirm would then have to
#: decide whether "nothing visibly happened" counts as success. One refusal at the
#: request is cheaper than that question existing at all.
#:
#: Note it compares the *folded* forms, which is not a detail: ``Alice@Example.com``
#: is the address ``alice@example.com`` already is, so asking to change to it is
#: exactly this case wearing a different spelling.
class EmailUnchangedError(IdentityError):
    pass


#: The confirmation token is unknown, or names an account that is gone.
#:
#: One class for both, which is ``InvalidSessionError``'s arrangement and its
#: reason: a caller who can tell "no such token" from "a token whose subject
#: vanished" has an oracle, and there is nothing different to *do* about either -
#: both mean ask for a new one.
#:
#: **Deliberately not shared with the two below**, and this is the split worth
#: getting right. Those two mean the token was real and the request is over, so
#: they have remedies that are genuinely different: *ask again* against *you already
#: did this*. This one means the caller is holding something this system never
#: issued, which is the same fact a bad session token is.
class InvalidEmailChangeTokenError(IdentityError):
    pass


#: The request has already been answered.
#:
#: The state ``EmailChangeAlreadyUsedError`` describes exists because the row
#: *survives* being spent: deleting it on use would collapse this into
#: ``InvalidEmailChangeTokenError`` and lose the difference between "you already did
#: this" and "that code means nothing", which are two different things to be told.
class EmailChangeAlreadyUsedError(IdentityError):
    pass


#: The request's window closed before it was answered.
#:
#: Derived and never written - nothing sweeps a row into this state. The store
#: refuses the claim on an ``expires_at`` in the past and this is the class it
#: raises, exactly as ``ConfirmationExpiredError`` reports ``EXPIRED`` without any
#: instance of the aggregate ever being stored in it.
class EmailChangeExpiredError(IdentityError):
    pass


# ---------------------------------------------------------------------------
# Resetting a forgotten password
# ---------------------------------------------------------------------------
#
# The same three groups as above, one aggregate further along, and the split
# between them is the same split for the same reason. What is *not* here is worth
# counting: an ``EmailChange`` needs a class for a new address carried on the row,
# and this row carries no payload at all - the password a reset authorises is
# never written down, so there is nothing on it to validate.
#
# Every one of these is a type-guard rather than a policy, and in practice means a
# repository that mapped a column wrongly.
class InvalidPasswordResetIDError(IdentityError):
    pass


class InvalidPasswordResetUserIDError(IdentityError):
    pass


class InvalidPasswordResetTokenHashError(IdentityError):
    pass


class InvalidPasswordResetStatusError(IdentityError):
    pass


class InvalidPasswordResetRequestedAtError(IdentityError):
    pass


class InvalidPasswordResetExpiresAtError(IdentityError):
    pass


class InvalidPasswordResetWindowError(IdentityError):
    pass


class InvalidPasswordResetSettledAtError(IdentityError):
    pass


#: The reset token is unknown, or names an account that is gone.
#:
#: One class for both, for ``InvalidEmailChangeTokenError``'s reason exactly: a
#: caller who can tell "no such code" from "a code whose account vanished" has an
#: oracle, and the remedy is the same either way.
#:
#: **Deliberately not shared with the two below and not shared with the email
#: change trio either.** The three refusals of a reset are the same three a change
#: has, but a caller that catches one is catching a fact about *this* request, and
#: a shared vocabulary across two features would mean a change to one had to be
#: reasoned about in terms of the other. See ``EmailChangeStatus`` for the same
#: argument one layer down.
class InvalidPasswordResetTokenError(IdentityError):
    pass


#: The reset has already been answered.
#:
#: Reachable only because the row survives being spent - deleting it on use would
#: collapse this into ``InvalidPasswordResetTokenError`` and lose the difference
#: between "you already did this" and "that code means nothing". It is worth more
#: here than an email change's equivalent: a person who has already reset their
#: password and presents the same code again has, in all likelihood, simply
#: forgotten which mail they answered.
class PasswordResetAlreadyUsedError(IdentityError):
    pass


#: The reset's window closed before it was answered.
#:
#: Derived and never written, exactly as ``EmailChangeExpiredError`` is.
class PasswordResetExpiredError(IdentityError):
    pass


#: This installation has no mail account, so a forgotten password cannot be reset.
#:
#: **The member of this module that is not a fact about an account**, and it earns
#: its place by being the only honest answer available. The address change has a
#: fallback for a mail-less install - it applies on the password proof alone - and
#: a forgotten password has no equivalent proof, because the whole premise is that
#: the caller cannot demonstrate the one secret the account holds. So refusing is
#: not a missing feature; it is the only thing that is true.
#:
#: It is raised by the use case rather than by a presentation dependency, and that
#: is a deliberate departure from the arrangement ``dependencies.payment_provider``
#: uses for the payment settings. There is one mail configuration for the whole
#: installation and *two* presentations that need this refusal, and a guard written
#: into the HTTP layer would have to be written again in the CLI - which is the
#: shape ``fold_email`` exists to end. So the sentence is composed once, by the
#: builder that knows the settings, and both presentations render it through the
#: handlers they already have.
#:
#: The reason string names the missing variable, which is
#: ``describe_configuration``'s whole purpose: the failure mode of a misconfigured
#: notifier is silence, and "SMTP_HOST is not set" is the difference between a
#: two-minute fix and an afternoon of guessing.
class NoMailAccountError(IdentityError):
    pass


# ---------------------------------------------------------------------------
# Proving a number belongs to the person typing it
# ---------------------------------------------------------------------------
#
# The same three groups as above, one aggregate along, and the split between them is
# the same split for the same reason. What is *not* here is worth counting: a phone
# verification carries a payload of exactly one field - the number - and that field
# is the table's key rather than a value being moved, so the classes below are one
# fewer than ``EmailChange``'s for the same reason ``PasswordReset``'s are: there is
# nothing on the row to validate that the key does not already cover.
#
# Every one of these is a type-guard rather than a policy, and in practice means a
# repository that mapped a column wrongly.
class InvalidPhoneVerificationIDError(IdentityError):
    pass


class InvalidPhoneVerificationPhoneError(IdentityError):
    """The number on the row is not a string at all.

    **This is the type check and not the shape rule**, exactly as
    ``InvalidEmailChangeNewEmailError`` is: a number that is a string but badly
    shaped is refused by ``checked_phone`` as ``InvalidUserPhoneError``, which is the
    right answer because the shape rule is ``User``'s and is deliberately shared.
    This class exists for the case that rule cannot describe - a value that is not
    even a string, which is a mix-up in the store rather than a bad number.
    """


class InvalidPhoneVerificationTokenHashError(IdentityError):
    pass


class InvalidPhoneVerificationStatusError(IdentityError):
    pass


class InvalidPhoneVerificationRequestedAtError(IdentityError):
    pass


class InvalidPhoneVerificationExpiresAtError(IdentityError):
    pass


class InvalidPhoneVerificationWindowError(IdentityError):
    pass


class InvalidPhoneVerificationSettledAtError(IdentityError):
    pass


#: The code is unknown, or names a number whose row no longer exists.
#:
#: One class for both, for ``InvalidEmailChangeTokenError``'s reason exactly: a caller
#: who can tell "no such code" from "a code whose row vanished" has an oracle, and the
#: remedy is the same either way.
#:
#: **Deliberately not shared with the two below, and not shared with the request
#: trios above either.** The three refusals here are the same three a reset has, but a
#: caller that catches one is catching a fact about *this* request, and a shared
#: vocabulary across two features would mean a change to one had to be reasoned about
#: in terms of the other. See ``PhoneVerificationStatus`` for the same argument one
#: layer down.
class InvalidPhoneVerificationTokenError(IdentityError):
    pass


#: The verification has already been answered.
#:
#: Reachable only because the row survives being spent - deleting it on use would
#: collapse this into ``InvalidPhoneVerificationTokenError`` and lose the difference
#: between "you already did this" and "that code means nothing". It is the one of the
#: three siblings where that difference is **least** useful to the person and still
#: worth keeping: a reset is re-presented by someone who forgot which mail they
#: answered, whereas a spent verification here means the account was created, so the
#: actionable response is to log in rather than to read this at all. It stays for the
#: reason the row stays - the message is not this class's to write, and a caller that
#: wants to say "you already have an account, log in" needs to be able to tell.
class PhoneVerificationAlreadyUsedError(IdentityError):
    pass


#: The verification's window closed before it was answered.
#:
#: Derived and never written, exactly as ``EmailChangeExpiredError`` and
#: ``PasswordResetExpiredError`` are.
class PhoneVerificationExpiredError(IdentityError):
    pass


#: This installation has no SMS account, so a number cannot be proven.
#:
#: ``NoMailAccountError`` one channel over, and its whole argument applies unchanged:
#: it is a fact about the *installation* rather than about an account, the reason
#: string is composed by the builder that knows the settings and names the missing
#: variable, and it is raised by the use case rather than by a presentation dependency
#: so that the CLI and the API render one identical sentence. Read that class for why
#: each of those is so.
#:
#: **What is different is that this one has no fallback at all, where mail's has
#: one.** A mail-less installation can still apply an address change - the request
#: applies on the password proof alone - so ``NoMailAccountError`` names the one flow
#: that genuinely cannot proceed. A number has no second proof: the reason to believe
#: somebody holds a handset *is* a message arriving on it, so an installation that
#: cannot send texts cannot verify numbers, and refusing is not a missing feature but
#: the only thing that is true. The same applies to a password reset that names a
#: number rather than an address, which is why this class is raised by more than one
#: use case rather than by the signup alone.
class NoSmsAccountError(IdentityError):
    pass


# ---------------------------------------------------------------------------
# Signing in with Google
# ---------------------------------------------------------------------------
#
# **The first group in this file that is not about an aggregate of this system.**
# Every class above describes a row this codebase owns and can map wrongly. The
# three type-guards below describe a *report* - what Google asserted in a token it
# signed - and the four refusals after them describe what this system does with
# such a report. That is why the section is split where it is, and the split is
# the same one every group above makes: a type-guard means the adapter built a
# malformed value out of claims, and a refusal is a real answer to a real caller.
#
# Read with ``app.domain.identity.googleIdentity``, which carries the argument for
# why the verification result is a value object at all rather than a tuple, and
# with ``app.domain.identity.googleIdentityVerifier``, which carries the argument
# for why it reports facts rather than enforcing this system's policy about them.
class InvalidGoogleIdentitySubjectError(IdentityError):
    """The identity's ``sub`` is not a non-empty string.

    ``InvalidUserGoogleSubjectError`` one layer up and deliberately not the same
    class. That one guards a value this system is about to *store* against a
    ``UNIQUE`` column; this one guards a claim that arrived over the network. The
    two happen to test the same shape today - Google issues the same opaque
    string either way - but they answer to different failures, and collapsing
    them would mean a change to how tokens are read had to be reasoned about in
    terms of how accounts are written.
    """


class InvalidGoogleIdentityEmailError(IdentityError):
    """The identity's ``email`` is not a non-empty string.

    **The type check and not the address rule**, for the reason
    ``InvalidPhoneVerificationPhoneError`` gives: an address that is a string but
    badly shaped is refused by ``checked_email`` as ``InvalidUserEmailError``,
    because that rule is ``User``'s and is deliberately shared. This class exists
    for what that rule cannot describe - a claim that is not even a string, or one
    that is blank, which is an adapter reading the wrong claim rather than a
    person typing a bad address.
    """


class InvalidGoogleIdentityEmailVerifiedError(IdentityError):
    """The identity's ``email_verified`` is not a real ``bool``.

    **A real ``bool`` rather than something merely truthy, and that strictness is
    the whole of this class.** The obvious reading of a claim called
    ``email_verified`` is ``if claims["email_verified"]``, which is what makes
    this the one field here where being permissive is actively dangerous: Google
    has shipped the claim as the *string* ``"false"``, and ``bool("false")`` is
    ``True``. So the adapter must normalise, and this class is what refuses the
    adapter that forgot - rather than letting ``"false"`` read as "Google says
    this address is proved", which is the one reading that turns the address into
    a credential for the account.
    """


#: The id_token is unknown, malformed, expired, or not ours.
#:
#: **One class for all of those**, and the reason is ``InvalidSessionError``'s:
#: the remedy is identical in every case - obtain a fresh token - and telling them
#: apart would describe a forgery to the forger. A caller who could distinguish
#: "wrong signature" from "expired" would learn which part of a guess was close,
#: which is the only feedback an attacker here has any use for.
#:
#: The three cases that are *not* about the caller are folded in deliberately. A
#: token minted for a different application, a token from a different issuer, and
#: a token signed by a key that is not Google's all mean the same thing to this
#: system - this is not proof of anything - and a named class for each would be
#: three ways to say one sentence.
#:
#: Derived from ``IdentityError`` rather than from a provider-specific root
#: because it is a *refusal about a request*, which is exactly what everything
#: else in this file is. The class below is the one that is about the provider.
class InvalidGoogleTokenError(IdentityError):
    pass


#: Google says it has not proved the address on this identity.
#:
#: **The load-bearing refusal of this feature**, and it is a policy about a report
#: rather than a check inside the verifier - which is why it is here and not
#: folded into the adapter. The chain it closes runs through code that already
#: ships: ``RequestPasswordReset`` mails a code *to the address the account holds*
#: and deliberately has no credential check, precisely so that an account with no
#: password can set its first one. So an account holding an address Google had not
#: proved would have a working reset code mailed to whoever controls that address
#: - and that person, not the Google account's holder, could then take the account.
#:
#: It is its own class rather than an ``InvalidGoogleTokenError`` because the two
#: mean different things to the person: that one says "your proof is no good, get
#: another", and this one says "your proof is fine and Google has not finished
#: proving your address". The remedy for this is to use a different Google
#: account, which is not something the caller could infer from the other sentence.
class UnverifiedGoogleEmailError(IdentityError):
    pass


#: This Google account already has an account here.
#:
#: Raised by the sign-up, and it is the sibling of ``DuplicateEmailError`` rather
#: than a reuse of it - the two are different collisions with different remedies.
#: That one says the *address* is taken and the answer is to sign up with another
#: one; this one says the *Google identity* is taken and the answer is to log in.
#: A caller that could not tell them apart would be told to do the wrong thing.
#:
#: It is reachable at sign-up only because a client called the create route for an
#: account that already exists - the log-in route is where this identity belongs,
#: and a caller that meant to log in has made a mistake this sentence names.
class DuplicateGoogleSubjectError(IdentityError):
    pass


#: This installation could not reach Google, so a token could not be checked.
#:
#: **Deliberately a different grade from ``SmsProviderError``, which is a 400**, and
#: the difference is the kind of question being asked rather than the kind of
#: provider answering. An SMS refusal is about *the message* - this handset, this
#: sender id, this balance - so a caller can fix it by asking differently. A JWKS
#: fetch that fails is a fact about the *installation*: Google's keys could not be
#: retrieved because this server has no route to them, and no change to the
#: request would alter that. So it grades with ``NoMailAccountError`` and
#: ``NoSmsAccountError`` as a 503, which is the honest answer, rather than telling
#: the caller their token was wrong when it was never read.
#:
#: It exists at all because the port cannot express "I could not ask" as a
#: ``GoogleIdentity``. A verifier that returned something would be asserting a
#: fact it does not have.
class GoogleProviderError(IdentityError):
    pass


#: This installation has no Google client id, so no token can be judged.
#:
#: **The third member of the "this deployment cannot do that" group**, after
#: ``NoMailAccountError`` and ``NoSmsAccountError``, and it is the same fact one
#: configuration over: a flow that cannot run at all until an operator sets a
#: variable, refused in words that name the variable - see
#: ``describe_google_configuration``.
#:
#: **It is distinguishable from ``GoogleProviderError`` above on purpose, and the
#: two will look alike at a glance.** That one says Google could not be reached;
#: this one says there is nothing here pointed at Google at all. Both are 503s
#: because both are facts about the installation rather than about the request,
#: but the remedies are different people's work - one is the network, the other is
#: a missing line in a config - and an operator reading a log needs to know which.
#:
#: It is raised before anything is verified, and unlike the two channel refusals
#: it is raised without a token having been read at all: a client id is what
#: verification is *against*, so there is no such thing as a partial attempt when
#: one is absent.
class NoGoogleAccountError(IdentityError):
    pass


# ---------------------------------------------------------------------------
# Profile (who the account holder is, as opposed to which account they hold)
# ---------------------------------------------------------------------------
#
# The first group in this file that is mostly *policy* rather than type-guard,
# and the difference is worth marking: a session's field checks exist to catch a
# repository that mapped a column wrongly, whereas a profile's exist to catch a
# person who typed something that is not a name. The two are split below rather
# than mixed, so a caller can tell which kind of refusal it is holding.
#
# One class per field, for the reason the top of this file gives and for a
# sharper version of it here: these refusals reach a form, and "that is not a
# phone number" is only useful if it says *which* field it is about. A shared
# ``InvalidProfileFieldError`` would make every one of them say the same thing.
class InvalidProfileUserIDError(IdentityError):
    pass


class InvalidProfileCreatedAtError(IdentityError):
    pass


class InvalidProfileUpdatedAtError(IdentityError):
    pass


class InvalidProfileWindowError(IdentityError):
    pass


class InvalidProfileDisplayNameError(IdentityError):
    """The display name is missing, blank, the wrong type, or absurdly long.

    The only *required* field on a profile, which is why this class exists at
    all while the five identity fields below it are optional. See
    ``app.domain.identity.profile`` for why a profile with nothing in it is
    refused rather than stored.
    """


class InvalidProfileDateOfBirthError(IdentityError):
    """The date of birth is not a ``date`` - and ``datetime`` is refused too.

    **The one field in this codebase where ``datetime`` is the wrong answer.**
    Every other aggregate refuses a bare ``date`` because a moment stamped with
    a day has lost its time; a birth date has no time to lose, and accepting a
    ``datetime`` would store a midnight nobody was born at. See
    ``Profile``'s docstring, which carries the argument and its inversion.
    """


class InvalidProfileLegalNameError(IdentityError):
    """A legal name is blank, the wrong type, or absurdly long.

    Covers both halves - ``legal_first_name`` and ``legal_last_name`` - because
    they share one rule and a caller acts on them identically. Contrast the
    field classes above and below, which are separate because their rules
    genuinely differ.
    """


class InvalidProfilePhoneError(IdentityError):
    """The phone number is blank, the wrong type, or absurdly long.

    **Deliberately not a phone number grammar.** What this refuses is the value
    that is obviously not one; whether a number is dialable is a question for
    the SMS provider that does not exist yet, and a regex here would refuse the
    international formats it had not thought of - the same argument
    ``checked_email`` makes for its ``@`` and nothing more.
    """


class InvalidProfileCountryError(IdentityError):
    """The country is not a two-letter code.

    A *shape* rule and not a membership check, and the distinction is the whole
    of the class: see ``Profile._checked_country`` for why a hand-written list
    of ISO 3166 codes is the wrong thing to build here.
    """


class InvalidProfileAddressError(IdentityError):
    """The address line is blank, the wrong type, or absurdly long.

    Note what is **not** validated: that the address is real. An address is
    confirmed by mailing something to it or by the identity check that has not
    been built, and no amount of parsing changes that - ``checked_email``'s
    argument applied to a street.
    """


# ---------------------------------------------------------------------------
# Tier limits (what an account is allowed to move)
# ---------------------------------------------------------------------------
class TierLimitExceededError(IdentityError):
    """A movement was refused because it breached the account's tier limits.

    **Carries the three facts an audit needs, and not just a sentence.** ``kind``
    says which ceiling refused it, ``limit`` says what the ceiling was, and
    ``attempted`` says what the value would have been - so "was this refused for
    the per-transaction ceiling or the daily cap?" is answerable from the row
    rather than by reading prose, which is ``RunBlockReason``'s argument for
    being an enum arriving one layer over at the exception that reports it.

    ``attempted`` means something slightly different per kind and that is
    deliberate rather than sloppy: for a per-transaction breach it is the
    movement, for a daily breach it is the day's total *including* this movement,
    and for a balance breach it is the balance the wallet would have held. In
    every case it is the number that was compared against ``limit`` - which is
    the only reading under which the two fields can be checked against each other
    later without re-deriving which comparison this was.

    Deriving from ``IdentityError`` puts it under ``MoneyError``, and that is
    load-bearing rather than tidy: ``WalletService._run`` catches ``MoneyError``
    to record a refusal in the audit trail and commit it, and a limit refusal has
    to travel that same path. An exception root outside the tree would be a
    refusal the ledger never hears about, which is the one thing a financial
    control must not be.
    """

    def __init__(
        self, kind: LimitKind, limit: Money, attempted: Money, message: str
    ):
        super().__init__(message)
        self.kind = kind
        self.limit = limit
        self.attempted = attempted

