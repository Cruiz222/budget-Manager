"""Errors raised by the notifications side of the domain.

Deriving from ``MoneyError`` rather than from ``Exception`` continues the rule
``app.domain.planning.exception`` states: **a new exception belongs under the
existing root, or every catch site in the codebase has to be revisited.** The
CLI catches ``MoneyError`` once, at the top of ``main``, and turns it into
``error: ...`` with exit code 1. A notifications root outside that tree would
mean every message-construction bug escaped as a traceback instead.

The name ``MoneyError`` no longer describes what it holds, and this is the
package that makes that plain - there is no money anywhere in a warning about a
payout. It is left alone deliberately: renaming the root touches every domain
package and README decision 6, which is a change worth making on a day when
nothing else is in flight rather than in the middle of a feature.
"""

from app.domain.money.exception import MoneyError


class NotificationError(MoneyError):
    """Base for every rejection the notifications domain makes."""


# --- OutboundMessage --------------------------------------------------------


class InvalidOutboundMessagePlanIDError(NotificationError):
    pass


class InvalidOutboundMessageDueAtError(NotificationError):
    pass


class InvalidOutboundMessageRecipientError(NotificationError):
    pass


class InvalidOutboundMessageSubjectError(NotificationError):
    pass


class InvalidOutboundMessageBodyError(NotificationError):
    pass


class InvalidOutboundMessageCreatedAtError(NotificationError):
    pass


class InvalidOutboundMessageStatusError(NotificationError):
    pass


class InvalidOutboundMessageAttemptsError(NotificationError):
    pass


class InvalidOutboundMessageLastErrorError(NotificationError):
    pass


class InvalidOutboundMessageSettledAtError(NotificationError):
    pass


class MessageAlreadySettledError(NotificationError):
    pass


# --- Notification -----------------------------------------------------------
# The receipt half, with its own classes rather than reusing the ones above.
# The two aggregates reject different things - an ``OutboundMessage`` without a
# plan id is a bug, and so is a ``Notification`` *with* one - so a shared error
# would have to describe neither. ``MessageAlreadySettledError`` is the one
# exception both raise, because "this message is already finished" means exactly
# the same thing on either side.


class InvalidNotificationEventKeyError(NotificationError):
    pass


class InvalidNotificationKindError(NotificationError):
    pass


class InvalidNotificationSubjectIDError(NotificationError):
    pass


class InvalidNotificationRecipientError(NotificationError):
    pass


class InvalidNotificationSubjectError(NotificationError):
    pass


class InvalidNotificationBodyError(NotificationError):
    pass


class InvalidNotificationCreatedAtError(NotificationError):
    pass


class InvalidNotificationStatusError(NotificationError):
    pass


class InvalidNotificationAttemptsError(NotificationError):
    pass


class InvalidNotificationLastErrorError(NotificationError):
    pass


class InvalidNotificationSettledAtError(NotificationError):
    pass


# --- SmsChannel -------------------------------------------------------------
# One class rather than the fifteen above, and the difference is what the two
# groups are: everything in the sections above is an aggregate refusing a value
# at construction, and this is an adapter reporting that it could not do its job.
# The name is here rather than beside the adapter for the reason
# ``PaymentProviderError`` sits in ``app.domain.payments.exception`` rather than
# in the Paystack module: a caller must not have to catch ``httpx`` to find out
# that the provider said no.


class SmsProviderError(NotificationError):
    """A text was not sent, and this is why.

    Raised by ``SmsChannel`` implementations - never by a use case - exactly as
    the port's docstring states, and the argument ``PaymentProviderError``'s
    docstring makes one domain over applies without amendment: the exception *is*
    the error report, because a channel has no partially-successful send to model.

    **It carries one refusal of a different kind as well, deliberately.** A
    recipient that is not in the shape an SMS API accepts - unfolded, or still
    carrying a national trunk prefix - is refused by the channel before anything
    is sent, and raises this same class. That makes the name a slight lie for that
    case: the provider did not refuse anything, because it was never asked. It is
    accepted because the alternative is worse. The port's contract is that a
    failed send raises, and a caller has one thing to catch; a second class for
    "our own channel would not send this" would turn that into two things to
    catch, and the callers in this slice have no use for the distinction - both
    cases mean the text did not go and the request cannot be answered. The
    sentence says which happened, which is where a human reads it.

    **It is not graded anywhere, so it answers 400** - the fall-through in
    ``app.presentation.api.errors._grade``, and the same grade
    ``PaymentProviderError`` gets. For a refusal that is right, since what the
    provider would not serve is usually something about what this system asked
    for. Whether an *outage* at the far end deserves a 503 instead is a real
    question and a decision for the flow that first has a route to answer it on -
    there is none yet in the step this class arrives in, and ``UNAVAILABLE`` is
    where that answer goes if it turns out to be the one, rather than being
    guessed at here.
    """

