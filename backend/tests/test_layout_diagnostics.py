from app.models import PackRequest, CargoSpec, ContainerSpec, Placement
from app.layout_diagnostics import diagnose_layout, explain_solutions
from app.packing import _build_solution, pack_order


def request():
    return PackRequest(container=ContainerSpec(id='c', name='c', inner_length_mm=4000,
        inner_width_mm=2000, inner_height_mm=2000, door_width_mm=2000, door_height_mm=2000,
        max_payload_g=1000000), cargo_items=[CargoSpec(id='a', sku='a', name='a', kind='carton',
        length_mm=1000, width_mm=1000, height_mm=500, weight_g=100, quantity=4,
        allowed_orientations=['LWH'], stackable=True, max_layers=4, max_top_load_g=1000)], door_buffer_mm=0)


def piece(i, x=0, y=0, z=0):
    return Placement(id=f'a-{i}',cargo_id='a',instance_index=i,x_mm=x,y_mm=y,z_mm=z,
        length_mm=1000,width_mm=1000,height_mm=500,weight_g=100,rotation='LWH',step=i+1)


def test_gaps_have_exact_geometry_area_and_actionable_locations():
    result = diagnose_layout(request(), [piece(0), piece(1,x=2000)])
    assert result.floor_void_m2 == 1
    assert result.large_void_count == 1
    region = result.regions[0]
    assert region.x_mm == 1000 and region.length_mm == 1000
    assert region.area_m2 == 1 and region.z_mm == 0
    assert result.min_support_pct is None
    assert result.estimated_handling_distance_m == 10


def test_support_is_distinct_from_continuity_and_height_layers():
    pieces = [piece(0),piece(1,x=2000),piece(2,z=500),piece(3,x=2000,z=500)]
    result = diagnose_layout(request(), pieces)
    assert result.min_support_pct == 100
    assert result.upper_continuity_pct == 50
    assert result.upper_fragment_count == 2
    assert result.upper_max_void_m2 == 1
    assert result.large_void_count == 2
    moved = [*pieces[:3],piece(3,x=500,z=1000)]
    assert diagnose_layout(request(), moved).min_support_pct == 50


def test_single_upper_piece_is_contiguous_but_no_upper_is_not_applicable():
    assert diagnose_layout(request(), [piece(0),piece(1,z=500)]).upper_continuity_pct == 100
    assert diagnose_layout(request(), []).upper_continuity_pct is None
    assert diagnose_layout(request(), [piece(0),piece(1,z=500),piece(2,z=1000)]).upper_continuity_pct == 100


def test_explanations_disclose_tradeoffs_and_do_not_promise_global_optimum():
    req=request()
    high=_build_solution(req,[],'high_fill',fixed_placements=[piece(0),piece(1,x=1000)])
    easy=_build_solution(req,[],'easy',fixed_placements=[piece(0)])
    explain_solutions(req,[high,easy],status='budget_fallback')
    assert easy.assessment.status == 'budget_fallback'
    assert easy.assessment.deltas['loaded_pieces'] == -1
    assert any('少装 1' in item for item in easy.assessment.tradeoffs)
    assert easy.assessment.unmet_soft_goals
    assert '全局最优' in easy.assessment.limits


def test_every_solver_path_gets_assessment_and_fallback_is_labelled():
    response=pack_order(request(),time_budget_seconds=0)
    assert len(response.solutions)==3
    assert all(s.assessment.status=='budget_fallback' for s in response.solutions)
    assert all(s.assessment.hard_constraints for s in response.solutions)
    assert response.recommendation_reason
