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

from .models import AIStrategyStatus, ContainerSpec, PackRequest, PackResponse, PackingSolution
from .ai_strategy import (
    PROVIDER_DEFAULTS,
    ConnectionDiagnostic,
    LayoutHintResult,
    load_ai_layout_hint_diagnostic,
    resolve_ai_api_key,
    verify_ai_connection_diagnostic,
)
from .packing import PackingFailure, pack_order


app = FastAPI(title="装柜方案助手", version="0.1.0")
logger = logging.getLogger("container_loading_assistant")
MAX_REQUEST_BYTES = 1024 * 1024
RATE_LIMIT_PER_MINUTE = 60
# Leave room for the advisory AI request before local layout calculation.
PACK_TIMEOUT_SECONDS = 45
# Keep a small response margin after the optional AI request and process cleanup.
PACK_CALCULATION_BUDGET_SECONDS = 35
# AI only provides optional ordering hints; it must not consume the local
# solver's entire request budget when the provider has a long tail.
AI_STRATEGY_TIMEOUT_SECONDS = 8
request_windows: dict[str, deque[float]] = defaultdict(deque)
pack_slots = threading.BoundedSemaphore(2)


@app.middleware("http")
async def request_guard(request: Request, call_next):
    if request.url.path in {"/api/v1/pack", "/api/v1/ai/test"}:
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
        PACK_CALCULATION_BUDGET_SECONDS,
        cancellable=True,
    )


def ai_strategy_status(
    api_key: str | None,
    provider: str | None,
    model: str | None,
    hint: object | None,
    error: str | None = None,
) -> AIStrategyStatus:
    if not (api_key or "").strip():
        return AIStrategyStatus(
            status="disabled",
            message="未启用 AI 策略，当前使用本地装柜算法",
        )
    provider_id = (provider or "deepseek").strip().lower()
    defaults = PROVIDER_DEFAULTS.get(provider_id, {})
    selected_model = (model or defaults.get("model") or "").strip() or None
    if hint is None:
        reason = {
            "timeout": "AI 服务响应超时",
            "http_error": "AI 服务返回错误",
            "invalid_config": "AI 地址、模型或 API Key 配置无效",
            "invalid_response": "AI 返回内容无法识别",
            "request_error": "AI 网络请求失败",
        }.get(error, "AI 服务暂时不可用")
        return AIStrategyStatus(
            status="fallback",
            provider=provider_id,
            model=selected_model,
            message=f"{reason}，已自动使用本地安全算法",
        )
    sku_order = getattr(hint, "sku_order", ())
    orientations = getattr(hint, "orientations", None) or {}
    serialized_hint = hint.as_dict() if hasattr(hint, "as_dict") else {}
    return AIStrategyStatus(
        status="considered",
        provider=provider_id,
        model=selected_model,
        message="AI 策略建议已获取，正在由本地物理校验决定是否采纳",
        sku_order=list(sku_order),
        orientations=orientations,
        profiles=serialized_hint.get("profiles", {}),
    )


def _applied_ai_row_groups(
    request: PackRequest,
    solution: PackingSolution,
    raw_groups: object | None = None,
) -> list[list[str]]:
    if raw_groups is None:
        hint = request.ai_layout_hint or {}
        raw_groups = hint.get("row_groups")
    if not isinstance(raw_groups, list):
        return []
    placements = solution.placements
    applied: list[list[str]] = []
    for raw_group in raw_groups:
        if not isinstance(raw_group, list) or not 1 <= len(raw_group) <= 2:
            continue
        group = [cargo_id for cargo_id in raw_group if isinstance(cargo_id, str)]
        if len(group) != len(raw_group):
            continue
        if len(group) == 1:
            if any(item.cargo_id == group[0] for item in placements):
                applied.append(group)
            continue
        left_items = [item for item in placements if item.cargo_id == group[0]]
        right_items = [item for item in placements if item.cargo_id == group[1]]
        if any(
            left.z_mm == right.z_mm
            and left.y_mm == right.y_mm
            and left.x_mm <= right.x_mm + right.length_mm + request.item_gap_mm
            and right.x_mm <= left.x_mm + left.length_mm + request.item_gap_mm
            for left in left_items
            for right in right_items
        ):
            applied.append(group)
    return applied


