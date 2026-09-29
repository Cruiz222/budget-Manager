from datetime import datetime
import pytest
from uuid import uuid4
from app.domain.identity.exception import (
    DuplicatePhoneError,
    PhoneVerificationAlreadyUsedError,
)

from app.application.identity.confirm_phone_change import (
    ConfirmPhoneChange,
)
from app.domain.identity.phoneVerification import PhoneVerification
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)


NOW = datetime(2026, 3, 2, 12, 0)
TYPED_PHONE = "08012345678"
FOLDED_PHONE = "2348012345678"


def test_it_attaches_the_verified_phone_to_the_actor(
    tmp_path,
    build_user,
):
    factory = SqliteUnitOfWorkFactory(
        str(tmp_path / "confirm-phone-change.db")
    )
    user = build_user(
        email="johnny@example.com",
        phone=None,
    )
    verification, code = PhoneVerification.issue(
        phone=TYPED_PHONE,
        now=NOW,
    )

    seed = factory.start()
    try:
        seed.users.save(user)
        seed.phone_verifications.save(verification)
        seed.commit()
    finally:
        seed.rollback()

    service = ConfirmPhoneChange(
        factory,
        actor=user.user_id,
    )

    changed = service.execute(code, NOW)

    assert changed.user_id == user.user_id
    assert changed.phone == FOLDED_PHONE

    stored = factory.start()
    try:
        assert stored.users.get_by_id(user.user_id).phone == FOLDED_PHONE
    finally:
        stored.rollback()


def test_it_refuses_a_phone_owned_by_another_account(
    tmp_path,
    build_user,
):
    factory = SqliteUnitOfWorkFactory(
        str(tmp_path / "duplicate-phone-change.db")
    )
    actor = build_user(
        email="johnny@example.com",
        phone=None,
    )
    owner = build_user(
        user_id=uuid4(),
        email="owner@example.com",
        phone=TYPED_PHONE,
    )
    verification, code = PhoneVerification.issue(
        phone=TYPED_PHONE,
        now=NOW,
    )

    seed = factory.start()
    try:
        seed.users.save(actor)
        seed.users.save(owner)
        seed.phone_verifications.save(verification)
        seed.commit()
    finally:
        seed.rollback()

    service = ConfirmPhoneChange(
        factory,
        actor=actor.user_id,
    )

    with pytest.raises(DuplicatePhoneError):
        service.execute(code, NOW)

    stored = factory.start()
    try:
        assert stored.users.get_by_id(actor.user_id).phone is None
        assert (
            stored.users.get_by_id(owner.user_id).phone
            == FOLDED_PHONE
        )
    finally:
        stored.rollback()

    # Even though the phone could not be attached, the presented code
    # was consumed and cannot be used again.
    with pytest.raises(PhoneVerificationAlreadyUsedError):
        service.execute(code, NOW)        