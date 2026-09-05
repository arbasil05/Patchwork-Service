from fastapi import Header, HTTPException
from config.settings import settings

def verify_client(x_api_key: str | None = Header(None, alias="X-API-Key")):
    """
    Dependency to verify API key against the database or return anonymous client.
    """
    if settings.AUTH_MODE == "none":
        return {"client_id": "anonymous"}
        
    if settings.AUTH_MODE == "api_key":
        if not x_api_key:
            raise HTTPException(status_code=401, detail="X-API-Key header missing")
            
        # TODO: Replace stub validation with persistent client/API-key lookup
        # before enabling AUTH_MODE=api_key in production.
        if not x_api_key.startswith("ptch_"):
            raise HTTPException(status_code=401, detail="Invalid API Key")
            
        return {"client_id": f"stub_{x_api_key}", "quota": 100}
