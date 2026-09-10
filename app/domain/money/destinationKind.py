from enum import Enum


class DestinationKind(Enum):
    """Which rail a destination is reachable on.

    The kind is a discriminator: it says which ``details`` keys are meaningful,
    and it is what keeps the door open for other rails. Adding one (mobile
    money, a crypto address, another bank scheme) means a new member here plus
    an entry in the required-details table - not a change to the stored shape,
    so rows written for an existing rail stay readable.

    Only the bank rail exists today. Crypto addresses were modelled and then
    dropped: nothing needed them yet, and an unused enum member is a guess about
    the future wearing a design's clothes.
    """

    BANK_ACCOUNT = "bank_account"
