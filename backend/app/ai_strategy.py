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
AI_REQUEST_TIMEOUT_SECONDS = 8.0
AI_STRATEGY_MAX_TOKENS = 512
PROVIDER_DEFAULTS = {
    "deepseek": {"base_url": "https://api.deepseek.com/v1", "model": os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"), "host": "api.deepseek.com"},
    "qwen": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen3-max", "host": "dashscope.aliyuncs.com"},
    "zhipu": {"base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-5.3", "host": "open.bigmodel.cn"},
}


@dataclass(frozen=True)
class ProfileHint:
    sku_order: tuple[str, ...] = ()
    orientations: dict[str, str] | None = None
    row_groups: tuple[tuple[str, ...], ...] = ()
    zone_order: tuple[str, ...] = ()
    max_zones: int | None = None


@dataclass(frozen=True)
class LayoutHint:
    sku_order: tuple[str, ...] = ()
    orientations: dict[str, str] | None = None
    row_groups: tuple[tuple[str, ...], ...] = ()
    profiles: dict[str, ProfileHint] | None = None

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "sku_order": list(self.sku_order),
            "orientations": self.orientations or {},
            "row_groups": [list(group) for group in self.row_groups],
        }
        if self.profiles:
            result["profiles"] = {
                profile: {
                    "sku_order": list(profile_hint.sku_order),
                    "orientations": profile_hint.orientations or {},
                    "row_groups": [list(group) for group in profile_hint.row_groups],
                    "zone_order": list(profile_hint.zone_order),
                    **(
                        {"max_zones": profile_hint.max_zones}
                        if profile_hint.max_zones is not None
                        else {}
                    ),
                }
                for profile, profile_hint in self.profiles.items()
            }
        return result


@dataclass(frozen=True)
class LayoutHintResult:
    hint: LayoutHint | None
    error: str | None = None


@dataclass(frozen=True)
class CompletionResult:
    payload: dict[str, Any] | None
    error: str | None = None
    status_code: int | None = None


@dataclass(frozen=True)
class ConnectionDiagnostic:
    message: str | None
    error: str | None = None
    status_code: int | None = None


def resolve_ai_api_key(api_key: str | None) -> str:
    return (api_key or os.getenv("DEEPSEEK_API_KEY") or "").strip()


def _content_candidates(content: Any) -> list[dict[str, Any]]:
    """Extract JSON objects from string, structured, and explanatory content."""
    if isinstance(content, dict):
        return [content]
    if isinstance(content, list):
        candidates: list[dict[str, Any]] = []
        for part in content:
            if isinstance(part, dict):
                for key in ("text", "content", "value", "arguments", "input"):
                    if key in part:
                        candidates.extend(_content_candidates(part[key]))
                if not any(key in part for key in ("text", "content", "value", "arguments", "input")):
                    candidates.extend(_content_candidates(part))
            else:
                candidates.extend(_content_candidates(part))
        return candidates
    if not isinstance(content, str):
        return []
    text = content.strip()
    candidates: list[dict[str, Any]] = []
    decoder = json.JSONDecoder()
    for start in range(len(text)):
        if text[start] != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            candidates.append(value)
            break
    return candidates


