import pytest

from app.domain.payments.exception import InvalidTransferIntentError
from app.domain.payments.transferIntent import TransferIntent


def test_transfer_intent_holds_provider_reference():
    intent = TransferIntent(provider_reference="TRF_123456")

    assert intent.provider_reference == "TRF_123456"


@pytest.mark.parametrize(
    "reference",
    [
        "",
        " ",
        "   ",
    ],
)
def test_transfer_intent_rejects_empty_provider_reference(reference):
    with pytest.raises(InvalidTransferIntentError):
        TransferIntent(provider_reference=reference)


def test_transfer_intent_provider_reference_must_be_a_string():
    with pytest.raises(InvalidTransferIntentError):
        TransferIntent(provider_reference=int)