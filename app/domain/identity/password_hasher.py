from abc import ABC, abstractmethod

from .password import PlainPassword


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