def _message_payload(message: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for key in ("content", "parsed", "reasoning_content"):
        candidates.extend(_content_candidates(message.get(key)))
    for tool_call in message.get("tool_calls", []) if isinstance(message.get("tool_calls"), list) else []:
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function")
        if isinstance(function, dict):
            candidates.extend(_content_candidates(function.get("arguments")))
        candidates.extend(_content_candidates(tool_call.get("input")))
    return candidates


def _content_json(content: Any) -> dict[str, Any] | None:
    return next(iter(_content_candidates(content)), None)


def _parse_profile_hint(
    payload: Any,
    request: PackRequest,
) -> ProfileHint | None:
    if not isinstance(payload, dict):
        return None
    allowed_ids = {item.id for item in request.cargo_items}
    raw_order = payload.get("sku_order")
    clean_order_items: list[str] = []
    if isinstance(raw_order, list):
        for item_id in raw_order:
            if (
                isinstance(item_id, str)
                and item_id in allowed_ids
                and item_id not in clean_order_items
            ):
                clean_order_items.append(item_id)
    clean_order = tuple(clean_order_items)
    raw_orientations = payload.get("orientations", {})
    orientations: dict[str, str] = {}
    if isinstance(raw_orientations, dict):
        for item_id, orientation in raw_orientations.items():
            if not isinstance(item_id, str) or item_id not in allowed_ids:
                continue
            cargo = next(item for item in request.cargo_items if item.id == item_id)
            allowed_orientations = {value.value for value in cargo.allowed_orientations}
            candidates = orientation if isinstance(orientation, list) else [orientation]
            for candidate in candidates:
                if isinstance(candidate, str) and candidate in allowed_orientations:
                    orientations[item_id] = candidate
                    break
    raw_row_groups = payload.get("row_groups", [])
    row_groups: list[tuple[str, ...]] = []
    if isinstance(raw_row_groups, list):
        for raw_group in raw_row_groups:
            if not isinstance(raw_group, list) or not 1 <= len(raw_group) <= 2:
                continue
            if (
                not all(isinstance(item_id, str) and item_id in allowed_ids for item_id in raw_group)
                or len(set(raw_group)) != len(raw_group)
            ):
                continue
            group = tuple(raw_group)
            if group not in row_groups:
                row_groups.append(group)
    raw_zone_order = payload.get("zone_order", [])
    zone_order = tuple(
        item_id for item_id in raw_zone_order
        if isinstance(item_id, str) and item_id in allowed_ids
    ) if isinstance(raw_zone_order, list) else ()
    if len(set(zone_order)) != len(zone_order):
        return None
    max_zones = payload.get("max_zones")
    if not isinstance(max_zones, int) or isinstance(max_zones, bool) or max_zones < 1:
        max_zones = None
    return ProfileHint(
        sku_order=clean_order,
        orientations=orientations,
        row_groups=tuple(row_groups),
        zone_order=zone_order,
        max_zones=max_zones,
    )


def _parse_hint(payload: dict[str, Any], request: PackRequest) -> LayoutHint | None:
    # Some providers return the orientation map directly instead of wrapping
    # it in the documented ``orientations`` field.
    allowed_ids = {item.id for item in request.cargo_items}
    direct_ids = [item_id for item_id in payload if item_id in allowed_ids]
    if direct_ids and not any(
        key in payload for key in ("sku_order", "orientations", "row_groups", "profiles")
    ):
        payload = {
            "sku_order": direct_ids,
            "orientations": {item_id: payload[item_id] for item_id in direct_ids},
        }
    common = _parse_profile_hint(payload, request)
    if common is None:
        return None
    raw_profiles = payload.get("profiles", {})
    profiles: dict[str, ProfileHint] = {}
    if isinstance(raw_profiles, dict):
        for profile in ("high_fill", "stable", "easy"):
            profile_hint = _parse_profile_hint(raw_profiles.get(profile), request)
            if profile_hint is not None:
                profiles[profile] = profile_hint
    if not common.sku_order and not profiles:
        return None
    return LayoutHint(
        common.sku_order,
        common.orientations,
        common.row_groups,
        profiles or None,
    )


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
    if provider_id == "deepseek":
        body["thinking"] = {"type": "disabled"}
    started_at = time.monotonic()
    if response_format is not None:
        body["response_format"] = response_format
    attempted_format_fallback = False
    while True:
        try:
            response = httpx.post(
                url,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=dict(body),
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
        except httpx.ReadTimeout:
            logger.warning(
                "%s AI response timed out model=%s after_ms=%d",
                provider_id,
                selected_model,
                (time.monotonic() - started_at) * 1000,
            )
            return CompletionResult(None, "timeout")
        except httpx.HTTPStatusError as exc:
            if (
                response_format is not None
                and not attempted_format_fallback
                and exc.response.status_code == 400
            ):
                attempted_format_fallback = True
                body.pop("response_format", None)
                logger.info(
                    "%s provider rejected response_format; retrying without it model=%s",
                    provider_id,
                    selected_model,
                )
                continue
            logger.warning(
                "%s AI response failed model=%s status=%s after_ms=%d",
                provider_id,
                selected_model,
                exc.response.status_code,
                (time.monotonic() - started_at) * 1000,
            )
            return CompletionResult(None, "http_error", exc.response.status_code)
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
                    "你是装柜排组策略助手。只输出紧凑 JSON，字段为 sku_order（货物 id 数组）、"
                    "orientations（货物 id 到允许朝向的映射）、row_groups（每项最多两个同排货物 id），"
                    "以及 profiles.high_fill、profiles.stable、profiles.easy 三种目标策略；"
                    "easy 可给出 zone_order 和 max_zones。"
                    "禁止输出坐标、重量结论、Markdown 或解释。"
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
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return LayoutHintResult(None, "invalid_response")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            return LayoutHintResult(None, "invalid_response")
        hint = next(
            (
                parsed
                for candidate in _message_payload(message)
                if (parsed := _parse_hint(candidate, request)) is not None
            ),
            None,
        )
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
    return verify_ai_connection_diagnostic(api_key, provider, model, base_url).message


def verify_ai_connection_diagnostic(
    api_key: str | None,
    provider: str | None,
    model: str | None,
    base_url: str | None,
) -> ConnectionDiagnostic:
    completion = _post_chat_completion(
        api_key,
        provider,
        model,
        base_url,
        [{"role": "user", "content": "Reply with OK."}],
    )
    payload = completion.payload
    if payload is None or not payload.get("choices"):
        return ConnectionDiagnostic(
            None,
            completion.error,
            completion.status_code,
        )
    return ConnectionDiagnostic("连接成功，模型可用于策略建议")
