import copy
import json
import sys
from pathlib import Path

import pytest

from app.models import CargoSpec, ContainerSpec, PackRequest, Placement
from app.packing import _build_solution
from evaluation.metrics import geometry_metrics, summarize_solution
from evaluation.runner import anonymize_request, compare_reports, load_cases, run_once


def request():
    return PackRequest(
        container=ContainerSpec(id='private-client', name='private-client', inner_length_mm=2000,
                                inner_width_mm=1000, inner_height_mm=2000, door_width_mm=1000,
                                door_height_mm=2000, max_payload_g=1000000),
        cargo_items=[CargoSpec(id='private-sku', sku='private-sku', name='private-client', kind='carton',
                              length_mm=1000, width_mm=1000, height_mm=500, weight_g=100,
                              quantity=3, allowed_orientations=['LWH'], stackable=True,
                              max_layers=3, max_top_load_g=1000)], door_buffer_mm=0,
    )


def placement(index, x=0, z=0):
    return Placement(id=f'p{index}', cargo_id='private-sku', instance_index=index, x_mm=x,
                     y_mm=0, z_mm=z, length_mm=1000, width_mm=1000, height_mm=500,
                     rotation='LWH', weight_g=100, step=index + 1)


def test_geometry_distinguishes_empty_space_from_missing_support():
    req = request()
    pieces = [placement(0), placement(1, x=1500), placement(2, x=500, z=500)]
    metrics = geometry_metrics(req, pieces)
    assert metrics['floor_bbox_void_m2'] == 0.5
    assert metrics['min_support_pct'] == 50
    assert metrics['unsupported_upper_pieces'] == 1
    assert metrics['upper_layer_max_void_m2'] == 0
    assert geometry_metrics(req, pieces[:1])['min_support_pct'] is None
    assert geometry_metrics(req, [])['floor_bbox_void_m2'] is None


def test_overlapping_supports_do_not_double_count_support_area():
    pieces = [placement(0), placement(1), placement(2, x=500, z=500)]
    assert geometry_metrics(request(), pieces)['min_support_pct'] == 50


def test_layer_void_does_not_merge_different_heights():
    pieces = [placement(0), placement(1, z=500), placement(2, x=1500, z=1000)]
    assert geometry_metrics(request(), pieces)['upper_layer_max_void_m2'] == 0


def test_summary_detects_physical_and_count_errors():
    req = request()
    solution = _build_solution(req, [], 'high_fill', fixed_placements=[placement(0), placement(1, z=500)])
    assert summarize_solution(req, solution)['valid']
    solution.loaded_counts['private-sku'] = 3
    assert 'COUNT_MISMATCH' in summarize_solution(req, solution)['errors']
    solution.placements[1] = placement(1, z=600)
    assert 'UNSUPPORTED' in summarize_solution(req, solution)['errors']


def test_anonymization_preserves_physics_but_never_keys_or_labels():
    raw = request().model_dump(mode='json')
    raw.update(apiKey='secret-value', ai_layout_hint={'message':'secret-value'})
    raw['locked_placements'] = [placement(0).model_dump(mode='json')]
    sanitized = anonymize_request(raw)
    encoded = json.dumps(sanitized)
    assert 'secret-value' not in encoded and 'private' not in encoded
    assert sanitized['cargo_items'][0]['quantity'] == 3
    assert sanitized['cargo_items'][0]['weight_g'] == 100
    assert sanitized['locked_placements'][0]['cargo_id'] == sanitized['cargo_items'][0]['id']
    PackRequest.model_validate(sanitized)


def report():
    return {'schema_version':1, 'settings':{'budget_s':15, 'timeout_s':45, 'repeats':2},
            'environment':{'python':'test'}, 'cases':[{
                'id':'sample', 'input_sha256':'a', 'source':{'kind':'synthetic'},
                'status':'ok', 'timing':{'solve_median_s':1},
                'profiles':{profile:{'valid':True, 'loaded_pieces':10, 'floor_bbox_void_m2':1,
                                    'min_support_pct':None} for profile in ['easy', 'high_fill', 'stable']}, 'layout_repeatable':True}]}


