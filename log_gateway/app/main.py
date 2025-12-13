from functools import lru_cache
from typing import Annotated, Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from pydantic import BaseModel, Field
from pydantic import field_validator
from pydantic_settings import BaseSettings as PydanticBaseSettings
from starlette.responses import JSONResponse, PlainTextResponse


class Settings(PydanticBaseSettings):
    token: str = Field(..., alias="LOGGW_TOKEN")
    supervisor_token: str = Field(..., alias="SUPERVISOR_TOKEN")
    z2m_slug: str = Field("45df7312_zigbee2mqtt", alias="LOGGW_Z2M_SLUG")
    lines_default: int = Field(1000, alias="LOGGW_LINES_DEFAULT")
    lines_max: int = Field(1000, alias="LOGGW_LINES_MAX")
    no_colors: bool = Field(True, alias="LOGGW_NO_COLORS")

    model_config = {
        "env_file": None,
        "extra": "ignore",
    }

    @field_validator("z2m_slug")
    def _slug_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("z2m_slug cannot be empty")
        return v

    @field_validator("lines_default", "lines_max")
    def _positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("must be positive")
        return v

    @field_validator("lines_max")
    def _max_ge_default(cls, v: int, info) -> int:
        default = info.data.get("lines_default", 0)
        if v < default:
            raise ValueError("lines_max must be >= lines_default")
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[arg-type]


@lru_cache(maxsize=1)
def _get_http_client(auth_header: str) -> httpx.Client:
    return httpx.Client(
        base_url="http://supervisor",
        headers={"Authorization": auth_header},
        timeout=httpx.Timeout(connect=2.0, read=6.0, write=6.0, pool=6.0),
        follow_redirects=False,
    )


def get_http_client(settings: Settings) -> httpx.Client:
    return _get_http_client(f"Bearer {settings.supervisor_token}")


class HealthResponse(BaseModel):
    status: str


class LogSnapshotResponse(BaseModel):
    source: str
    lines: int
    note: Optional[str] = None


def require_bearer_auth(
    authorization: Annotated[Optional[str], Header()] = None,
    settings: Settings = Depends(get_settings),
) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header.",
        )
    provided = authorization[7:].strip()
    if not provided or provided != settings.token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token.",
        )
    return provided


app = FastAPI(
    title="Log Gateway",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
)


@app.get("/healthz", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


def fetch_logs(path: str, settings: Settings) -> str:
    params = {"lines": settings.lines_default}
    if settings.no_colors:
        params["no_colors"] = 1

    client = get_http_client(settings)
    try:
        response = client.get(path, params=params)
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Supervisor request failed: {exc.__class__.__name__}",
        ) from exc

    if response.status_code != status.HTTP_200_OK:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Supervisor returned {response.status_code}: {response.text[:200]}",
        )

    return response.text


@app.get(
    "/logs/system",
    response_class=PlainTextResponse,
    responses={
        200: {"content": {"text/plain": {"example": "log lines..."}}},
        502: {"description": "Upstream error"},
    },
)
def get_system_logs(
    _: str = Depends(require_bearer_auth),
    settings: Settings = Depends(get_settings),
) -> Response:
    content = fetch_logs("/host/logs", settings)
    return PlainTextResponse(content)


@app.get(
    "/logs/z2m",
    response_class=PlainTextResponse,
    responses={
        200: {"content": {"text/plain": {"example": "log lines..."}}},
        502: {"description": "Upstream error"},
    },
)
def get_z2m_logs(
    _: str = Depends(require_bearer_auth),
    settings: Settings = Depends(get_settings),
) -> Response:
    path = f"/addons/{settings.z2m_slug}/logs"
    content = fetch_logs(path, settings)
    return PlainTextResponse(content)
