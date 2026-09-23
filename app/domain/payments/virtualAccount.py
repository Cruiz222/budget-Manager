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
        if not isinstance(self.wallet_id, uuid.UUID):
            raise InvalidVirtualAccountError(
                "virtual account wallet_id must be a UUID"
        )

        if not isinstance(self.status, VirtualAccountStatus):
            raise InvalidVirtualAccountError(
                "virtual account status must be a VirtualAccountStatus"
        )

        if not isinstance(self.provider, str) or self.provider.strip() == "":
            raise InvalidVirtualAccountError(
                "virtual account provider must be a non-empty string"
        )
    
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


        if self.status is VirtualAccountStatus.PENDING:
            bank_details = {
                "account_number": self.account_number,
                "account_name": self.account_name,
                "bank_name": self.bank_name,
    }

            for field_name, value in bank_details.items():
                if value is not None:
                    raise InvalidVirtualAccountError(
                        f"a pending virtual account cannot have {field_name}"
            )    
                    
    
    def activate(
    self,
    *,
    provider_customer_code: str,
    account_number: str,
    account_name: str,
    bank_name: str,
) -> "VirtualAccount":
        if self.status is not VirtualAccountStatus.PENDING:
            raise InvalidVirtualAccountError(
                "only a pending virtual account can be activated"
    )
        return VirtualAccount(
            wallet_id=self.wallet_id,
            status=VirtualAccountStatus.ACTIVE,
            provider=self.provider,
            provider_customer_code=provider_customer_code,
            account_number=account_number,
            account_name=account_name,
            bank_name=bank_name,
    )                