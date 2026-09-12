"""One module per resource, each exporting a ``router``.

The split is by *what the paths are about* rather than by use case, so a reader
looking for an endpoint can guess the file and be right. ``funds`` is separate
from ``wallets`` for the same reason the paths are nested: a pot is reachable
only through the wallet that holds it, and the file layout says so. ``sessions``
is separate from ``users`` for a related reason: an account and the proof that a
request *is* that account have different lifetimes and different owners, and the
two endpoints that hand out and discard a token are the only pair in the API that
can be reached without already having one.

One thing the layout deliberately does *not* do is give any router a prefix.
Every path is written in full at the route that serves it, which is slightly
repetitive and buys two things: a reader can see the URL without concatenating a
prefix in their head, and the boundary this phase draws - which operations exist
and which do not - is legible by reading the files rather than by inferring it
from what is missing. ``tests/presentation/api/test_boundary.py`` asserts the
missing half.
"""
