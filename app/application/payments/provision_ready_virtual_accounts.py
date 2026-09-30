from uuid import UUID

from app.application.payments.provision_virtual_account import (
    ProvisionVirtualAccount,
)
from app.application.unit_of_work import UnitOfWorkFactory
from app.domain.money.walletStatus import WalletStatus
from app.domain.payments.exception import (
    VirtualAccountNotReadyError,
)
from app.domain.payments.virtualAccount import VirtualAccount
from app.domain.payments.virtualAccountProvider import (
    VirtualAccountProvider,
)


class ProvisionReadyVirtualAccounts:
    """Provision every eligible wallet belonging to one actor."""

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

    def execute(self) -> list[VirtualAccount]:
        wallet_ids = self._wallet_ids()
        provisioner = ProvisionVirtualAccount(
            self._unit_of_work_factory,
            self._provider,
            actor=self._actor,
        )
        accounts = []

        for wallet_id in wallet_ids:
            try:
                accounts.append(provisioner.execute(wallet_id))
            except VirtualAccountNotReadyError:
                # The actor has not supplied every required identity field yet.
                # A later phone or profile update can safely try again.
                continue

        return accounts

    def _wallet_ids(self) -> list[UUID]:
        uow = self._unit_of_work_factory.start()

        try:
            return [
                wallet.wallet_id
                for wallet in uow.wallets.list_for_owner(self._actor)
                if wallet.status is not WalletStatus.CLOSED
            ]
        finally:
            uow.rollback()