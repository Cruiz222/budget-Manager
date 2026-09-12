from dataclasses import dataclass

from .exception import InvalidPasswordError, WeakPasswordError

#: The shortest password this system will accept.
#:
#: Eight characters is the floor every current guideline converges on, and it is
#: a *length* rule rather than a composition rule - no demand for a digit and a
#: symbol. That is deliberate: composition rules push people towards
#: ``Password1!``, which satisfies every one of them and is in every cracking
#: dictionary, while length is the thing that actually costs an attacker. The
#: rule refuses the password that is obviously nothing rather than trying to
#: measure how good a password is, which nothing can do from the inside.
MINIMUM_LENGTH = 8

#: The longest password this system will accept, and the reason is not storage.
#:
#: Nothing here truncates - unlike bcrypt, whose well-known 72-byte limit silently
#: discards the rest of a long passphrase, argon2 has no such boundary. The limit
#: exists because hashing costs *work by design*, so an unbounded input turns a
#: login endpoint into a way to make the server burn memory and CPU on request.
#: A kilobyte is far past any passphrase a person types and far short of anything
#: that matters to argon2.
MAXIMUM_LENGTH = 1024


@dataclass(repr=False)
class PlainPassword:
    """A password as it arrives, before anything has been done to it.

    **This object never reaches storage, and it is not what is stored.** What
    lands in the database is the encoded hash, wrapped in a
    ``PasswordCredential``; this is the other end of that journey, the value that
    exists for the length of one login or one sign-up and is then dropped. The two
    are separate types rather than one string in two roles because the rules are
    different and so is the danger: this one must never be written down anywhere,
    and the hash must be written down exactly once.

    **It is not stripped.** A password is not a handle a person types - it is a
    secret they chose, and every character in it is part of it, including a
    trailing space. Trimming would mean a password set with a space at the end is
    silently altered, and the person who set it has no way to know which one they
    actually have. (This is the opposite of the call ``User`` makes for an email,
    and the difference is the point: an address is an identifier compared against
    other systems, and a password is compared against nothing but itself.)

    **The repr is redacted, and that is a safety property rather than manners.**
    A dataclass prints its fields, so without this, one ``f"{password}"`` in a
    traceback hands the value to whatever collects the traceback - and this
    codebase has two places that turn an exception into text a user sees
    (``errors._detail``, ``cli._describe``) plus whatever logging is added later.
    Redacting here means those places cannot leak it even by accident.
    """

    secret: str

    def __post_init__(self):
        if not isinstance(self.secret, str):
            raise InvalidPasswordError("invalid password")

        # Emptiness is checked before length so that the two refusals stay
        # distinguishable. "" is not "too short" in any useful sense - it is a
        # caller that passed nothing - and collapsing them would make a bug at
        # the boundary look like a user choosing a bad password.
        if not self.secret:
            raise InvalidPasswordError("password must not be empty")

        if len(self.secret) < MINIMUM_LENGTH:
            raise WeakPasswordError(
                f"password must be at least {MINIMUM_LENGTH} characters"
            )

        if len(self.secret) > MAXIMUM_LENGTH:
            raise WeakPasswordError(
                f"password must be at most {MAXIMUM_LENGTH} characters"
            )

    def __repr__(self) -> str:
        """Never the value - see the class docstring.

        The length is not shown either. It is the kind of detail that seems
        harmless and is not: a leaked length narrows the search space for anyone
        attacking a hash, and a redaction that leaks a little is harder to trust
        than one that leaks nothing.
        """
        return "PlainPassword(<redacted>)"
