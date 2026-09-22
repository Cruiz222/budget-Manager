from dataclasses import dataclass
import uuid

from app.domain.payments.virtualAccountStatus import VirtualAccountStatus
from app.domain.payments.exception import InvalidVirtualAccountError


@dataclass(frozen=True)
class VirtualAccount:
    wallet_id: uuid.UUID
    status: VirtualAccountStatus

    provider: str
    provider_customer_code: str | None = None

    account_number: str | None = None
    account_name: str | None = None
    bank_name: str | None = None

    def __post_init__(self):
        if self.status is VirtualAccountStatus.ACTIVE:
            required_fields = {
                "provider_customer_code": self.provider_customer_code,
                "account_number": self.account_number,
                "account_name": self.account_name,
                "bank_name": self.bank_name,
    }

            for field_name, value in required_fields.items():
                if not isinstance(value, str) or value.strip() == "":
                    raise InvalidVirtualAccountError(
                         f"an active virtual account requires {field_name}"
                 )


