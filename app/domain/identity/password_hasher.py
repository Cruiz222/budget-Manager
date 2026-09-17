from abc import ABC, abstractmethod

from .password import PlainPassword

#: A password hash whose preimage was thrown away, spent when there is no stored
#: hash to compare a presented password against.
#:
#: **What it is for.** ``LogIn`` refuses an unknown identifier and a wrong password
#: with one sentence, because telling them apart lets anybody with a list of
#: addresses learn which are registered - decision 55. The *words* were never the
#: whole of that protection: a branch that finds no credential has nothing to hash
#: against, so it used to return in microseconds where a wrong password took tens
#: of milliseconds, and the difference is measurable from outside. Comparing
#: against this instead spends the same work on both branches, so the two answers
#: cost the same order of time. The result is discarded - see ``LogIn._settle``,
#: which refuses on ``encoded is None`` whatever this comparison returns.
#:
#: **It lives in the domain, and it is a literal, and both are load-bearing.**
#: ``LogIn`` is application code that may import only the domain, and it holds a
#: ``PasswordHasher`` by its port rather than by its adapter - so the decoy cannot
#: be the adapter's property without the caller naming argon2, and it cannot be
#: computed at import without the domain importing it. A literal is the only shape
#: left, and a literal can be wrong in a way that is invisible: ``argon2-cffi``
#: raises rather than returning ``False`` for an encoding it cannot parse, and
#: ``Argon2PasswordHasher`` propagates that deliberately, so a decoy that merely
#: *looks* like a hash would answer 500 for every unknown identifier instead of
#: 401. Therefore the value below is a real argon2id hash and not a plausible
#: string, and ``tests/infrastructure/security/test_argon2_password_hasher.py``
#: asserts that both adapters accept it, that its digest decodes to argon2's
#: 32-byte output length, and that its parameters are the ones the hasher produces
#: today.
#:
#: That last assertion is what makes a frozen literal safe. The cost parameters
#: travel inside every hash, and a real comparison costs whatever the *stored*
#: hash says - so if ``argon2-cffi``'s defaults ever move, this decoy would answer
#: faster than a genuine comparison and the timing gap would reopen in the opposite
#: direction. The test fails on that day rather than the comment asking somebody to
#: notice, and the fix is to regenerate this value with
#: ``PasswordHasher().hash("a decoy whose preimage is discarded")`` and delete the
#: preimage again.
#:
#: The preimage is not recorded anywhere and is not recoverable from this string,
#: which is the point: it must not be a password anybody could present.
DUMMY_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$iInTawApGJj5+6XzlvVYcw"
    "$q6HxO0y+MXtBf0Lj4eHjprTPe5x34zwaNs0mMDCqKJM"
)


class PasswordHasher(ABC):
    """Turns a password into what gets stored, and checks one against it.

    **A port, in the same sense ``NotificationChannel`` is one.** The domain knows
    that a password must be turned into something irreversible and later checked
    against, and it does not know - or want to know - that the answer is argon2,
    with a particular memory cost and a particular encoding. The algorithm is a
    dependency: it is third-party code with an implementation and a version, and
    the rule this codebase follows everywhere is that the domain imports none.

    Concretely, that is what lets the entire identity domain be written and tested
    with argon2 not installed. The rule is enforced by CI not at all and by review
    every time, which is exactly why it is worth a port rather than an import.

    **``verify`` takes the whole password and the whole encoded hash, rather than
    exposing a ``hash`` the caller could compare itself.** That is not a
    convenience: the salt and the cost parameters live *inside* the encoded string,
    so a caller comparing two hashes directly would be comparing the same password
    against itself, and would fail for every password the moment the salt was
    anything but fixed. The comparison has to be performed by the thing that can
    read the encoding, and that is this.

    Nothing here says what happens when ``encoded`` is not a hash at all - a
    truncated column, a value written by something that was not this adapter. That
    is the adapter's decision and it is documented there; the port deliberately
    does not promise a boolean, because promising one would forbid the adapter
    from being loud about corruption.

    **One value is an exception to the paragraph above, and it is ``DUMMY_HASH``.**
    ``LogIn`` hands it to ``verify`` on the path where no credential was found, so
    every implementation must *accept* it - answer ``False`` rather than raise -
    and that is a real obligation rather than an accident of argon2's parser: an
    adapter that raised on it would turn a refusal into a 500 on the one path
    whose whole purpose is to be indistinguishable from the ordinary one. It is the
    only value a caller passes that the implementation did not itself produce, and
    it is why the two adapters in this codebase are tested against it together.
    """

    @abstractmethod
    def hash(self, password: PlainPassword) -> str:
        """Encode this password for storage.

        The result carries everything needed to verify it later - salt, parameters
        and digest - so a database column holding one is self-describing. Two
        calls with the *same* password must return different strings; that is the
        salt, and it is what stops one cracked password from revealing which other
        accounts share it.
        """
        pass

    @abstractmethod
    def verify(self, password: PlainPassword, encoded: str) -> bool:
        """Whether this password is the one ``encoded`` was made from.

        Returns a boolean for a password that is simply wrong, which is the
        ordinary case and the one the login path acts on.
        """
        pass
