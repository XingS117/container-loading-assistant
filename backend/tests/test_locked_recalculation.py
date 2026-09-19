import pytest

from app.models import PackRequest
from app.packing import PackingFailure, pack_order
from app.validator import validate_solution


def request_with_lock(**updates):
    body = {
        "container": {"id": "test", "name": "test", "inner_length_mm": 2500, "inner_width_mm": 1500, "inner_height_mm": 1500, "door_width_mm": 1500, "door_height_mm": 1500, "max_payload_g": 10000},
        "cargo_items": [{"id": "a", "sku": "A", "name": "A", "kind": "carton", "length_mm": 500, "width_mm": 500, "height_mm": 500, "weight_g": 1000, "quantity": 3, "allowed_orientations": ["LWH"], "stackable": True, "max_layers": 2, "max_top_load_g": 1000}],
        "locked_placements": [{"id": "a-2", "cargo_id": "a", "instance_index": 2, "x_mm": 0, "y_mm": 0, "z_mm": 0, "length_mm": 500, "width_mm": 500, "height_mm": 500, "rotation": "LWH", "weight_g": 1000, "step": 9}],
        "door_buffer_mm": 300,
    }
    body.update(updates)
    return PackRequest.model_validate(body)


def assert_preserved(request, response):
    for solution in response.solutions:
        assert validate_solution(request.container, request.cargo_items, solution.placements, item_gap_mm=request.item_gap_mm).valid
        assert len({p.id for p in solution.placements}) == len(solution.placements)
        assert sum(z.piece_count for z in solution.zones) == len(solution.placements)
        by_id = {p.id: p for p in solution.placements}
        for locked in request.locked_placements:
            assert by_id[locked.id].model_dump(exclude={"step"}) == locked.model_dump(exclude={"step"})


def test_nonconsecutive_locked_instances_are_not_generated_again():
    request = request_with_lock()
    response = pack_order(request)
    assert_preserved(request, response)
    assert all(s.loaded_counts["a"] == 3 for s in response.solutions)


def test_complete_locked_stack_remains_in_place():
    request = request_with_lock()
    request.locked_placements.append(request.locked_placements[0].model_copy(update={"id": "a-0", "instance_index": 0, "z_mm": 500, "step": 1}))
    response = pack_order(request)
    assert_preserved(request, response)
    for s in response.solutions:
        steps = {p.id: p.step for p in s.placements}
        assert steps["a-2"] < steps["a-0"]


def test_locked_subset_does_not_require_all_must_load_pieces_yet():
    request = request_with_lock()
    request.cargo_items[0].must_load = True
    response = pack_order(request)
    assert_preserved(request, response)
    assert all(s.loaded_counts["a"] == 3 for s in response.solutions)


def test_new_cargo_respects_gap_and_container_clearance():
    request = request_with_lock(item_gap_mm=20)
    request.container.clearance_mm = 10
    request.locked_placements[0] = request.locked_placements[0].model_copy(update={"x_mm": 10, "y_mm": 10, "z_mm": 10})
    response = pack_order(request)
    assert_preserved(request, response)
    assert all(s.loaded_counts["a"] == 3 for s in response.solutions)


def test_locked_weight_is_deducted_from_remaining_payload():
    request = request_with_lock()
    request.container.max_payload_g = 2000
    response = pack_order(request)
    assert_preserved(request, response)
    assert all(s.metrics.loaded_pieces == 2 for s in response.solutions)


def test_locked_timeout_never_falls_back_to_unlocked_layout():
    request = request_with_lock()
    with pytest.raises(PackingFailure, match="锁定"):
        pack_order(request, time_budget_seconds=0)


def test_unsupported_locked_upper_box_is_still_rejected():
    request = request_with_lock()
    request.locked_placements[0].z_mm = 500
    with pytest.raises(PackingFailure, match="锁定布局无效"):
        pack_order(request)


def test_fully_used_payload_keeps_only_locked_cargo():
    request = request_with_lock()
    request.container.max_payload_g = 1000
    response = pack_order(request)
    assert_preserved(request, response)
    assert all(s.metrics.loaded_pieces == 1 for s in response.solutions)


def test_new_cargo_keeps_door_buffer():
    request = request_with_lock()
    request.cargo_items[0].quantity = 10
    response = pack_order(request)
    assert_preserved(request, response)
    assert all(p.x_mm + p.length_mm <= 2200 for s in response.solutions for p in s.placements)


def test_duplicate_locked_ids_are_rejected():
    request = request_with_lock()
    body = request.model_dump()
    body["locked_placements"].append({**body["locked_placements"][0], "instance_index": 0, "x_mm": 1000})
    with pytest.raises(ValueError, match="标识不能重复"):
        PackRequest.model_validate(body)
