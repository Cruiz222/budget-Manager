from abc import ABC, abstractmethod

from app.domain.identity.password_credential import PasswordCredential


class PasswordCredentialRepository(ABC):
    """Defines what a credential store must do (the domain does not care how).

    Scoped to a user rather than to an actor, and the distinction is the whole of
    the design: this is read during *login*, when the caller has an address and a
    password and no identity at all. Asking "what is this account's password hash"
    cannot require already being that account - the same shape
    ``UserRepository.find_by_email`` has, for the same reason, and it is the
    reason neither of these is narrowed by an actor the way every other repository
    in this package is.
    """

    @abstractmethod
    def save(self, credential: PasswordCredential) -> PasswordCredential:
        """Insert or replace this user's credential, keyed on ``user_id``."""
        pass

    @abstractmethod
    def find_by_user_id(self, user_id) -> PasswordCredential | None:
        """Return this account's credential, or ``None``.

        **``None`` rather than an exception**, and the difference from
        ``UserRepository.get_by_id`` is the same one ``find_by_email`` draws: "this
        account has no password" is an ordinary answer rather than a broken
        invariant. Today nothing creates such an account - sign-up writes the user
        and the credential together - but the shape exists for the Google sign-in
        that Phase 2c adds, where an account genuinely has no password and the
        login path has to be able to tell that apart from a wrong one. A raised
        error would force the caller to catch an exception to ask a question with
        two ordinary answers.
        """
        pass
