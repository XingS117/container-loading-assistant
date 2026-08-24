from __future__ import annotations

import json
import logging
import os
from urllib.parse import urlparse
from dataclasses import dataclass
from typing import Any

import httpx

from .models import PackRequest

logger = logging.getLogger("container_loading_assistant.ai")
PROVIDER_DEFAULTS = {
    "deepseek": {"base_url": "https://api.deepseek.com/v1", "model": os.getenv("DEEPSEEK_MODEL", "deepseek-v4"), "host": "api.deepseek.com"},
    "qwen": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen3-max", "host": "dashscope.aliyuncs.com"},
    "zhipu": {"base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4.5", "host": "open.bigmodel.cn"},
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
    key = (api_key or os.getenv("DEEPSEEK_API_KEY") or "").strip()
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
) -> dict[str, Any] | None:
    connection = _resolve_connection(api_key, provider, model, base_url)
    if connection is None:
        return None
    key, selected_model, url, provider_id = connection
    body: dict[str, Any] = {"model": selected_model, "temperature": 0.1, "messages": messages}
    if response_format is not None:
        body["response_format"] = response_format
    try:
        response = httpx.post(
            url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=body,
            timeout=4.0,
        )
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        logger.warning("%s AI connection unavailable: %s", provider_id, type(exc).__name__)
        return None


def load_ai_layout_hint(
    request: PackRequest,
    api_key: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> LayoutHint | None:
    prompt = {
        "container": request.container.model_dump(mode="json"),
        "cargo_items": [item.model_dump(mode="json") for item in request.cargo_items],
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
                    "你是装柜排组策略助手。只输出 JSON，字段为 sku_order（货物 id 数组）"
                    "和 orientations（货物 id 到允许朝向的映射）。不要输出坐标、重量结论或解释。"
                ),
            },
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
    }
    try:
        payload = _post_chat_completion(
            api_key, provider, model, base_url, body["messages"], {"type": "json_object"},
        )
        if payload is None:
            return None
        content = payload.get("choices", [{}])[0].get("message", {}).get("content")
        return _parse_hint(_content_json(content) or {}, request)
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        logger.warning("AI layout hint unavailable: %s", type(exc).__name__)
        return None


def verify_ai_connection(
    api_key: str | None,
    provider: str | None,
    model: str | None,
    base_url: str | None,
) -> str | None:
    payload = _post_chat_completion(
        api_key,
        provider,
        model,
        base_url,
        [{"role": "user", "content": "Reply with OK."}],
    )
    if payload is None or not payload.get("choices"):
        return None
    return "连接成功，模型可用于策略建议"