def _ai_hint_applied(request: PackRequest, solutions: list[PackingSolution]) -> tuple[bool, list[list[str]]]:
    hint = request.ai_layout_hint or {}
    profiles = hint.get("profiles") if isinstance(hint, dict) else None
    common_order = hint.get("sku_order")
    common_orientations = hint.get("orientations")
    common_row_groups = hint.get("row_groups")
    applied_groups: list[list[str]] = []

    for solution in solutions:
        profile_hint = profiles.get(solution.profile) if isinstance(profiles, dict) else None
        profile_hint = profile_hint if isinstance(profile_hint, dict) else {}
        raw_order = profile_hint.get("sku_order", common_order)
        if not isinstance(raw_order, list) or not raw_order:
            raw_order = profile_hint.get("zone_order", [])
        raw_orientations = {}
        if isinstance(common_orientations, dict):
            raw_orientations.update(common_orientations)
        if isinstance(profile_hint.get("orientations"), dict):
            raw_orientations.update(profile_hint["orientations"])
        raw_row_groups = profile_hint.get("row_groups", common_row_groups)
        has_order = isinstance(raw_order, list) and bool(raw_order)
        has_orientations = bool(raw_orientations)
        has_row_groups = isinstance(raw_row_groups, list) and bool(raw_row_groups)
        max_zones = profile_hint.get("max_zones")
        has_zone_limit = isinstance(max_zones, int) and not isinstance(max_zones, bool) and max_zones > 0
        if not (has_order or has_orientations or has_row_groups or has_zone_limit):
            continue
        placements = solution.placements
        floor = [
            item
            for item in placements
            if item.z_mm == request.container.clearance_mm
        ]
        first_x: dict[str, int] = {}
        for item in floor:
            first_x[item.cargo_id] = min(first_x.get(item.cargo_id, item.x_mm), item.x_mm)
        order_ok = True
        if has_order:
            ordered_ids = [
                cargo_id
                for cargo_id in raw_order
                if isinstance(cargo_id, str) and cargo_id in first_x
            ]
            order_ok = len(ordered_ids) == len(raw_order) and all(
                first_x[left] <= first_x[right]
                for index, left in enumerate(ordered_ids)
                for right in ordered_ids[index + 1:]
            )
        orientation_ok = True
        if has_orientations:
            for cargo_id, expected in raw_orientations.items():
                if not isinstance(cargo_id, str) or not isinstance(expected, str):
                    continue
                matching = [item for item in placements if item.cargo_id == cargo_id]
                if not matching or any(item.rotation.value != expected for item in matching):
                    orientation_ok = False
        profile_groups = _applied_ai_row_groups(request, solution, raw_row_groups)
        row_groups_ok = not has_row_groups or len(profile_groups) == len(raw_row_groups)
        zones_ok = (
            not has_zone_limit
            or len({(item.cargo_id, item.step) for item in placements}) <= max_zones
        )
        if order_ok and orientation_ok and row_groups_ok and zones_ok:
            applied_groups.extend(profile_groups)
            return True, applied_groups
    return False, []


