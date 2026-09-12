from argon2 import PasswordHasher as _Argon2
from argon2.exceptions import VerifyMismatchError

from app.domain.identity.password import PlainPassword
from app.domain.identity.password_hasher import PasswordHasher


class Argon2PasswordHasher(PasswordHasher):
    """The ``PasswordHasher`` port, answered with argon2id.

    **argon2id and not argon2i or argon2d**, which is what ``argon2-cffi``
    defaults to and is the variant the Password Hashing Competition recommended for
    this exact job: it is the hybrid that resists both the side-channel attacks the
    ``i`` variant is shaped against and the time-memory tradeoffs the ``d`` variant
    is shaped against. Naming it rather than relying on the default would be
    redundant today and is still worth knowing, because a default is a thing that
    can change under you.

    **The parameters are left at the library's defaults**, and the reason is that
    they are already tuned to the right order of magnitude (tens of milliseconds
    and 64 MiB per hash at the time of writing) and are revised upward by the
    library as hardware improves. Picking our own numbers would mean picking them
    for the machine this was written on, and a cost factor chosen on a laptop is
    either too weak for a server or too slow for one. When the cost does need
    raising, the parameter belongs with the deployment rather than the code - and
    it is safe to raise later, because the parameters are written into every
    encoded hash, so old passwords keep verifying against the parameters they were
    made with.

    **Raising them is safe; nothing moves the old rows, and that is a gap rather
    than a detail.** The port has two methods and neither re-hashes, so a password
    set under the old cost stays at the old cost until its owner changes it. The
    migration that closes this - re-hash on the next successful login, which is the
    only moment the plaintext is in hand - is a real feature and is not in Phase 2a.
    Written down because the sentence above is otherwise easy to read as "the
    upgrade is handled", and it is not.

    **A verified password is compared by the library, never here.** The salt and
    the cost parameters are inside the encoded string, so there is no second value
    to compare against - the only correct comparison is the one argon2 performs,
    which re-derives the hash using the parameters it reads back out of ``encoded``.
    The library also performs the comparison in constant time, which matters
    because a byte-by-byte early exit leaks how much of a guess was right.
    """

    def __init__(self):
        self._hasher = _Argon2()

    def hash(self, password: PlainPassword) -> str:
        return self._hasher.hash(password.secret)

    def verify(self, password: PlainPassword, encoded: str) -> bool:
        """Whether this password is the one ``encoded`` was made from.

        **A wrong password returns ``False``; a corrupt hash raises.** The two are
        distinguished deliberately, and it is the opposite of what the naming above
        might suggest. ``VerifyMismatchError`` means the hash was well-formed and
        the password simply did not match - the ordinary case, which the login path
        answers with "those details did not match". Anything else - a truncated
        column, a value some other tool wrote - means the row is not a hash at all,
        and swallowing that as ``False`` would present database corruption as a user
        who cannot remember their password. They would try again, and again, and the
        store would go on looking fine. So it propagates, and the API's catch-all
        reports it as what it is.

        **The catch is deliberately narrow, because corruption is not one class.**
        A value whose header argon2 does not recognise raises ``InvalidHashError``,
        which descends from ``ValueError``; one with a valid header and an
        unreadable body raises ``VerificationError``, which descends from
        ``Argon2Error``. The two share no ancestor, so a single ``except`` cannot
        name both - and catching either here would be wrong anyway, because both
        mean the same unwelcome thing. ``VerifyMismatchError`` is the only one that
        means "the password was wrong", which is why it is the only one caught.
        """
        try:
            self._hasher.verify(encoded, password.secret)
        except VerifyMismatchError:
            return False
        return True
