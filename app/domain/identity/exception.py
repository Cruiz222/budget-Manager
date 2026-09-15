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


#: The address and password do not match an account.
#:
#: **Deliberately one refusal for two situations** - no account holds that
#: address, or the password is wrong for the account that does. ``LogIn`` raises
#: this for both, because a login that answers "no such account" is an
#: account-enumeration oracle: a caller can walk a list of addresses and learn
#: which ones are registered without ever guessing a password. This is decision 55
#: - a foreign wallet reports as a missing one - applied to the thing that hands
#: out identities rather than the things that hold money.
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
