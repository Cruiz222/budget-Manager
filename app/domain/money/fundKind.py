from enum import Enum


class FundKind(Enum):
    """What a locked fund is *for*, and so how sealed it is.

    This is the only axis on which the two kinds differ, and it is deliberately
    a single question: *may this fund pay a scheduled obligation before it
    matures?*

    - ``PERSONAL`` - money set aside for the person who owns it. Nothing leaves
      before the maturity date by any route: it cannot be released back to the
      available balance, and it cannot be paid out to an external account. It is
      the stronger promise, and the one locking exists for.
    - ``BUSINESS`` - a float earmarked for obligations that come due. It may pay
      a scheduled external account before it matures, because that is the whole
      reason it exists; but it still cannot be pulled back into general spending
      early, because otherwise it would not be earmarked at all.

    Both kinds accept deposits at any time, and both refuse an early *release*.
    The maturity date gates the release for both; only the payout differs.

    Values are lowercase names, matching ``PlanSource`` and ``Cadence``.
    """

    PERSONAL = "personal"
    BUSINESS = "business"
