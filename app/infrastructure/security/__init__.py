"""Password hashing, over argon2id.

A package of its own rather than a module beside the repositories, because this is
a different kind of adapter: the repositories translate between Python objects and
SQL, and this translates between a secret and a string. It is also the one place in
the codebase that imports a cryptographic library, which makes it worth being able
to point at.
"""
