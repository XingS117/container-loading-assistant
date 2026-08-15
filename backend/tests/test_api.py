import asyncio

from fastapi.testclient import TestClient

from app import main
from app.main import app, resolve_frontend_path


client = TestClient(app)


def test_health_and_container_presets():
    assert client.get("/health").json() == {"status": "ok"}

    response = client.get("/api/v1/container-presets")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == ["20gp", "40gp", "40hq"]


def _small_payload(**overrides):
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
    payload.update(overrides)
    return payload


def test_pack_endpoint_returns_single_high_fill_solution_by_default():
    response = client.post("/api/v1/pack", json=_small_payload())

    assert response.status_code == 200
    body = response.json()
    assert len(body["solutions"]) == 1
    assert body["solutions"][0]["profile"] == "high_fill"
    assert "identical_to" not in body["solutions"][0]


def test_pack_endpoint_returns_requested_goal_solution():
    for goal in ["high_fill", "stable", "easy"]:
        response = client.post(
            "/api/v1/pack",
            json=_small_payload(optimization_goal=goal),
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["solutions"]) == 1
        assert body["solutions"][0]["profile"] == goal


def test_pack_endpoint_request_id_varies_with_goal():
    high = client.post("/api/v1/pack", json=_small_payload(optimization_goal="high_fill")).json()
    stable = client.post("/api/v1/pack", json=_small_payload(optimization_goal="stable")).json()

    assert high["request_id"] != stable["request_id"]


def test_pack_endpoint_rejects_invalid_optimization_goal():
    response = client.post(
        "/api/v1/pack",
        json=_small_payload(optimization_goal="strict_support"),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


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
