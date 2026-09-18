from copy import deepcopy

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def payload():
    return {
        "container": {"id": "test", "name": "test", "inner_length_mm": 3000, "inner_width_mm": 2000, "inner_height_mm": 2000, "door_width_mm": 2000, "door_height_mm": 2000, "max_payload_g": 1000000},
        "cargo_items": [{"id": "a", "sku": "A", "name": "A", "kind": "carton", "length_mm": 500, "width_mm": 500, "height_mm": 500, "weight_g": 1000, "quantity": 2, "allowed_orientations": ["LWH"], "stackable": True, "max_layers": 2, "max_top_load_g": 1000}],
        "placements": [{"id": f"a-{i}", "cargo_id": "a", "instance_index": i, "x_mm": 0, "y_mm": 0, "z_mm": i * 500, "length_mm": 500, "width_mm": 500, "height_mm": 500, "rotation": "LWH", "weight_g": 1000, "step": i + 1} for i in range(2)],
    }


def test_review_returns_authoritative_metrics():
    response = client.post("/api/v1/layout/review", json=payload())
    assert response.status_code == 200
    data = response.json()
    assert data["valid"] is True
    assert data["metrics"]["loaded_pieces"] == 2
    assert "floor_largest_gap_mm" in data["metrics"]
    assert data["zones"]


def test_review_rejects_removed_support():
    body = payload()
    body["placements"][0]["x_mm"] = 1000
    data = client.post("/api/v1/layout/review", json=body).json()
    assert data["valid"] is False
    assert any(e["code"] == "UNSUPPORTED" for e in data["errors"])
    assert data["metrics"] is None


def test_review_rejects_duplicate_ids():
    body = payload()
    body["placements"][1]["id"] = "a-0"
    assert client.post("/api/v1/layout/review", json=body).status_code == 422


def test_review_rejects_changed_cargo_dimensions():
    body = payload()
    body["placements"][0]["length_mm"] = 600
    data = client.post("/api/v1/layout/review", json=body).json()
    assert data["valid"] is False
    assert any(e["code"] == "DIMENSIONS_MISMATCH" for e in data["errors"])


def test_review_checks_overlap_load_and_clearance():
    for change, code in [("overlap", "OVERLAP"), ("load", "TOP_LOAD_EXCEEDED"), ("clearance", "OUT_OF_BOUNDS")]:
        body = deepcopy(payload())
        if change == "overlap":
            body["placements"][1]["z_mm"] = 0
        elif change == "load":
            body["cargo_items"][0]["max_top_load_g"] = 1
        else:
            body["container"]["clearance_mm"] = 10
        data = client.post("/api/v1/layout/review", json=body).json()
        assert data["valid"] is False
        assert any(e["code"] == code for e in data["errors"])
