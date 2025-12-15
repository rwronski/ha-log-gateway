from functools import lru_cache
from pathlib import Path
from typing import Annotated, Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from pydantic import field_validator
from pydantic_settings import BaseSettings as PydanticBaseSettings
from starlette.responses import JSONResponse, PlainTextResponse
import datetime as dt
import re


class Settings(PydanticBaseSettings):
    token: str = Field(..., alias="LOGGW_TOKEN")
    supervisor_token: str = Field(..., alias="SUPERVISOR_TOKEN")
    z2m_slug: str = Field("45df7312_zigbee2mqtt", alias="LOGGW_Z2M_SLUG")
    z2m_fetch_cap: int = Field(20000, alias="LOGGW_Z2M_FETCH_CAP")
    lines_default: int = Field(1000, alias="LOGGW_LINES_DEFAULT")
    lines_max: int = Field(1000, alias="LOGGW_LINES_MAX")
    no_colors: bool = Field(True, alias="LOGGW_NO_COLORS")
    config_dir: str = Field("/config", alias="LOGGW_CONFIG_DIR")

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

    @field_validator("z2m_fetch_cap")
    def _cap_min(cls, v: int) -> int:
        if v < 1000:
            raise ValueError("z2m_fetch_cap must be >= 1000")
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

_TS_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})[ T](?P<time>\d{2}:\d{2}:\d{2})(?:\.(?P<ms>\d{1,6}))?"
)

_Z2M_DEBUG_RE = re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]\s+debug:")


def _parse_ts(line: str) -> Optional[dt.datetime]:
    m = _TS_RE.match(line)
    if not m:
        return None
    ms = m.group("ms") or "0"
    ms = (ms + "000000")[:6]
    try:
        return dt.datetime.fromisoformat(f"{m.group('date')}T{m.group('time')}.{ms}")
    except ValueError:
        return None


def _tail_lines(path: Path, n: int, encoding: str = "utf-8") -> list[str]:
    if n <= 0:
        return []
    with path.open("rb") as f:
        f.seek(0, 2)
        end = f.tell()
        block = 8192
        data = b""
        pos = end
        while pos > 0 and data.count(b"\n") <= n:
            read_size = block if pos >= block else pos
            pos -= read_size
            f.seek(pos)
            data = f.read(read_size) + data
        text = data.decode(encoding, errors="replace")
    lines = text.splitlines()
    return lines[-n:]


def fetch_logs(path: str, settings: Settings, *, lines: Optional[int] = None) -> str:
    params = {"lines": int(lines if lines is not None else settings.lines_default)}
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


def _drop_z2m_debug_lines(text: str) -> str:
    lines = text.splitlines()
    filtered = [line for line in lines if not _Z2M_DEBUG_RE.match(line)]
    return "\n".join(filtered)


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
    "/logs/core",
    response_class=PlainTextResponse,
    responses={
        200: {"content": {"text/plain": {"example": "log lines..."}}},
        502: {"description": "Upstream error"},
    },
)
def get_core_logs(
    _: str = Depends(require_bearer_auth),
    settings: Settings = Depends(get_settings),
) -> Response:
    # Merge best-effort from:
    # - Supervisor container logs: /core/logs
    # - File logs: /config/home-assistant.log (+ rotations if needed)
    entries: list[tuple[Optional[dt.datetime], int, str]] = []
    order = 0

    container_text = fetch_logs("/core/logs", settings)
    for line in container_text.splitlines():
        entries.append((_parse_ts(line), order, line))
        order += 1

    requested = settings.lines_default
    cfg = Path(settings.config_dir)
    remaining = requested
    file_lines: list[str] = []
    for candidate in [cfg / "home-assistant.log", cfg / "home-assistant.log.1", cfg / "home-assistant.log.2"]:
        if remaining <= 0:
            break
        try:
            if candidate.exists():
                chunk = _tail_lines(candidate, remaining)
                if chunk:
                    file_lines = chunk + file_lines
                    remaining = max(0, requested - len(file_lines))
        except OSError:
            continue

    for line in file_lines:
        entries.append((_parse_ts(line), order, line))
        order += 1

    with_ts = [e for e in entries if e[0] is not None]
    no_ts = [e for e in entries if e[0] is None]
    with_ts.sort(key=lambda x: (x[0], x[1]))  # type: ignore[call-arg]
    merged = with_ts + no_ts
    tail = merged[-settings.lines_default :] if len(merged) > settings.lines_default else merged
    return PlainTextResponse("\n".join([x[2] for x in tail]))

@app.get(
    "/logs/supervisor",
    response_class=PlainTextResponse,
    responses={
        200: {"content": {"text/plain": {"example": "log lines..."}}},
        502: {"description": "Upstream error"},
    },
)
def get_supervisor_logs(
    _: str = Depends(require_bearer_auth),
    settings: Settings = Depends(get_settings),
) -> Response:
    content = fetch_logs("/supervisor/logs", settings)
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
    include_debug: bool = Query(False, description="Include debug lines"),
) -> Response:
    path = f"/addons/{settings.z2m_slug}/logs"
    target = settings.lines_default

    if include_debug:
        content = fetch_logs(path, settings, lines=target)
        lines = content.splitlines()
        return PlainTextResponse("\n".join(lines[-target:]) if len(lines) > target else content)

    # Best-effort: return exactly `target` non-debug lines by over-fetching and filtering.
    # Falls back to returning `target` raw lines if there aren't enough non-debug lines.
    fetch_n = max(5000, target * 5)
    cap = max(fetch_n, settings.z2m_fetch_cap)

    warning: Optional[str] = None
    while True:
        content = fetch_logs(path, settings, lines=min(fetch_n, cap))
        raw_lines = content.splitlines()
        filtered_lines = [line for line in raw_lines if not _Z2M_DEBUG_RE.match(line)]

        if len(filtered_lines) >= target:
            return PlainTextResponse("\n".join(filtered_lines[-target:]))

        if fetch_n >= cap:
            if len(raw_lines) >= target:
                warning = "Insufficient non-debug lines; returning mixed lines to satisfy target count."
                return PlainTextResponse(
                    "\n".join(raw_lines[-target:]),
                    headers={"X-LogGateway-Warning": warning},
                )
            warning = "Insufficient lines available to satisfy target count."
            return PlainTextResponse(
                "\n".join(filtered_lines),
                headers={"X-LogGateway-Warning": warning},
            )

        fetch_n = min(cap, fetch_n * 2)
