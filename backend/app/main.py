"""ASGI entrypoint.

This module deliberately contains no composition decisions. What gets mounted,
warmed, and started is resolved from the active tenant's capability profile by
:func:`app.composition.factory.create_app`, which is the single documented way
to build a Corvus application.

Keeping this file thin is load-bearing, not cosmetic: routers imported here
would be imported for every tenant regardless of profile, which would defeat
the absence proof that composition exists to provide.
"""

from app.composition import create_app

app = create_app()
