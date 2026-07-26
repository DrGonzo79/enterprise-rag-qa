"""API-key authentication with two scopes (SPEC-006 KD-7).

Read and admin are separated because `/ingest` **spends money** and mutates the
corpus while `/query` reads — one key would mean anyone who can ask a question
can also trigger spend.
"""

import hmac
from collections.abc import Awaitable, Callable
from enum import StrEnum

from fastapi import Request, Security
from fastapi.security import APIKeyHeader

from rag_qa.api.errors import Forbidden, Unauthenticated

API_KEY_HEADER = "X-API-Key"

# auto_error=False: this scheme is declared for OpenAPI (so /docs shows the lock
# and AC-11 can assert it), but the 401/403 decision is ours — FastAPI's default
# 403-for-missing-key would collapse the distinction KD-7 depends on.
api_key_scheme = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)


class Scope(StrEnum):
    READ = "read"
    ADMIN = "admin"


def verify(
    presented: str | None,
    *,
    required: Scope,
    read_key: str | None,
    admin_key: str | None,
    allow_anonymous: bool,
) -> None:
    if allow_anonymous:
        return
    if not presented:
        raise Unauthenticated(f"missing {API_KEY_HEADER} header")

    is_admin = bool(admin_key) and hmac.compare_digest(presented, admin_key or "")
    is_read = bool(read_key) and hmac.compare_digest(presented, read_key or "")

    if not (is_admin or is_read):
        raise Unauthenticated("unknown API key")
    # Admin is a superset of read: an operator holding the admin key can query.
    if required is Scope.ADMIN and not is_admin:
        raise Forbidden("this endpoint requires the admin API key")


def require(scope: Scope) -> Callable[..., Awaitable[None]]:
    async def dependency(
        request: Request, presented: str | None = Security(api_key_scheme)
    ) -> None:
        settings = request.app.state.settings
        verify(
            presented,
            required=scope,
            read_key=settings.api_key,
            admin_key=settings.admin_api_key,
            allow_anonymous=settings.allow_anonymous,
        )

    return dependency
