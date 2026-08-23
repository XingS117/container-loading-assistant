from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path

import anyio.to_process
import anyio.to_thread
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .models import ContainerSpec, PackRequest, PackResponse
from .ai_strategy import load_ai_layout_hint
from .packing import PackingFailure, pack_order


app = FastAPI(title="装柜方案助手", version="0.1.0")
logger = logging.getLogger("container_loading_assistant")
MAX_REQUEST_BYTES = 1024 * 1024
RATE_LIMIT_PER_MINUTE = 60
PACK_TIMEOUT_SECONDS = 15
request_windows: dict[str, deque[float]] = defaultdict(deque)
pack_slots = threading.BoundedSemaphore(2)


@app.middleware("http")
async def request_guard(request: Request, call_next):
    if request.url.path == "/api/v1/pack":
        content_length = request.headers.get("content-length")
        try:
            declared_length = int(content_length) if content_length else 0
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={"error": {"code": "INVALID_CONTENT_LENGTH", "message": "Content-Length 无效"}},
            )
        if declared_length > MAX_REQUEST_BYTES:
            return JSONResponse(
                status_code=413,
                content={"error": {"code": "REQUEST_TOO_LARGE", "message": "请求体不能超过 1 MB"}},
            )
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_REQUEST_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={"error": {"code": "REQUEST_TOO_LARGE", "message": "请求体不能超过 1 MB"}},
                )
        request._body = bytes(body)
        client_key = request.client.host if request.client else "unknown"
        now = time.monotonic()
        window = request_windows[client_key]
        while window and window[0] < now - 60:
            window.popleft()
        if len(window) >= RATE_LIMIT_PER_MINUTE:
            return JSONResponse(
                status_code=429,
                content={"error": {"code": "RATE_LIMITED", "message": "计算请求过于频繁，请稍后重试"}},
            )
        window.append(now)
        if len(request_windows) > 5000:
            oldest_key = next(iter(request_windows))
            if oldest_key != client_key:
                request_windows.pop(oldest_key, None)
    return await call_next(request)


CONTAINER_PRESETS = [
    ContainerSpec(
        id="20gp",
        name="20GP",
        inner_length_mm=5898,
        inner_width_mm=2352,
        inner_height_mm=2393,
        door_width_mm=2340,
        door_height_mm=2280,
        max_payload_g=28_200_000,
    ),
    ContainerSpec(
        id="40gp",
        name="40GP",
        inner_length_mm=12032,
        inner_width_mm=2352,
        inner_height_mm=2393,
        door_width_mm=2340,
        door_height_mm=2280,
        max_payload_g=28_800_000,
    ),
    ContainerSpec(
        id="40hq",
        name="40HQ",
        inner_length_mm=12032,
        inner_width_mm=2352,
        inner_height_mm=2698,
        door_width_mm=2340,
        door_height_mm=2585,
        max_payload_g=28_600_000,
    ),
]


@app.exception_handler(PackingFailure)
async def packing_failure_handler(_: Request, exc: PackingFailure) -> JSONResponse:
    content: dict[str, object] = {"error": {"code": exc.code, "message": exc.message}}
    if exc.hint:
        content["error"]["hint"] = exc.hint  # type: ignore[index]
    return JSONResponse(
        status_code=422,
        content=content,
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        {
            "field": ".".join(str(part) for part in error["loc"] if part != "body"),
            "message": error["msg"],
        }
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "INVALID_REQUEST", "message": "输入参数无效", "details": details}},
    )


@app.exception_handler(Exception)
async def unexpected_error_handler(_: Request, exc: Exception) -> JSONResponse:
    error_id = uuid.uuid4().hex[:10]
    logger.exception("unhandled error id=%s type=%s", error_id, type(exc).__name__)
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL_ERROR", "message": "服务暂时无法完成计算", "error_id": error_id}},
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/container-presets", response_model=list[ContainerSpec])
def container_presets() -> list[ContainerSpec]:
    return CONTAINER_PRESETS


async def run_pack_calculation(request: PackRequest) -> PackResponse:
    return await anyio.to_process.run_sync(
        pack_order,
        request,
        cancellable=True,
    )


@app.post("/api/v1/pack", response_model=PackResponse)
async def pack(request: PackRequest, http_request: Request) -> PackResponse | JSONResponse:
    if not pack_slots.acquire(blocking=False):
        return JSONResponse(
            status_code=503,
            content={"error": {"code": "CALCULATION_BUSY", "message": "当前计算任务较多，请稍后重试"}},
        )
    try:
        ai_key = http_request.headers.get("X-AI-API-Key")
        # AI is advisory only; timeout/errors leave the deterministic path unchanged.
        hint = await anyio.to_thread.run_sync(load_ai_layout_hint, request, ai_key)
        if hint is not None:
            request = request.model_copy(update={"ai_layout_hint": hint.as_dict()})
        return await asyncio.wait_for(
            run_pack_calculation(request),
            timeout=PACK_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        return JSONResponse(
            status_code=504,
            content={"error": {"code": "CALCULATION_TIMEOUT", "message": "订单较复杂，15 秒内未完成计算"}},
        )
    finally:
        pack_slots.release()


DIST_DIR = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def resolve_frontend_path(path: str, dist_dir: Path = DIST_DIR) -> Path:
    root = dist_dir.resolve()
    candidate = (root / path).resolve()
    if candidate.is_relative_to(root) and candidate.is_file():
        return candidate
    return root / "index.html"


if DIST_DIR.exists():
    assets_dir = DIST_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/api/{path:path}", include_in_schema=False)
    def unknown_api(path: str) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "Not Found"})

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str) -> FileResponse:
        return FileResponse(resolve_frontend_path(path))
