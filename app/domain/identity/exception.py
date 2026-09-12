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
