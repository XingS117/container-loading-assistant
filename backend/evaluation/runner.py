import json
import os
import platform
import re
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

from app.models import PackRequest
from app.packing import pack_order
from .metrics import digest, summarize_solution

BACKEND = Path(__file__).resolve().parents[1]
PROFILES = {'high_fill', 'stable', 'easy'}
SOURCE_KINDS = {'synthetic', 'historical_reproduction', 'customer_template', 'real_order'}


def anonymize_request(raw: dict) -> dict:
    # Pick model fields explicitly so API keys and arbitrary nested extras never enter reports.
    clean = {key: value for key, value in raw.items() if key in PackRequest.model_fields and key != 'ai_layout_hint'}
    request = PackRequest.model_validate(clean)
    ids = {cargo.id: f'sku-{i + 1:02d}' for i, cargo in enumerate(request.cargo_items)}
    request.container = request.container.model_copy(update={'id':'container', 'name':'container'})
    for cargo in request.cargo_items:
        cargo.id = cargo.sku = cargo.name = ids[cargo.id]
    for i, placed in enumerate(request.locked_placements):
        placed.cargo_id = ids[placed.cargo_id]
        placed.id = f'locked-{i + 1:04d}'
    return request.model_dump(mode='json', exclude={'ai_layout_hint'})


def load_cases(directory: Path) -> list[dict]:
    cases = []
    seen = set()
    for path in sorted(directory.glob('*.json')):
        case = json.loads(path.read_text(encoding='utf-8-sig'))
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}', case.get('id', '')):
            raise ValueError('invalid case id')
        if case['id'] in seen:
            raise ValueError('duplicate case id')
        seen.add(case['id'])
        source = case.get('source', {})
        if source.get('kind') not in SOURCE_KINDS or not source.get('evidence') or not isinstance(source.get('limitations'), list):
            raise ValueError('case source and limitations required')
        if case.get('acceptance') != 'unreviewed':
            raise ValueError('field acceptance requires a separate audited reference layout; not supported yet')
        if source['kind'] == 'real_order' and not source.get('verified_on'):
            raise ValueError('real_order source requires verified_on')
        case['request'] = anonymize_request(case['request'])
        cases.append(case)
    if not cases:
        raise ValueError('no evaluation cases')
    return cases


def evaluate(raw: dict, budget_s: float) -> dict:
    request = PackRequest.model_validate(raw)
    started = time.perf_counter()
    response = pack_order(request, time_budget_seconds=budget_s)
    solve_s = time.perf_counter() - started
    profiles = {solution.profile: summarize_solution(request, solution) for solution in response.solutions}
    valid = len(response.solutions) == 3 and set(profiles) == PROFILES and all(p['valid'] for p in profiles.values())
    return {'status':'ok' if valid else 'invalid', 'solve_s':round(solve_s, 6),
            'validation_s':round(time.perf_counter() - started - solve_s, 6), 'profiles':profiles}


def run_once(raw: dict, budget_s: float, timeout_s: float) -> dict:
    env = dict(os.environ, PYTHONPATH=str(BACKEND), PYTHONHASHSEED='0')
    started = time.perf_counter()
    try:
        result = subprocess.run([sys.executable, '-m', 'evaluation', '_worker'],
                                input=json.dumps({'request':raw, 'budget_s':budget_s}),
                                text=True, capture_output=True, timeout=timeout_s, cwd=BACKEND, env=env)
        if result.returncode:
            return {'status':'error', 'error':'WORKER_EXIT', 'exit_code':result.returncode, 'profiles':{}}
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        return {'status':'timeout', 'wall_s':round(time.perf_counter() - started, 3), 'profiles':{}}


def environment() -> dict:
    return {'python':platform.python_version(), 'system':platform.platform(),
            'processor':platform.processor(), 'cpu_count':os.cpu_count(),
            'packages':{name:version(name) for name in ['pydantic', 'rectpack']}}


def revision() -> dict:
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=BACKEND, text=True).strip()
    source = {str(path.relative_to(BACKEND)):path.read_text(encoding='utf-8-sig')
              for folder in ['app', 'evaluation'] for path in sorted((BACKEND / folder).glob('*.py'))}
    return {'commit':git('rev-parse', 'HEAD'), 'tracked_dirty':bool(git('diff', 'HEAD', '--name-only')),
            'untracked_code':bool(git('ls-files', '--others', '--exclude-standard', '--', 'app', 'evaluation')),
            'source_sha256':digest(source)}


def run_suite(cases, budget_s=15, timeout_s=45, repeats=2) -> dict:
    report = {'schema_version':1, 'created_at':datetime.now(timezone.utc).isoformat(),
              'revision':revision(), 'environment':environment(),
              'settings':{'budget_s':budget_s, 'timeout_s':timeout_s, 'repeats':repeats}, 'cases':[]}
    for case in cases:
        runs = [run_once(case['request'], budget_s, timeout_s) for _ in range(repeats)]
        ok = all(run['status'] == 'ok' for run in runs)
        fingerprints = [digest(run.get('profiles', {})) for run in runs]
        row = {key:case[key] for key in ['id', 'source', 'acceptance']}
        row.update(input_sha256=digest(case['request']), requested_pieces=sum(c['quantity'] for c in case['request']['cargo_items']),
                   status='ok' if ok else next(r['status'] for r in runs if r['status'] != 'ok'),
                   layout_repeatable=ok and len(set(fingerprints)) == 1, runs=runs,
                   profiles=runs[0].get('profiles', {}), timing={})
        if ok:
            row['timing'] = {'solve_median_s':round(statistics.median(r['solve_s'] for r in runs), 6),
                             'solve_max_s':max(r['solve_s'] for r in runs),
                             'validation_median_s':round(statistics.median(r['validation_s'] for r in runs), 6)}
        report['cases'].append(row)
        print(f"{case['id']}: {row['status']}, repeatable={row['layout_repeatable']}", file=sys.stderr, flush=True)
    return report


