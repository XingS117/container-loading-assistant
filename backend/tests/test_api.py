import asyncio

from fastapi.testclient import TestClient

from app import main
from app.ai_strategy import AI_REQUEST_TIMEOUT_SECONDS
from app.main import app, resolve_frontend_path


client = TestClient(app)


def test_pack_timeout_covers_ai_request_timeout():
    assert main.PACK_TIMEOUT_SECONDS > AI_REQUEST_TIMEOUT_SECONDS


def test_health_and_container_presets():
    assert client.get("/health").json() == {"status": "ok"}

    response = client.get("/api/v1/container-presets")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == ["20gp", "40gp", "40hq"]


def test_pack_endpoint_returns_four_core_solutions(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    payload = {
        "container": {
            "id": "small",
            "name": "小型测试柜",
            "inner_length_mm": 2000,
            "inner_width_mm": 1000,
            "inner_height_mm": 1000,
            "door_width_mm": 1000,
            "door_height_mm": 1000,
            "max_payload_g": 1000000,
            "clearance_mm": 0,
        },
        "cargo_items": [
            {
                "id": "a",
                "sku": "A",
                "name": "标准箱",
                "kind": "carton",
                "length_mm": 1000,
                "width_mm": 1000,
                "height_mm": 1000,
                "weight_g": 100000,
                "quantity": 2,
                "allowed_orientations": ["LWH"],
                "stackable": False,
                "max_layers": 1,
                "max_top_load_g": 0,
                "fragile": False,
                "must_load": False,
            }
        ],
        "item_gap_mm": 0,
    }

    response = client.post("/api/v1/pack", json=payload)

    assert response.status_code == 200
    assert len(response.json()["solutions"]) == 3
    assert response.json()["ai_strategy"] == {
        "status": "disabled",
        "applied": False,
        "provider": None,
        "model": None,
        "message": "未启用 AI 策略，当前使用本地装柜算法",
        "sku_order": [],
        "orientations": {},
        "row_groups": [],
    }


def test_pack_endpoint_passes_optional_ai_hint_without_changing_response_contract(monkeypatch):
    from app.ai_strategy import LayoutHint
    from app.packing import pack_order

    payload = {
        "container": {
            "id": "small",
            "name": "小型测试柜",
            "inner_length_mm": 2000,
            "inner_width_mm": 1000,
            "inner_height_mm": 1000,
            "door_width_mm": 1000,
            "door_height_mm": 1000,
            "max_payload_g": 1000000,
            "clearance_mm": 0,
        },
        "cargo_items": [{
            "id": "a",
            "sku": "A",
            "name": "标准箱",
            "kind": "carton",
            "length_mm": 1000,
            "width_mm": 1000,
            "height_mm": 1000,
            "weight_g": 100000,
            "quantity": 1,
            "allowed_orientations": ["LWH"],
            "stackable": False,
            "max_layers": 1,
            "max_top_load_g": 0,
        }],
    }
    captured = {}

    monkeypatch.setattr(
        main,
        "load_ai_layout_hint_diagnostic",
        lambda _request, **_kwargs: main.LayoutHintResult(LayoutHint(("a",), {}, (("a",),))),
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "server-key")

    async def calculation(request):
        captured["hint"] = request.ai_layout_hint
        return pack_order(request)

    monkeypatch.setattr(main, "run_pack_calculation", calculation)

    response = client.post("/api/v1/pack", json=payload)

    assert response.status_code == 200
    assert captured["hint"] == {"sku_order": ["a"], "orientations": {}, "row_groups": [["a"]]}
    assert len(response.json()["solutions"]) == 3
    assert response.json()["ai_strategy"] == {
        "status": "considered",
        "applied": True,
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "message": "AI 策略建议已采纳，并已参与候选布局生成；最终布局仍以本地物理校验和评分为准",
        "sku_order": ["a"],
        "orientations": {},
        "row_groups": [["a"]],
    }


def test_pack_endpoint_reports_profile_specific_ai_strategy(monkeypatch):
    from app.ai_strategy import LayoutHint, ProfileHint
    from app.packing import pack_order

    payload = {
        "container": {
            "id": "small",
            "name": "小型测试柜",
            "inner_length_mm": 2000,
            "inner_width_mm": 1000,
            "inner_height_mm": 1000,
            "door_width_mm": 1000,
            "door_height_mm": 1000,
            "max_payload_g": 1000000,
            "clearance_mm": 0,
        },
        "cargo_items": [{
            "id": "a",
            "sku": "A",
            "name": "标准箱",
            "kind": "carton",
            "length_mm": 1000,
            "width_mm": 1000,
            "height_mm": 1000,
            "weight_g": 100000,
            "quantity": 1,
            "allowed_orientations": ["LWH"],
            "stackable": False,
            "max_layers": 1,
            "max_top_load_g": 0,
        }],
    }
    hint = LayoutHint(
        profiles={
            "easy": ProfileHint(zone_order=("a",), max_zones=1),
        },
    )
    monkeypatch.setattr(main, "load_ai_layout_hint_diagnostic", lambda _request, **_kwargs: main.LayoutHintResult(hint))
    monkeypatch.setattr(main, "run_pack_calculation", lambda request: asyncio.sleep(0, result=pack_order(request)))

    response = client.post(
        "/api/v1/pack",
        json=payload,
        headers={"X-AI-API-Key": "sk-browser-test"},
    )

    assert response.status_code == 200
    assert response.json()["ai_strategy"]["applied"] is True
    assert response.json()["ai_strategy"]["profiles"]["easy"]["max_zones"] == 1


def test_pack_endpoint_reports_ai_fallback_when_the_hint_is_unavailable(monkeypatch):
    from app.packing import pack_order

    payload = {
        "container": {
            "id": "small", "name": "小型测试柜", "inner_length_mm": 2000,
            "inner_width_mm": 1000, "inner_height_mm": 1000,
            "door_width_mm": 1000, "door_height_mm": 1000,
            "max_payload_g": 1000000, "clearance_mm": 0,
        },
        "cargo_items": [{
            "id": "a", "sku": "A", "name": "标准箱", "kind": "carton",
            "length_mm": 1000, "width_mm": 1000, "height_mm": 1000,
            "weight_g": 100000, "quantity": 1, "allowed_orientations": ["LWH"],
            "stackable": False, "max_layers": 1, "max_top_load_g": 0,
        }],
    }
    monkeypatch.setattr(main, "load_ai_layout_hint_diagnostic", lambda _request, **_kwargs: main.LayoutHintResult(None, "request_error"))
    monkeypatch.setattr(main, "run_pack_calculation", lambda request: asyncio.sleep(0, result=pack_order(request)))

    response = client.post("/api/v1/pack", json=payload, headers={"X-AI-API-Key": "sk-browser-test"})

    assert response.status_code == 200
    assert response.json()["ai_strategy"]["status"] == "fallback"
    assert response.json()["ai_strategy"]["message"] == "AI 网络请求失败，已自动使用本地安全算法"


def test_pack_endpoint_falls_back_to_local_algorithm_when_ai_raises(monkeypatch):
    from app.packing import pack_order

    payload = {
        "container": {
            "id": "small", "name": "小型测试柜", "inner_length_mm": 2000,
            "inner_width_mm": 1000, "inner_height_mm": 1000,
            "door_width_mm": 1000, "door_height_mm": 1000,
            "max_payload_g": 1000000, "clearance_mm": 0,
        },
        "cargo_items": [{
            "id": "a", "sku": "A", "name": "标准箱", "kind": "carton",
            "length_mm": 1000, "width_mm": 1000, "height_mm": 1000,
            "weight_g": 100000, "quantity": 1, "allowed_orientations": ["LWH"],
            "stackable": False, "max_layers": 1, "max_top_load_g": 0,
        }],
    }
    monkeypatch.setattr(main, "load_ai_layout_hint_diagnostic", lambda _request, **_kwargs: (_ for _ in ()).throw(RuntimeError("AI unavailable")))
    monkeypatch.setattr(main, "run_pack_calculation", lambda request: asyncio.sleep(0, result=pack_order(request)))

    response = client.post("/api/v1/pack", json=payload, headers={"X-AI-API-Key": "sk-browser-test"})

    assert response.status_code == 200
    assert len(response.json()["solutions"]) == 3
    assert response.json()["ai_strategy"]["status"] == "fallback"


def test_pack_endpoint_passes_model_configuration_headers_to_ai_strategy(monkeypatch):
    from app.packing import pack_order

    payload = {
        "container": {
            "id": "small",
            "name": "小型测试柜",
            "inner_length_mm": 2000,
            "inner_width_mm": 1000,
            "inner_height_mm": 1000,
            "door_width_mm": 1000,
            "door_height_mm": 1000,
            "max_payload_g": 1000000,
            "clearance_mm": 0,
        },
        "cargo_items": [{
            "id": "a", "sku": "A", "name": "标准箱", "kind": "carton",
            "length_mm": 1000, "width_mm": 1000, "height_mm": 1000,
            "weight_g": 100000, "quantity": 1, "allowed_orientations": ["LWH"],
            "stackable": False, "max_layers": 1, "max_top_load_g": 0,
        }],
    }
    captured = {}

    def capture_hint(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return None

    monkeypatch.setattr(main, "load_ai_layout_hint_diagnostic", lambda _request, **kwargs: main.LayoutHintResult(capture_hint(_request, **kwargs)))
    monkeypatch.setattr(main, "run_pack_calculation", lambda request: asyncio.sleep(0, result=pack_order(request)))

    response = client.post(
        "/api/v1/pack",
        json=payload,
        headers={
            "X-AI-API-Key": "sk-browser-test",
            "X-AI-Provider": "qwen",
            "X-AI-Model": "qwen3-max",
            "X-AI-Base-URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        },
    )

    assert response.status_code == 200
    assert captured["kwargs"] == {
        "api_key": "sk-browser-test",
        "provider": "qwen",
        "model": "qwen3-max",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    }


def test_ai_connection_endpoint_passes_config_headers(monkeypatch):
    captured = {}

    def test_connection(**kwargs):
        captured.update(kwargs)
        return "连接成功，模型可用于策略建议"

    monkeypatch.setattr(main, "verify_ai_connection", test_connection)

    response = client.post(
        "/api/v1/ai/test",
        headers={
            "X-AI-API-Key": "sk-browser-test",
            "X-AI-Provider": "zhipu",
            "X-AI-Model": "glm-5.3",
            "X-AI-Base-URL": "https://open.bigmodel.cn/api/paas/v4",
        },
    )

    assert response.status_code == 200
    assert response.json()["message"] == "连接成功，模型可用于策略建议"
    assert captured == {
        "api_key": "sk-browser-test",
        "provider": "zhipu",
        "model": "glm-5.3",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
    }


def test_pack_endpoint_returns_structured_must_load_error():
    payload = {
        "container": {
            "id": "small",
            "name": "小型测试柜",
            "inner_length_mm": 1000,
            "inner_width_mm": 1000,
            "inner_height_mm": 1000,
            "door_width_mm": 1000,
            "door_height_mm": 1000,
            "max_payload_g": 1000000,
        },
        "cargo_items": [
            {
                "id": "a",
                "sku": "A",
                "name": "必装箱",
                "kind": "carton",
                "length_mm": 1000,
                "width_mm": 1000,
                "height_mm": 1000,
                "weight_g": 100000,
                "quantity": 2,
                "allowed_orientations": ["LWH"],
                "stackable": False,
                "max_layers": 1,
                "max_top_load_g": 0,
                "must_load": True,
            }
        ],
    }

    response = client.post("/api/v1/pack", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "MUST_LOAD_UNSATISFIED"


def test_returns_structured_validation_error():
    response = client.post(
        "/api/v1/pack",
        json={"container": {"id": "bad"}, "cargo_items": []},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
    assert response.json()["error"]["details"]


def test_rejects_request_body_larger_than_one_megabyte():
    response = client.post(
        "/api/v1/pack",
        content=b"{}",
        headers={"content-type": "application/json", "content-length": "1048577"},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_TOO_LARGE"


def test_frontend_path_cannot_escape_distribution_directory(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    index = dist / "index.html"
    index.write_text("app", encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("secret", encoding="utf-8")

    resolved = resolve_frontend_path("../secret.txt", dist)

    assert resolved == index


def test_unknown_api_route_returns_json_404():
    response = client.get("/api/v1/not-found")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


def test_timeout_releases_calculation_slot(monkeypatch):
    payload = {
        "container": {
            "id": "small",
            "name": "小型测试柜",
            "inner_length_mm": 1000,
            "inner_width_mm": 1000,
            "inner_height_mm": 1000,
            "door_width_mm": 1000,
            "door_height_mm": 1000,
            "max_payload_g": 1000000,
        },
        "cargo_items": [
            {
                "id": "a",
                "sku": "A",
                "name": "标准箱",
                "kind": "carton",
                "length_mm": 1000,
                "width_mm": 1000,
                "height_mm": 1000,
                "weight_g": 100000,
                "quantity": 1,
                "allowed_orientations": ["LWH"],
                "stackable": False,
                "max_layers": 1,
                "max_top_load_g": 0,
            }
        ],
    }

    async def slow_calculation(_request):
        await asyncio.sleep(0.1)

    monkeypatch.setattr(main, "run_pack_calculation", slow_calculation)
    monkeypatch.setattr(main, "PACK_TIMEOUT_SECONDS", 0.01)

    first = client.post("/api/v1/pack", json=payload)
    second = client.post("/api/v1/pack", json=payload)
    third = client.post("/api/v1/pack", json=payload)

    assert [first.status_code, second.status_code, third.status_code] == [504, 504, 504]


def test_packing_failure_response_includes_chinese_hint(monkeypatch):
    from app import main
    from app.packing import PackingFailure

    payload = {
        "container": {
            "id": "small",
            "name": "小型测试柜",
            "inner_length_mm": 2000,
            "inner_width_mm": 1000,
            "inner_height_mm": 1000,
            "door_width_mm": 1000,
            "door_height_mm": 1000,
            "max_payload_g": 1000000,
            "clearance_mm": 0,
        },
        "cargo_items": [
            {
                "id": "a",
                "sku": "A",
                "name": "标准箱",
                "kind": "carton",
                "length_mm": 1000,
                "width_mm": 1000,
                "height_mm": 1000,
                "weight_g": 100000,
                "quantity": 1,
                "allowed_orientations": ["LWH"],
                "stackable": False,
                "max_layers": 1,
                "max_top_load_g": 0,
                "fragile": False,
                "must_load": False,
            }
        ],
    }

    def failing_calculation(_request):
        raise PackingFailure(
            "LAYOUT_NOT_FEASIBLE",
            "当前货物参数无法生成有效的装柜方案，请根据下方提示调整后重试",
            hint="整托不可叠放，请取消整托的“可叠”选项，或减少整托数量",
        )

    monkeypatch.setattr(main, "run_pack_calculation", failing_calculation)

    response = client.post("/api/v1/pack", json=payload)

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "LAYOUT_NOT_FEASIBLE"
    assert body["error"]["hint"] == "整托不可叠放，请取消整托的“可叠”选项，或减少整托数量"
