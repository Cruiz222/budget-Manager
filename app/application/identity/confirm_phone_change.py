from datetime import datetime
from uuid import UUID

from app.application.unit_of_work import UnitOfWorkFactory
from app.domain.identity.exception import DuplicatePhoneError
from app.domain.identity.session import hash_session_token
from app.domain.identity.user import User


class ConfirmPhoneChange:
    """Attach a verified phone number to the authenticated actor."""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        *,
        actor: UUID,
    ):
        self._unit_of_work_factory = unit_of_work_factory
        self._actor = actor

    def execute(self, code: str, now: datetime) -> User:
        uow = self._unit_of_work_factory.start()

        try:
            verification = (
                uow.phone_verifications.claim_by_token_hash(
                    hash_session_token(code),
                    now,
                )
            )

            owner = uow.users.find_by_phone(verification.phone)
            

            if (
                owner is not None
                and owner.user_id != self._actor
            ):
                uow.commit()
                raise DuplicatePhoneError(
                    f"{verification.phone} is already registered"
                )

            user = uow.users.get_by_id(self._actor)
            user.change_phone(verification.phone)
            uow.users.save(user)
            uow.commit()

            return user
        finally:
            uow.rollback()