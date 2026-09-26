import httpx

from app.domain.payments.virtualAccountDetails import (
    VirtualAccountDetails,
)
from app.domain.payments.virtualAccountProvider import (
    VirtualAccountProvider,
)
from app.infrastructure.payments.paystack_payment_provider import (
    BASE_URL,
    DEFAULT_TIMEOUT,
)
from app.domain.payments.exception import (
    PaymentProviderUnavailableError,
)


class PaystackVirtualAccountProvider(VirtualAccountProvider):
    def __init__(
        self,
        secret_key: str,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self._secret_key = secret_key
        self._timeout = timeout

    def create_customer(self, *, email: str, phone: str, first_name: str, last_name: str) -> str:
        body = self._request(
            "/customer",
            payload={
            "email": email,
            "phone": phone,
            "first_name": first_name,
            "last_name": last_name,
            },
        )
        return body["data"]["customer_code"]    

    def create_virtual_account(
        self,
        *,
        customer_code: str,
    ) -> VirtualAccountDetails:
        body = self._request(
            "/dedicated_account",
            payload={
                "customer": customer_code,
            },
        )
        data = body["data"]

        return VirtualAccountDetails(
            account_number=data["account_number"],
            account_name=data["account_name"],
            bank_name=data["bank"]["name"],
        )


    def _request(
        self,
        path: str,
        *,
        payload: dict,
    ) -> dict:
        try:
            response = httpx.request(
                "POST",
                f"{BASE_URL}{path}",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self._secret_key}",
                    "Content-Type": "application/json",
                },
                timeout=self._timeout,
            )
        except httpx.HTTPError as failure:
            raise PaymentProviderUnavailableError(
                "could not reach the virtual account provider: "
                f"{type(failure).__name__}"
            ) from failure

        return response.json()
