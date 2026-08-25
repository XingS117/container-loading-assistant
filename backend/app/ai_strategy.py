from __future__ import annotations

import json
import logging
import os
import time
from urllib.parse import urlparse
from dataclasses import dataclass
from typing import Any

import httpx

from .models import PackRequest

logger = logging.getLogger("container_loading_assistant.ai")
AI_REQUEST_TIMEOUT_SECONDS = 18.0
AI_STRATEGY_MAX_TOKENS = 160
PROVIDER_DEFAULTS = {
    "deepseek": {"base_url": "https://api.deepseek.com/v1", "model": os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"), "host": "api.deepseek.com"},
    "qwen": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen3-max", "host": "dashscope.aliyuncs.com"},
    "zhipu": {"base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-5.3", "host": "open.bigmodel.cn"},
}


@dataclass(frozen=True)
class LayoutHint:
    sku_order: tuple[str, ...] = ()
    orientations: dict[str, str] | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "sku_order": list(self.sku_order),
            "orientations": self.orientations or {},
        }


@dataclass(frozen=True)
class LayoutHintResult:
    hint: LayoutHint | None
    error: str | None = None


@dataclass(frozen=True)
class CompletionResult:
    payload: dict[str, Any] | None
    error: str | None = None


def resolve_ai_api_key(api_key: str | None) -> str:
    return (api_key or os.getenv("DEEPSEEK_API_KEY") or "").strip()


def _content_json(content: Any) -> dict[str, Any] | None:
    if isinstance(content, dict):
        return content
    if not isinstance(content, str):
        return None
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _parse_hint(payload: dict[str, Any], request: PackRequest) -> LayoutHint | None:
    allowed_ids = {item.id for item in request.cargo_items}
    order = payload.get("sku_order")
    if not isinstance(order, list):
        return None
    clean_order = tuple(
        item_id for item_id in order
        if isinstance(item_id, str) and item_id in allowed_ids
    )
    if not clean_order or len(set(clean_order)) != len(clean_order):
        return None
    raw_orientations = payload.get("orientations", {})
    orientations: dict[str, str] = {}
    if isinstance(raw_orientations, dict):
        for item_id, orientation in raw_orientations.items():
            if not isinstance(item_id, str) or item_id not in allowed_ids:
                continue
            if not isinstance(orientation, str):
                continue
            cargo = next(item for item in request.cargo_items if item.id == item_id)
            if orientation in {value.value for value in cargo.allowed_orientations}:
                orientations[item_id] = orientation
    return LayoutHint(clean_order, orientations)


def _resolve_connection(
    api_key: str | None,
    provider: str | None,
    model: str | None,
    base_url: str | None,
) -> tuple[str, str, str, str] | None:
    key = resolve_ai_api_key(api_key)
    if not key:
        return None
    provider_id = (provider or "deepseek").strip().lower()
    defaults = PROVIDER_DEFAULTS.get(provider_id)
    if defaults is None:
        return None
    candidate_base_url = (base_url or defaults["base_url"]).strip().rstrip("/")
    parsed = urlparse(candidate_base_url)
    if parsed.scheme != "https" or parsed.hostname != defaults["host"]:
        return None
    selected_model = (model or defaults["model"]).strip()
    if not selected_model or len(selected_model) > 120:
        return None
    return key, selected_model, f"{candidate_base_url}/chat/completions", provider_id


