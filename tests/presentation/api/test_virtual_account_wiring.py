from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)
from app.presentation.api.app import create_app

from app.presentation.api import app as api_app
from app.infrastructure.settings import PaystackSettings


def test_create_app_keeps_the_injected_virtual_account_provider(
    tmp_path,
    password_hasher,
):
    injected = object()

    application = create_app(
        unit_of_work_factory=SqliteUnitOfWorkFactory(
            str(tmp_path / "virtual-account-wiring.db")
        ),
        password_hasher=password_hasher,
        virtual_account_provider=injected,
    )

    assert application.state.virtual_account_provider is injected


def test_create_app_builds_the_virtual_account_provider_from_paystack_settings(
    tmp_path,
    password_hasher,
    monkeypatch,
):
    settings = PaystackSettings(secret_key="sk_test_virtual_accounts")
    selected = object()
    received = {}

def choose(received_settings, provider=None):
    received["settings"] = received_settings
    received["provider"] = provider
    return selected

    monkeypatch.setattr(
        api_app,
        "virtual_account_provider_for",
        choose,
    )

    application = api_app.create_app(
        unit_of_work_factory=SqliteUnitOfWorkFactory(
            str(tmp_path / "virtual-account-default-wiring.db")
        ),
        password_hasher=password_hasher,
        paystack_settings=settings,
        payment_provider=object(),
    )

    assert received == {
        "settings": settings,
        "provider": None,
    }
    assert application.state.virtual_account_provider is selected    