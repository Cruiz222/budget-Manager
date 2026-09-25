from uuid import UUID

from app.application.unit_of_work import UnitOfWorkFactory
from app.domain.payments.exception import (
    VirtualAccountNotFoundError,
    VirtualAccountNotReadyError,
)
from app.domain.payments.virtualAccount import VirtualAccount
from app.domain.payments.virtualAccountDetails import VirtualAccountDetails
from app.domain.payments.virtualAccountProvider import VirtualAccountProvider
from app.domain.payments.virtualAccountStatus import VirtualAccountStatus


class ProvisionVirtualAccount:
    """Turn a pending virtual account into a provider-issued bank account.

    No provider call happens while a Unit of Work is open. Customer creation,
    saving its retry checkpoint, and bank-account creation are three separate
    stages so a network wait never holds SQLite's write lock.
    """

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        provider: VirtualAccountProvider,
        *,
        actor: UUID,
    ):
        self._unit_of_work_factory = unit_of_work_factory
        self._provider = provider
        self._actor = actor

    def execute(self, wallet_id: UUID) -> VirtualAccount:
        account, customer = self._prepare(wallet_id)

        if account.status is VirtualAccountStatus.ACTIVE:
            return account

        customer_code = account.provider_customer_code

        if customer_code is None:
            customer_code = self._provider.create_customer(
                email=customer["email"],
                phone=customer["phone"],
                first_name=customer["first_name"],
                last_name=customer["last_name"],
            )
            self._save_customer_code(wallet_id, customer_code)

        details = self._provider.create_virtual_account(
            customer_code=customer_code,
        )

        return self._activate(
            wallet_id,
            customer_code,
            details,
        )

    def _prepare(
        self,
        wallet_id: UUID,
    ) -> tuple[VirtualAccount, dict[str, str]]:
        """Read and validate local state, then close the read transaction."""
        uow = self._unit_of_work_factory.start()

        try:
            # This scoped read proves the wallet belongs to the actor.
            uow.wallets.get_owned(wallet_id, self._actor)

            account = uow.virtual_accounts.get_by_wallet_id(wallet_id)
            if account is None:
                raise VirtualAccountNotFoundError(
                    "this wallet has no virtual account"
                )

            if account.status not in (
                VirtualAccountStatus.PENDING,
                VirtualAccountStatus.ACTIVE,
            ):
                raise VirtualAccountNotReadyError(
                    "this virtual account cannot be provisioned"
                )

            user = uow.users.get_by_id(self._actor)
            profile = uow.profiles.find_for_user(self._actor)

            if user.email is None:
                raise VirtualAccountNotReadyError(
                    "virtual account provisioning requires an email address"
                )

            if user.phone is None:
                raise VirtualAccountNotReadyError(
                    "virtual account provisioning requires a verified phone number"
                )

            if profile is None:
                raise VirtualAccountNotReadyError(
                    "virtual account provisioning requires a profile"
                )

            if profile.legal_first_name is None:
                raise VirtualAccountNotReadyError(
                    "virtual account provisioning requires a legal first name"
                )

            if profile.legal_last_name is None:
                raise VirtualAccountNotReadyError(
                    "virtual account provisioning requires a legal last name"
                )

            customer = {
                "email": user.email,
                "phone": user.phone,
                "first_name": profile.legal_first_name,
                "last_name": profile.legal_last_name,
            }

            return account, customer
        finally:
            # This unit only reads. Rolling it back releases its transaction
            # before either provider call can block on the network.
            uow.rollback()

    def _save_customer_code(
        self,
        wallet_id: UUID,
        customer_code: str,
    ) -> None:
        """Commit the retry checkpoint produced by customer creation."""
        uow = self._unit_of_work_factory.start()

        try:
            account = uow.virtual_accounts.get_by_wallet_id(wallet_id)
            if account is None:
                raise VirtualAccountNotFoundError(
                    "virtual account disappeared during provisioning"
                )

            if account.provider_customer_code is None:
                account = account.record_provider_customer_code(
                    customer_code
                )
            elif account.provider_customer_code != customer_code:
                raise VirtualAccountNotReadyError(
                    "virtual account is linked to another provider customer"
                )

            uow.virtual_accounts.save(account)
        except BaseException:
            uow.rollback()
            raise
        else:
            uow.commit()

    def _activate(
        self,
        wallet_id: UUID,
        customer_code: str,
        details: VirtualAccountDetails,
    ) -> VirtualAccount:
        """Commit the provider-issued bank details as the active account."""
        uow = self._unit_of_work_factory.start()

        try:
            account = uow.virtual_accounts.get_by_wallet_id(wallet_id)
            if account is None:
                raise VirtualAccountNotFoundError(
                    "virtual account disappeared during activation"
                )

            if account.status is VirtualAccountStatus.ACTIVE:
                return account

            if account.provider_customer_code != customer_code:
                raise VirtualAccountNotReadyError(
                    "provider customer changed during provisioning"
                )

            active = account.activate(
                provider_customer_code=customer_code,
                account_number=details.account_number,
                account_name=details.account_name,
                bank_name=details.bank_name,
            )
            uow.virtual_accounts.save(active)
        except BaseException:
            uow.rollback()
            raise
        else:
            uow.commit()
            return active