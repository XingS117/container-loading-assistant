from copy import deepcopy

from fastapi.testclient import TestClient
from app.main import app
from app.models import PackRequest, Placement
from app.packing import _build_solution, _compute_zones, _coordinate_placements_to_stacks

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


def test_review_after_removal_preserves_sparse_identity_and_accepts_empty_layout():
    body = payload()
    body["placements"] = [dict(body["placements"][1], z_mm=0)]
    data = client.post("/api/v1/layout/review", json=body).json()
    assert data["valid"] is True
    assert data["metrics"]["loaded_pieces"] == 1
    assert data["placements"][0]["instance_index"] == 1
    assert sum(z["piece_count"] for z in data["zones"]) == 1
    body["placements"] = []
    data = client.post("/api/v1/layout/review", json=body).json()
    assert data["valid"] is True
    assert data["metrics"]["loaded_pieces"] == 0
    assert data["placements"] == data["zones"] == []


def test_review_rejects_removing_must_load_cargo():
    body = payload()
    body["cargo_items"][0]["must_load"] = True
    body["placements"] = body["placements"][:1]
    data = client.post("/api/v1/layout/review", json=body).json()
    assert data["valid"] is False
    assert any(e["code"] == "MUST_LOAD_MISSING" for e in data["errors"])


def test_generated_solution_uses_contiguous_public_step_numbers():
    body = payload()
    request = PackRequest.model_validate(body)
    placements = [Placement.model_validate(p).model_copy(update={"step": 30 + i * 10}) for i, p in enumerate(body["placements"])]
    solution = _build_solution(request, _coordinate_placements_to_stacks(request, placements), "high_fill")
    assert [p.step for p in solution.placements] == [1, 2]
    assert [z.step for z in solution.zones] == [1, 2]
    assert solution.metrics.loading_steps == 2
    assert [p.model_dump(exclude={"step"}) for p in solution.placements] == [p.model_dump(exclude={"step"}) for p in placements]


def test_zones_count_stacked_pieces_only_in_their_own_step():
    body = payload()
    request = PackRequest.model_validate(body)
    placements = [Placement.model_validate(p) for p in body["placements"]]
    zones = _compute_zones(request, placements)
    assert [(z.step, z.piece_count) for z in zones] == [(1, 1), (2, 1)]


def test_zones_count_each_piece_once_with_different_footprints():
    body = payload()
    placements = [Placement.model_validate(p) for p in body["placements"]]
    placements[1] = placements[1].model_copy(update={"step": 1, "length_mm": 300})
    zones = _compute_zones(PackRequest.model_validate(body), placements)
    assert sum(z.piece_count for z in zones) == 2


def test_zones_keep_same_step_stack_count():
    body = payload()
    placements = [Placement.model_validate(p).model_copy(update={"step": 1}) for p in body["placements"]]
    zones = _compute_zones(PackRequest.model_validate(body), placements)
    assert len(zones) == 1
    assert zones[0].piece_count == 2


def test_review_regenerates_steps_after_moving_boxes():
    body = payload()
    body["placements"][0].update(x_mm=1000, step=1)
    body["placements"][1].update(z_mm=0, step=2)
    data = client.post("/api/v1/layout/review", json=body).json()
    assert data["valid"]
    ordered = sorted(data["placements"], key=lambda p: p["step"])
    assert [p["id"] for p in ordered] == ["a-1", "a-0"]
    assert sum(z["piece_count"] for z in data["zones"]) == 2
    assert data["metrics"]["loading_steps"] == 2


def test_review_places_support_before_upper_box_and_preserves_array_order():
    body = payload()
    body["cargo_items"][0].update(quantity=3, max_layers=2, max_top_load_g=2000)
    body["placements"][0].update(x_mm=250, step=8)
    body["placements"][1].update(x_mm=250, step=1)
    body["placements"].append({**body["placements"][0], "id": "a-2", "instance_index": 2, "x_mm": 0, "y_mm": 500})
    data = client.post("/api/v1/layout/review", json=body).json()
    assert data["valid"]
    by_id = {p["id"]: p for p in data["placements"]}
    assert by_id["a-0"]["step"] < by_id["a-1"]["step"]
    assert {p["step"] for p in data["placements"]} == {1, 2, 3}
    assert [p["id"] for p in data["placements"]] == [p["id"] for p in body["placements"]]


def test_invalid_review_does_not_return_executable_placements():
    body = payload()
    body["placements"][1]["z_mm"] = 750
    data = client.post("/api/v1/layout/review", json=body).json()
    assert not data["valid"]
    assert data["placements"] == []


def test_review_waits_for_all_supports_before_loading_a_bridging_box():
    body = payload()
    body["cargo_items"][0]["quantity"] = 3
    body["placements"][1]["x_mm"] = 250
    body["placements"].append({**body["placements"][0], "id": "a-2", "instance_index": 2, "x_mm": 500})
    data = client.post("/api/v1/layout/review", json=body).json()
    assert data["valid"]
    by_id = {p["id"]: p for p in data["placements"]}
    assert by_id["a-1"]["step"] > max(by_id["a-0"]["step"], by_id["a-2"]["step"])
    for p in data["placements"]:
        original = next(item for item in body["placements"] if item["id"] == p["id"])
        assert {k: v for k, v in p.items() if k != "step"} == {k: v for k, v in original.items() if k != "step"}


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
