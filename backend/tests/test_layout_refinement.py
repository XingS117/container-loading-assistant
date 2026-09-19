import json
from pathlib import Path

from app.models import PackRequest
from app.packing import pack_order, _pack_order_full
from app.layout_diagnostics import diagnose_layout
from app.layout_refinement import refine_rows
from app.validator import validate_solution


def test_row_refinement_reduces_upper_void_without_changing_cargo_or_balance():
    path=Path(__file__).parents[1]/'evaluation/cases/historical-six-sku.json'
    req=PackRequest.model_validate(json.loads(path.read_text())['request'])
    original=_pack_order_full(req).solutions[1]
    refined=refine_rows(req,original)
    before,after=diagnose_layout(req,original.placements),diagnose_layout(req,refined.placements)
    assert after.upper_max_void_m2 < before.upper_max_void_m2
    assert after.upper_continuity_pct >= before.upper_continuity_pct
    assert refined.metrics.length_imbalance_pct <= original.metrics.length_imbalance_pct
    assert refined.metrics.width_imbalance_pct <= original.metrics.width_imbalance_pct
    assert refined.loaded_counts == original.loaded_counts
    assert {(p.id,p.cargo_id,p.instance_index,p.length_mm,p.width_mm,p.height_mm,p.weight_g,p.rotation) for p in refined.placements} == {(p.id,p.cargo_id,p.instance_index,p.length_mm,p.width_mm,p.height_mm,p.weight_g,p.rotation) for p in original.placements}
    assert validate_solution(req.container,req.cargo_items,refined.placements,item_gap_mm=req.item_gap_mm).valid


def test_locked_rows_are_never_moved_and_door_reserve_not_reduced():
    path=Path(__file__).parents[1]/'evaluation/cases/customer-five-sku.json'
    req=PackRequest.model_validate(json.loads(path.read_text())['request'])
    solution=pack_order(req).solutions[1]
    req.locked_placements=solution.placements[:1]
    assert refine_rows(req,solution) is solution
