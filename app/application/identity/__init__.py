"""Use cases about who is asking, before anything else can be asked.

Four of them, and they are the smallest use cases in the codebase - which is the
point. Every other package under ``app.application`` performs a business
operation *for* an actor it was handed; this one is where the actor comes from in
the first place, and after Phase 2a that means it is the package that owns
credentials:

- ``SignUp`` creates an account and the password that proves it.
- ``LogIn`` exchanges those for a session, and is the only thing that mints a
  token.
- ``ResolveActorFromSession`` turns a token back into a person, and is what every
  authenticated surface calls.
- ``LogOut`` ends a session.

Read together they are the whole of the system's trust: a token exists only
because ``LogIn`` made one, ``ResolveActorFromSession`` is the only thing that
accepts one, and nothing else in the codebase reads a credential table at all.

The package sits in the application layer rather than in a presentation because
both presentations need it and neither owns it. The CLI signs up and logs in
against a file it keeps; the API does the same over HTTP; and the rules that make
those two the same person - the email fold in ``User``, the hash in
``hash_session_token``, the expiry comparison in ``Session`` - are each written
down once, in the domain, where neither surface can hold a copy that drifts.
"""
