from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

from .models import PackRequest

logger = logging.getLogger("container_loading_assistant.ai")
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4")


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


def load_ai_layout_hint(
    request: PackRequest,
    api_key: str | None = None,
) -> LayoutHint | None:
    key = (api_key or os.getenv("DEEPSEEK_API_KEY") or "").strip()
    if not key:
        return None
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
        "model": DEEPSEEK_MODEL,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
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
        response = httpx.post(
            DEEPSEEK_URL,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=4.0,
        )
        response.raise_for_status()
        payload = response.json()
        content = payload.get("choices", [{}])[0].get("message", {}).get("content")
        return _parse_hint(_content_json(content) or {}, request)
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        logger.warning("DeepSeek layout hint unavailable: %s", type(exc).__name__)
        return None
