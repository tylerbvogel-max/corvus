"""Operations: the surfaces an operator needs to run this deployment.

This context owns what is true of *any* Corvus deployment regardless of which
knowledge capabilities it grants — the system use notification shown before
access, and the read side of the request audit trail.

Both landed in ``app.routers.compliance`` because they cite control families
(AC-8, AU-2/3/6/7), but a citation is not ownership. ``AuditMiddleware`` is
installed unconditionally by the application factory, so every profile writes
audit rows; a profile that could not read them back would be audited and blind.
The banner is worse: it renders on every page load and fails soft, so gating it
behind the compliance capability made a control surface disappear in silence.

Ownership here follows the writer, not the citation.
"""

from app.operations.router import router

__all__ = ["router"]