def test_comparison_preserves_tradeoffs_and_rejects_changed_inputs():
    baseline = report()
    current = copy.deepcopy(baseline)
    current['cases'][0]['profiles']['easy'].update(loaded_pieces=9, floor_bbox_void_m2=0.5)
    comparison = compare_reports(current, baseline)
    assert comparison['complete']
    row = comparison['rows'][0]
    assert row['deltas']['loaded_pieces'] == -1
    assert row['deltas']['floor_bbox_void_m2'] == -0.5
    assert row['deltas']['min_support_pct'] is None
    current['cases'][0]['input_sha256'] = 'b'
    assert not compare_reports(current, baseline)['complete']
    current = report()
    current['settings']['budget_s'] = 10
    assert not compare_reports(current, baseline)['complete']
    current = report()
    current['cases'] = []
    assert not compare_reports(current, baseline)['complete']


def test_failed_case_cannot_be_hidden_as_improvement():
    current = report()
    current['cases'][0].update(status='timeout', profiles={})
    assert not compare_reports(current, report())['complete']


def test_comparison_rejects_missing_profile_and_contradictory_validity():
    current = report()
    del current['cases'][0]['profiles']['stable']
    assert not compare_reports(current, current)['complete']
    current = report()
    current['cases'][0]['profiles']['easy']['valid'] = False
    assert not compare_reports(current, report())['complete']


def test_comparison_rejects_environment_and_nonrepeatable_results():
    current = report()
    current['environment']['python'] = 'different'
    comparison = compare_reports(current, report())
    assert not comparison['complete'] and comparison['rows'] == []
    current = report()
    current['cases'][0]['layout_repeatable'] = False
    assert not compare_reports(current, report())['complete']


def test_report_output_cannot_overwrite_baseline_or_markdown(monkeypatch, tmp_path):
    from evaluation.__main__ import main
    def unexpected_run(*args):
        raise AssertionError('report execution should not start for invalid output paths')
    monkeypatch.setattr('evaluation.__main__.run_suite', unexpected_run)
    for args in [ ['--output', str(tmp_path / 'bad.md')],
                  ['--output', str(tmp_path / 'old.json'), '--baseline', str(tmp_path / 'old.json')]]:
        monkeypatch.setattr(sys, 'argv', ['evaluation', 'run', *args])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 2


def test_catches_step_zone_and_duplicate_identity_errors():
    req = request()
    solution = _build_solution(req, [], 'high_fill', fixed_placements=[placement(0), placement(1, z=500)])
    solution.zones[0].piece_count += 1
    solution.placements[1].id = solution.placements[0].id
    errors = summarize_solution(req, solution)['errors']
    assert 'STEP_COUNT_MISMATCH' in errors and 'DUPLICATE_ID' in errors


def test_dataset_rejects_duplicate_ids_and_missing_provenance(tmp_path):
    sample = {'id':'same', 'source':{'kind':'synthetic', 'evidence':'unit test', 'limitations':[]},
              'acceptance':'unreviewed', 'request':anonymize_request(request().model_dump())}
    for name in ['a', 'b']:
        (tmp_path / f'{name}.json').write_text(json.dumps(sample), encoding='utf-8')
    with pytest.raises(ValueError, match='duplicate'):
        load_cases(tmp_path)
    (tmp_path / 'b.json').unlink()
    sample.pop('source')
    (tmp_path / 'a.json').write_text(json.dumps(sample), encoding='utf-8')
    with pytest.raises(ValueError, match='source'):
        load_cases(tmp_path)


def test_isolated_runner_completes_and_enforces_wall_timeout():
    raw = anonymize_request(request().model_dump())
    result = run_once(raw, budget_s=2, timeout_s=15)
    assert result['status'] == 'ok'
    assert set(result['profiles']) == {'high_fill', 'stable', 'easy'}
    assert run_once(raw, budget_s=2, timeout_s=0.001)['status'] == 'timeout'


def test_committed_cases_are_valid_and_do_not_claim_field_acceptance():
    cases = load_cases(Path(__file__).parents[1] / 'evaluation' / 'cases')
    assert len(cases) >= 5
    assert all(case['acceptance'] == 'unreviewed' for case in cases)
    assert sum(case['source']['kind'] == 'real_order' for case in cases) == 0
