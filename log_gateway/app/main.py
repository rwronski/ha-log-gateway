from functools import lru_cache
from pathlib import Path
from typing import Annotated, Optional

import datetime as dt
import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from pydantic import field_validator
from pydantic_settings import BaseSettings as PydanticBaseSettings
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse
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
    all_addon_configs_dir: str = Field("/all_addon_configs", alias="LOGGW_ALL_ADDON_CONFIGS_DIR")

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


def get_requested_lines(lines: Optional[int], settings: Settings) -> int:
    requested = settings.lines_default if lines is None else int(lines)
    if requested <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="lines must be positive.")
    if requested > settings.lines_max:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"lines must be <= {settings.lines_max}.",
        )
    return requested


app = FastAPI(
    title="Log Gateway",
    version="0.1.17",
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


def _read_text_file(path: Path, *, max_bytes: int) -> tuple[str, bool]:
    data = path.read_bytes()
    if len(data) <= max_bytes:
        return data.decode("utf-8", errors="replace"), False
    return data[:max_bytes].decode("utf-8", errors="replace"), True


def _z2m_config_dirs(settings: Settings) -> list[Path]:
    # Zigbee2MQTT add-on commonly stores its config under /config/zigbee2mqtt on HA OS.
    # Some variants may also use addon config directory; support both.
    return [
        Path(settings.config_dir) / "zigbee2mqtt",
        Path(settings.all_addon_configs_dir) / settings.z2m_slug,
    ]


_Z2M_ALLOWED_FILES: dict[str, str] = {
    "configuration.yaml": "text/yaml; charset=utf-8",
    "configuration.yml": "text/yaml; charset=utf-8",
    "devices.yaml": "text/yaml; charset=utf-8",
    "devices.yml": "text/yaml; charset=utf-8",
    "groups.yaml": "text/yaml; charset=utf-8",
    "groups.yml": "text/yaml; charset=utf-8",
    "coordinator_backup.json": "application/json; charset=utf-8",
    # Zigbee2MQTT uses JSON text format despite ".db" extension.
    "database.db": "application/json; charset=utf-8",
}


@app.get("/files/z2m", response_class=JSONResponse)
def list_z2m_files(
    _: str = Depends(require_bearer_auth),
    settings: Settings = Depends(get_settings),
) -> Response:
    locations = []
    for base in _z2m_config_dirs(settings):
        items = []
        for name, content_type in _Z2M_ALLOWED_FILES.items():
            p = base / name
            try:
                if p.exists() and p.is_file():
                    stat = p.stat()
                    items.append(
                        {
                            "name": name,
                            "size": stat.st_size,
                            "content_type": content_type,
                            "mtime": int(stat.st_mtime),
                        }
                    )
            except OSError:
                continue
        if items:
            locations.append({"base": str(base), "files": items})
    return JSONResponse({"locations": locations})

@app.get("/files/z2m/", response_class=JSONResponse, include_in_schema=False)
def list_z2m_files_slash(
    _: str = Depends(require_bearer_auth),
    settings: Settings = Depends(get_settings),
) -> Response:
    return list_z2m_files(_, settings)


@app.get("/files/z2m/{name}", response_class=PlainTextResponse)
def get_z2m_file(
    name: str,
    _: str = Depends(require_bearer_auth),
    settings: Settings = Depends(get_settings),
    download: bool = Query(False, description="Download as attachment (no truncation header)."),
) -> Response:
    # Avoid route shadowing: /files/z2m/{name} is registered before the static
    # /files/z2m/external_converters route in this file, so handle it here.
    if name == "external_converters":
        return list_z2m_external_converters(_, settings)

    if name not in _Z2M_ALLOWED_FILES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not allowed.")

    found_path: Optional[Path] = None
    for base in _z2m_config_dirs(settings):
        candidate = base / name
        try:
            if candidate.exists() and candidate.is_file():
                found_path = candidate
                break
        except OSError:
            continue
    if found_path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found.")

    headers = {"Content-Type": _Z2M_ALLOWED_FILES[name]}
    headers["X-LogGateway-Path"] = str(found_path)
    content_type = _Z2M_ALLOWED_FILES[name]
    if download:
        return FileResponse(
            path=str(found_path),
            media_type=content_type,
            filename=found_path.name,
            headers={"X-LogGateway-Path": str(found_path)},
        )

    try:
        content, truncated = _read_text_file(found_path, max_bytes=2_000_000)
    except OSError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    if truncated:
        headers["X-LogGateway-Truncated"] = "true"
    return PlainTextResponse(content, headers=headers)


_Z2M_JS_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.js$")


def _z2m_external_converters_dirs(settings: Settings) -> list[Path]:
    return [base / "external_converters" for base in _z2m_config_dirs(settings)]


@app.get("/files/z2m/external_converters", response_class=JSONResponse)
def list_z2m_external_converters(
    _: str = Depends(require_bearer_auth),
    settings: Settings = Depends(get_settings),
) -> Response:
    results = []
    for base in _z2m_external_converters_dirs(settings):
        try:
            if not base.exists() or not base.is_dir():
                continue
            files = []
            for p in sorted(base.glob("*.js")):
                try:
                    if not p.is_file():
                        continue
                    stat = p.stat()
                    files.append(
                        {
                            "name": p.name,
                            "size": stat.st_size,
                            "mtime": int(stat.st_mtime),
                        }
                    )
                except OSError:
                    continue
            results.append({"base": str(base), "files": files})
        except OSError:
            continue
    return JSONResponse({"locations": results})

@app.get("/files/z2m/external_converters/", response_class=JSONResponse, include_in_schema=False)
def list_z2m_external_converters_slash(
    _: str = Depends(require_bearer_auth),
    settings: Settings = Depends(get_settings),
) -> Response:
    return list_z2m_external_converters(_, settings)


@app.get("/files/z2m/external_converters/{name}", response_class=PlainTextResponse)
def get_z2m_external_converter(
    name: str,
    _: str = Depends(require_bearer_auth),
    settings: Settings = Depends(get_settings),
) -> Response:
    if not _Z2M_JS_NAME_RE.match(name):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not allowed.")

    for base in _z2m_external_converters_dirs(settings):
        path = base / name
        try:
            if not path.exists() or not path.is_file():
                continue
            content, truncated = _read_text_file(path, max_bytes=2_000_000)
            headers = {"Content-Type": "application/javascript; charset=utf-8"}
            if truncated:
                headers["X-LogGateway-Truncated"] = "true"
            headers["X-LogGateway-Path"] = str(path)
            return PlainTextResponse(content, headers=headers)
        except OSError:
            continue

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found.")


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
    lines: Optional[int] = Query(None, ge=1, description="Number of lines (must be <= lines_max)"),
) -> Response:
    requested = get_requested_lines(lines, settings)
    content = fetch_logs("/host/logs", settings, lines=requested)
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
    lines: Optional[int] = Query(None, ge=1, description="Number of lines (must be <= lines_max)"),
) -> Response:
    # Merge best-effort from:
    # - Supervisor container logs: /core/logs
    # - File logs: /config/home-assistant.log (+ rotations if needed)
    requested = get_requested_lines(lines, settings)
    entries: list[tuple[Optional[dt.datetime], int, str]] = []
    order = 0

    container_text = fetch_logs("/core/logs", settings, lines=requested)
    for line in container_text.splitlines():
        entries.append((_parse_ts(line), order, line))
        order += 1

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
    tail = merged[-requested:] if len(merged) > requested else merged
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
    lines: Optional[int] = Query(None, ge=1, description="Number of lines (must be <= lines_max)"),
) -> Response:
    requested = get_requested_lines(lines, settings)
    content = fetch_logs("/supervisor/logs", settings, lines=requested)
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
    lines: Optional[int] = Query(None, ge=1, description="Number of lines (must be <= lines_max)"),
    include_debug: bool = Query(False, description="Include debug lines"),
) -> Response:
    path = f"/addons/{settings.z2m_slug}/logs"
    target = get_requested_lines(lines, settings)

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
