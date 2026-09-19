"""Compare bounded whole-row permutations, preserving vertical supports."""
from collections import defaultdict
from itertools import combinations, islice

from .layout_diagnostics import components, diagnose_layout, rect
from .validator import validate_solution


def refine_rows(request, solution):
    if request.locked_placements or solution.profile=='high_fill' or len(solution.placements)>200:
        return solution
    before=diagnose_layout(request,solution.placements)
    if not before.complete or (before.upper_max_void_m2 or 0)<0.25:
        return solution
    groups=defaultdict(list)
    for p in solution.placements:
        groups[p.x_mm].append(p)
    rows=[groups[x] for x in sorted(groups)]
    if not 2<=len(rows)<=24:
        return solution
    from .packing import PackingBudgetExceeded, _build_solution, _packing_deadline
    from time import monotonic
    # Refinement must not consume the time reserved for returning a safe result.
    deadline = min(monotonic() + 1.5, _packing_deadline.get() or float('inf'))
    def upper_components(pieces):
        return len(components([rect(p) for p in pieces if p.z_mm > request.container.clearance_mm], request.item_gap_mm + 1))
    original_components = upper_components(solution.placements)
    priorities={c.id:c.unload_order for c in request.cargo_items}
    def inversions(pieces):
        specified=[p for p in pieces if priorities[p.cargo_id]]
        return sum(a.x_mm<b.x_mm and priorities[a.cargo_id]<priorities[b.cargo_id] for a in specified for b in specified)
    original_inversions=inversions(solution.placements)
    original_end=max(p.x_mm+p.length_mm for p in solution.placements)
    best=solution
    def score(s,d):
        if s.profile=='easy':
            return (-s.metrics.loading_steps,-d.sku_switches,-d.estimated_handling_distance_m,
                    -(d.upper_max_void_m2 or 0),d.upper_continuity_pct or 0)
        return (-(d.upper_max_void_m2 or 0),d.upper_continuity_pct or 0,
                -max(s.metrics.length_imbalance_pct,s.metrics.width_imbalance_pct))
    best_score=score(best,before)
    for i,j in islice(combinations(range(len(rows)),2),96):
        if monotonic() >= deadline:
            break
        order=list(rows)
        order[i],order[j]=order[j],order[i]
        x=min(p.x_mm for p in solution.placements)
        pieces=[]
        for row in order:
            pieces.extend(p.model_copy(update={'x_mm':x}) for p in row)
            x+=max(p.length_mm for p in row)+request.item_gap_mm
        if max(p.x_mm+p.length_mm for p in pieces)>original_end or inversions(pieces)>original_inversions:
            continue
        # Mixed-height upper cargo must retain its overall planar continuity too.
        if upper_components(pieces) > original_components:
            continue
        if not validate_solution(request.container,request.cargo_items,pieces,item_gap_mm=request.item_gap_mm).valid:
            continue
        try:
            candidate=_build_solution(request,[],solution.profile,fixed_placements=pieces)
        except PackingBudgetExceeded:
            break
        d=diagnose_layout(request,pieces)
        if not d.complete or candidate.loaded_counts!=solution.loaded_counts:
            continue
        if candidate.metrics.length_imbalance_pct>solution.metrics.length_imbalance_pct or candidate.metrics.width_imbalance_pct>solution.metrics.width_imbalance_pct:
            continue
        if (d.floor_void_m2 or 0)>(before.floor_void_m2 or 0) or (d.upper_max_void_m2 or 0)>(before.upper_max_void_m2 or 0):
            continue
        if (d.upper_continuity_pct or 100)<(before.upper_continuity_pct or 100):
            continue
        current=score(candidate,d)
        if current>best_score:
            best,best_score=candidate,current
    return best