def compare_reports(current: dict, baseline: dict) -> dict:
    issues = []
    rows = []
    for key in ['schema_version', 'settings', 'environment']:
        if current.get(key) != baseline.get(key):
            issues.append(f'{key} mismatch')
    old = {c['id']:c for c in baseline['cases']}
    new = {c['id']:c for c in current['cases']}
    if len(old) != len(baseline['cases']) or len(new) != len(current['cases']):
        issues.append('duplicate cases')
    if old.keys() != new.keys():
        issues.append('case set mismatch')
    if not old or not new:
        issues.append('empty case set')
    for case_id in sorted(old.keys() & new.keys()):
        a, b = new[case_id], old[case_id]
        if any(a.get(key) != b.get(key) for key in ['input_sha256', 'source', 'acceptance']):
            issues.append(f'{case_id}: input/provenance mismatch')
            continue
        if a['status'] != 'ok' or b['status'] != 'ok' or not a['layout_repeatable'] or not b['layout_repeatable']:
            issues.append(f'{case_id}: failed or non-repeatable run')
            continue
        if set(a['profiles']) != PROFILES or set(b['profiles']) != PROFILES:
            issues.append(f'{case_id}: incomplete profiles')
            continue
        if not all(p.get('valid') is True for case in [a, b] for p in case['profiles'].values()):
            issues.append(f'{case_id}: invalid profile')
            continue
        for profile, values in a['profiles'].items():
            previous = b['profiles'][profile]
            if values.keys() != previous.keys():
                issues.append(f'{case_id}: metric set mismatch')
                continue
            deltas = {key:round(value - previous[key], 6) if type(value) in (int, float) and type(previous[key]) in (int, float) else None
                      for key, value in values.items() if value is None or type(value) in (int, float)}
            rows.append({'case_id':case_id, 'profile':profile, 'deltas':deltas,
                         'solve_median_delta_s':round(a['timing']['solve_median_s'] - b['timing']['solve_median_s'], 6)})
    return {'complete':not issues, 'issues':issues, 'rows':rows if not issues else []}


def markdown(report: dict) -> str:
    lines = ['# Layout evaluation', '', f"Revision: `{report['revision']['commit']}`; tracked dirty: {report['revision']['tracked_dirty']}.",
             '', 'Offline deterministic solver only; no AI, network or queue latency. Safety validity is not field acceptance.',
             'Void = bounding rectangle minus footprint union at the same base height; includes edge notches and configured gaps.',
             'Upper void = maximum per-layer void. Support = minimum direct bottom-face support; N/A means no upper cargo.',
             '', '| Case / source | Profile | Loaded / requested | Valid | Floor void m2 | Upper void m2 | Min support % | X / Y imbalance % | Steps | Solve median / max s |',
             '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    def fmt(value):
        return 'N/A' if value is None else str(value)
    for case in report['cases']:
        for profile, p in case['profiles'].items():
            t = case['timing']
            lines.append(f"| {case['id']} / {case['source']['kind']} | {profile} | {p['loaded_pieces']} / {case['requested_pieces']} | {p['valid']} | {fmt(p['floor_bbox_void_m2'])} | {fmt(p['upper_layer_max_void_m2'])} | {fmt(p['min_support_pct'])} | {p['length_imbalance_pct']} / {p['width_imbalance_pct']} | {p['loading_steps']} | {t.get('solve_median_s', 'N/A')} / {t.get('solve_max_s', 'N/A')} |")
    lines += ['', '## Provenance and run status']
    for case in report['cases']:
        lines += ['', f"{case['id']}: status={case['status']}; repeatable={case['layout_repeatable']}; acceptance={case['acceptance']}.",
                  f"Source: {case['source']['evidence']}", *[f'- {item}' for item in case['source']['limitations']], '']
    if 'comparison' in report:
        comp = report['comparison']
        lines += ['## Baseline comparison', '', f"Comparable: {comp['complete']}", *[f'- {issue}' for issue in comp['issues']],
                  '', '| Case | Profile | Loaded delta | Floor void delta m2 | Upper void delta m2 | X imbalance delta pp | Steps delta | Solve median delta s |',
                  '| --- | --- | --- | --- | --- | --- | --- | --- |']
        for row in comp['rows']:
            d = row['deltas']
            lines.append(f"| {row['case_id']} | {row['profile']} | {fmt(d.get('loaded_pieces'))} | {fmt(d.get('floor_bbox_void_m2'))} | {fmt(d.get('upper_layer_max_void_m2'))} | {fmt(d.get('length_imbalance_pct'))} | {fmt(d.get('loading_steps'))} | {row['solve_median_delta_s']} |")
        lines += ['', 'Deltas are current minus baseline. Fewer pieces and smaller voids are a tradeoff, not an unconditional improvement. Timing changes are observations, not a performance guarantee.']
    return '\n'.join(lines).rstrip() + '\n'
