from abc import ABC, abstractmethod
from uuid import UUID

from app.domain.payments.virtualAccount import VirtualAccount


class VirtualAccountRepository(ABC):
    @abstractmethod
    def save(self, account: VirtualAccount) -> VirtualAccount:
        pass

    @abstractmethod
    def get_by_wallet_id(self, wallet_id: UUID) -> VirtualAccount | None:
        pass

    @abstractmethod
    def get_by_account_number(
        self, account_number: str
    ) -> VirtualAccount | None:
        pass