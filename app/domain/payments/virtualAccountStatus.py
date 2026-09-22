from enum import Enum


class VirtualAccountStatus(Enum):
    PENDING = "pending"
    ACTIVE = "active"
    FAILED = "failed"