from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.routers.auth import router as auth_router
from app.routers.devices import router as devices_router
from app.routers.login import router as login_router
from app.routers.me import router as me_router
from app.routers.payments import router as payments_router
from app.routers.plans import router as plans_router
from app.routers.webhook import router as webhook_router

settings = get_settings()

app = FastAPI(title="MAPOLY WiFi Portal API", version="0.1.0")


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    """Every 422 uses the same shape as the rest of the API: {"detail": "<string>"}.

    The frontend shows `detail` to the user, so it must be a string, and it must not
    echo the submitted value back.
    """
    first = exc.errors()[0]
    field = str(first["loc"][-1]) if first.get("loc") else "request"
    return JSONResponse(
        status_code=422,
        content={"detail": f"Invalid value for {field}."},
    )

# Only the portal's own origin may call the API from a browser (Section 8).
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


for router in (
    login_router,
    auth_router,
    me_router,
    devices_router,
    plans_router,
    payments_router,
    webhook_router,
):
    app.include_router(router)
