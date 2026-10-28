import secrets

from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings

_bearer = HTTPBearer(auto_error=False)


async def require_service_token(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> None:
    """
    FastAPI dependency that validates the Bearer token on every request.

    Behaviour:
    - SERVICE_TOKEN not set (empty string) → auth disabled, all requests pass.
      This allows zero-config local development.
    - SERVICE_TOKEN set → token must be present and match exactly.
      Returns 401 on missing/wrong token.
    Uses secrets.compare_digest to prevent timing attacks.
    """
    token = settings.SERVICE_TOKEN.get_secret_value()
    if not token:
        return
    if credentials is None or not secrets.compare_digest(
        credentials.credentials.encode(), token.encode()
    ):
        raise HTTPException(status_code=401, detail="Unauthorized")