@app.post("/api/v1/pack", response_model=PackResponse)
async def pack(request: PackRequest, http_request: Request) -> PackResponse | JSONResponse:
    if not pack_slots.acquire(blocking=False):
        return JSONResponse(
            status_code=503,
            content={"error": {"code": "CALCULATION_BUSY", "message": "当前计算任务较多，请稍后重试"}},
        )
    try:
        ai_key = http_request.headers.get("X-AI-API-Key")
        effective_ai_key = resolve_ai_api_key(ai_key)
        provider = http_request.headers.get("X-AI-Provider")
        model = http_request.headers.get("X-AI-Model")
        base_url = http_request.headers.get("X-AI-Base-URL")
        # AI is advisory only; timeout/errors leave the deterministic path unchanged.
        try:
            hint_result: LayoutHintResult = await asyncio.wait_for(
                anyio.to_thread.run_sync(
                    lambda: load_ai_layout_hint_diagnostic(
                        request,
                        api_key=ai_key,
                        provider=provider,
                        model=model,
                        base_url=base_url,
                    ),
                    abandon_on_cancel=True,
                ),
                timeout=AI_STRATEGY_TIMEOUT_SECONDS,
            )
            hint = hint_result.hint
            hint_error = hint_result.error
        except asyncio.TimeoutError:
            logger.warning(
                "AI layout hint exceeded budget after_seconds=%s; using local algorithm",
                AI_STRATEGY_TIMEOUT_SECONDS,
            )
            hint = None
            hint_error = "timeout"
        except Exception as exc:
            logger.warning("AI layout hint failed; using local algorithm: %s", type(exc).__name__)
            hint = None
            hint_error = "request_error"
        strategy_status = ai_strategy_status(effective_ai_key, provider, model, hint, hint_error)
        if hint is not None:
            request = request.model_copy(update={"ai_layout_hint": hint.as_dict()})
        result = await asyncio.wait_for(
            run_pack_calculation(request),
            timeout=PACK_TIMEOUT_SECONDS,
        )
        if hint is not None:
            applied, applied_groups = _ai_hint_applied(request, result.solutions)
            strategy_status = strategy_status.model_copy(
                update={
                    "applied": applied,
                    "message": (
                        "AI 已按三种目标参与候选布局生成；最终布局仍以本地物理校验和评分为准"
                        if applied and strategy_status.profiles
                        else "AI 策略建议已采纳，并已参与候选布局生成；最终布局仍以本地物理校验和评分为准"
                        if applied
                        else "AI 策略建议已获取，但未形成完整的安全引导候选，已使用本地安全算法"
                    ),
                    "row_groups": applied_groups,
                },
            )
        return result.model_copy(update={"ai_strategy": strategy_status})
    except asyncio.TimeoutError:
        return JSONResponse(
            status_code=504,
            content={
                "error": {
                    "code": "CALCULATION_TIMEOUT",
                    "message": f"订单较复杂，{PACK_TIMEOUT_SECONDS} 秒内未完成计算",
                }
            },
        )
    finally:
        pack_slots.release()


@app.post("/api/v1/ai/test")
async def ai_connection_test(http_request: Request) -> JSONResponse:
    api_key = http_request.headers.get("X-AI-API-Key")
    if not api_key or not api_key.strip():
        return JSONResponse(status_code=422, content={"error": {"message": "请先填写 API Key"}})
    diagnostic: ConnectionDiagnostic = await anyio.to_thread.run_sync(
        lambda: verify_ai_connection_diagnostic(
            api_key=api_key,
            provider=http_request.headers.get("X-AI-Provider"),
            model=http_request.headers.get("X-AI-Model"),
            base_url=http_request.headers.get("X-AI-Base-URL"),
        ),
    )
    if diagnostic.message is None:
        messages = {
            400: "模型或请求参数不被支持，请检查模型名称和 API 地址",
            401: "API Key 无效，请检查 Key 是否正确或是否已失效",
            402: "AI 账户余额或额度不足，请充值或检查套餐额度",
            403: "API Key 没有调用权限，请检查账户权限",
            429: "AI 服务请求频率或额度受限，请稍后重试或检查套餐限额",
        }
        message = messages.get(
            diagnostic.status_code,
            {
                "invalid_config": "AI 提供商、模型或 API 地址配置不匹配",
                "timeout": "AI 服务响应超时，请稍后重试",
                "invalid_response": "AI 返回内容无法识别，请检查模型兼容性",
                "request_error": "无法连接 AI 服务，请检查网络和 API 地址",
            }.get(diagnostic.error, "AI 连接失败，请稍后重试"),
        )
        return JSONResponse(status_code=422, content={"error": {"message": message}})
    return JSONResponse(content={"message": diagnostic.message})


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
