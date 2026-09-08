from dotenv import load_dotenv
load_dotenv()

from routes import tickets, admin, admin_images, admin_packages
from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

# ---------------------------------------------------------------------------
# Admin-specific CORS origins (tightly scoped)
# ---------------------------------------------------------------------------
_ADMIN_ORIGINS = {
    "http://localhost:5173",
    "http://127.0.0.1:5173",
}


class SplitCORSMiddleware(BaseHTTPMiddleware):
    """
    Path-aware CORS middleware:
    - /api/admin/* → allow only known Vite dev origins, credentials=true
    - Everything else (/v1/*) → allow all origins, credentials=false
      (preserves the original behaviour for the ticket-execution API)
    """

    async def dispatch(self, request: Request, call_next):
        origin = request.headers.get("origin", "")
        is_admin_path = request.url.path.startswith("/api/admin")

        # Handle CORS preflight
        if request.method == "OPTIONS":
            headers = {
                "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
                "Access-Control-Max-Age": "600",
            }
            if is_admin_path:
                if origin in _ADMIN_ORIGINS:
                    headers["Access-Control-Allow-Origin"] = origin
                    headers["Access-Control-Allow-Credentials"] = "true"
                    headers["Vary"] = "Origin"
                # If origin not in allowed set, return 204 with no CORS headers
                # → browser will block the preflight naturally
            else:
                headers["Access-Control-Allow-Origin"] = "*"
            return Response(status_code=204, headers=headers)

        response = await call_next(request)

        if is_admin_path:
            if origin in _ADMIN_ORIGINS:
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Credentials"] = "true"
                response.headers["Vary"] = "Origin"
        else:
            response.headers["Access-Control-Allow-Origin"] = "*"

        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add hardening headers to all /api/admin responses."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/admin"):
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
        return response


# ---------------------------------------------------------------------------
# App setup
# Middleware is applied in reverse registration order (last added = outermost).
# ---------------------------------------------------------------------------
app = FastAPI()

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(SplitCORSMiddleware)


@app.get("/health")
def health_check():
    return {"status": "ok"}


app.include_router(tickets.router)
app.include_router(admin.router, prefix="/api")
app.include_router(admin_images.router, prefix="/api")
app.include_router(admin_packages.router, prefix="/api")