def _post_chat_completion(
    api_key: str | None,
    provider: str | None,
    model: str | None,
    base_url: str | None,
    messages: list[dict[str, str]],
    response_format: dict[str, str] | None = None,
) -> CompletionResult:
    connection = _resolve_connection(api_key, provider, model, base_url)
    if connection is None:
        return CompletionResult(None, "invalid_config")
    key, selected_model, url, provider_id = connection
    body: dict[str, Any] = {
        "model": selected_model,
        "temperature": 0.1,
        "max_tokens": AI_STRATEGY_MAX_TOKENS,
        "messages": messages,
    }
    if response_format is not None:
        body["response_format"] = response_format
    started_at = time.monotonic()
    try:
        response = httpx.post(
            url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=body,
            timeout=AI_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            return CompletionResult(None, "invalid_response")
        logger.info(
            "%s AI response received model=%s duration_ms=%d",
            provider_id,
            selected_model,
            (time.monotonic() - started_at) * 1000,
        )
        return CompletionResult(payload)
    except httpx.ReadTimeout as exc:
        logger.warning(
            "%s AI response timed out model=%s after_ms=%d",
            provider_id,
            selected_model,
            (time.monotonic() - started_at) * 1000,
        )
        return CompletionResult(None, "timeout")
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "%s AI response failed model=%s status=%s after_ms=%d",
            provider_id,
            selected_model,
            exc.response.status_code,
            (time.monotonic() - started_at) * 1000,
        )
        return CompletionResult(None, "http_error")
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        logger.warning(
            "%s AI request failed model=%s error=%s after_ms=%d",
            provider_id,
            selected_model,
            type(exc).__name__,
            (time.monotonic() - started_at) * 1000,
        )
        return CompletionResult(None, "request_error")


def load_ai_layout_hint(
    request: PackRequest,
    api_key: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> LayoutHint | None:
    return load_ai_layout_hint_diagnostic(
        request,
        api_key=api_key,
        provider=provider,
        model=model,
        base_url=base_url,
    ).hint


def load_ai_layout_hint_diagnostic(
    request: PackRequest,
    api_key: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> LayoutHintResult:
    prompt = {
        "container": {
            "length_mm": request.container.inner_length_mm,
            "width_mm": request.container.inner_width_mm,
            "height_mm": request.container.inner_height_mm,
            "payload_g": request.container.max_payload_g,
            "door_buffer_mm": request.door_buffer_mm,
        },
        "cargo_items": [{
            "id": item.id,
            "dimensions_mm": [item.length_mm, item.width_mm, item.height_mm],
            "weight_g": item.weight_g,
            "quantity": item.quantity,
            "allowed_orientations": [orientation.value for orientation in item.allowed_orientations],
            "stackable": item.stackable,
            "max_layers": item.max_layers,
            "fragile": item.fragile,
            "unload_order": item.unload_order,
        } for item in request.cargo_items],
        "rules": [
            "只返回排组策略，不返回坐标",
            "同规格才能叠放",
            "底层优先铺满，上层从中间向两边展开",
            "不可叠货物只能放底层",
        ],
    }
    body = {
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是装柜排组策略助手。只输出紧凑 JSON，字段为 sku_order（货物 id 数组）"
                    "和 orientations（货物 id 到允许朝向的映射）。禁止输出坐标、重量结论、Markdown 或解释。"
                ),
            },
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
    }
    try:
        completion = _post_chat_completion(
            api_key, provider, model, base_url, body["messages"], {"type": "json_object"},
        )
        if completion.payload is None:
            return LayoutHintResult(None, completion.error)
        payload = completion.payload
        content = payload.get("choices", [{}])[0].get("message", {}).get("content")
        hint = _parse_hint(_content_json(content) or {}, request)
        return LayoutHintResult(hint, None if hint is not None else "invalid_response")
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        logger.warning("AI layout hint unavailable: %s", type(exc).__name__)
        return LayoutHintResult(None, "invalid_response")


def verify_ai_connection(
    api_key: str | None,
    provider: str | None,
    model: str | None,
    base_url: str | None,
) -> str | None:
    completion = _post_chat_completion(
        api_key,
        provider,
        model,
        base_url,
        [{"role": "user", "content": "Reply with OK."}],
    )
    payload = completion.payload
    if payload is None or not payload.get("choices"):
        return None
    return "连接成功，模型可用于策略建议"
