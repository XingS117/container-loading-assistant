"""Bounded, coordinate-based explanations; never a replacement for validation."""
from collections import defaultdict

from .models import LayoutDiagnostics, LayoutRegion, SolutionAssessment

GOALS = {'high_fill':'优先装入件数和体积利用率', 'stable':'优先完整支撑、连续布局与重心平衡',
         'easy':'优先连续区域、较少步骤和搬运距离，可少装非必装货物'}
LIMITS = ('启发式方案，不保证全局最优。空白按同高度平面包围范围计算，含凹口和间隙；'
          '大空白阈值为 0.25 m²，不等于悬空或可继续装货空间。连续率为各上层平面最大相连区域面积占比的最小值。'
          '搬运距离按每件从柜门到纵向中心往返估算，不含绕行/举升，不代表实测工时。'
          '柜门通行尺寸为硬约束，预留操作空间为软目标，实际不足时另行提示。'
          '仍须现场复核包装强度、绑扎与装卸可达性。')


def rect(p):
    return p.x_mm, p.y_mm, p.x_mm + p.length_mm, p.y_mm + p.width_mm


def intervals(values):
    merged = []
    for a, b in sorted(values):
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    return merged


def planar(rectangles):
    """Disjoint empty rectangles, exact area; edge notches intentionally included."""
    if not rectangles:
        return 0, []
    xs = sorted({v for r in rectangles for v in (r[0], r[2])})
    low, high = min(r[1] for r in rectangles), max(r[3] for r in rectangles)
    gaps = []
    occupied = 0
    for x, end in zip(xs, xs[1:]):
        ys = intervals((r[1], r[3]) for r in rectangles if r[0] < end and r[2] > x)
        start = low
        for a, b in ys:
            if a > start:
                gaps.append((x, start, end, a))
            occupied += (end - x) * (b - a)
            start = b
        if start < high:
            gaps.append((x, start, end, high))
    return occupied, gaps


def components(rectangles, tolerance=0):
    parent = list(range(len(rectangles)))
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    ordered = sorted(range(len(rectangles)), key=lambda i: rectangles[i][0])
    for index, i in enumerate(ordered):
        a = rectangles[i]
        for j in ordered[index + 1:]:
            b = rectangles[j]
            if b[0] > a[2] + tolerance:
                break
            if max(a[1], b[1]) <= min(a[3], b[3]) + tolerance:
                parent[find(j)] = find(i)
    groups = defaultdict(list)
    for i, rectangle in enumerate(rectangles):
        groups[find(i)].append(rectangle)
    return list(groups.values())


def diagnose_layout(request, placements):
    result = LayoutDiagnostics()
    layers, tops = defaultdict(list), defaultdict(list)
    for p in placements:
        layers[p.z_mm].append(p)
        tops[p.z_mm + p.height_mm].append(p)
    ordered = sorted(placements, key=lambda p: (p.step, p.x_mm, p.y_mm, p.z_mm, p.id))
    result.sku_switches = sum(a.cargo_id != b.cargo_id for a, b in zip(ordered, ordered[1:]))
    result.estimated_handling_distance_m = round(sum(2 * max(0, request.container.inner_length_mm - p.x_mm - p.length_mm / 2) for p in placements) / 1000, 2)
    coverage, continuity, fragments, upper_voids = [], [], [], []
    work = 0
    for z, pieces in sorted(layers.items()):
        rectangles = [rect(p) for p in pieces]
        work += len(rectangles) ** 2
        if work > 3_000_000:
            result.complete = False
            continue
        _, gaps = planar(rectangles)
        area = sum((c-a)*(d-b) for a,b,c,d in gaps) / 1_000_000
        if z == request.container.clearance_mm:
            result.floor_void_m2 = round(area, 6)
        else:
            upper_voids.append(area)
            groups = components(rectangles, request.item_gap_mm + 1)
            areas = [planar(group)[0] for group in groups]
            continuity.append(max(areas) / sum(areas) * 100)
            fragments.append(len(groups))
        if len(gaps) > 1500:
            result.complete = False
        else:
            for group in components(gaps):
                gap_area = sum((c-a)*(d-b) for a,b,c,d in group) / 1_000_000
                if gap_area < 0.25:
                    continue
                result.large_void_count += 1
                result.large_void_area_m2 += gap_area
                if len(result.regions) >= 20:
                    continue
                x, y = min(r[0] for r in group), min(r[1] for r in group)
                end, side = max(r[2] for r in group), max(r[3] for r in group)
                result.regions.append(LayoutRegion(id=f'void-{z}-{result.large_void_count}', x_mm=x, y_mm=y, z_mm=z,
                    length_mm=end-x, width_mm=side-y, area_m2=round(gap_area,6),
                    placement_ids=[p.id for p in pieces if p.x_mm <= end and p.x_mm+p.length_mm >= x
                                   and p.y_mm <= side and p.y_mm+p.width_mm >= y][:12]))
        if z <= request.container.clearance_mm:
            continue
        work += len(pieces) * len(tops[z])
        if work > 3_000_000:
            result.complete = False
            continue
        for p in pieces:
            a,b,c,d=rect(p)
            clips=[]
            for support in tops[z]:
                e,f,g,h=rect(support)
                left,bottom,right,top=max(a,e),max(b,f),min(c,g),min(d,h)
                if left < right and bottom < top:
                    clips.append((left,bottom,right,top))
            covered,_ = planar(clips)
            coverage.append(covered/((c-a)*(d-b)))
    if result.complete:
        result.min_support_pct = round(min(coverage)*100,4) if coverage else None
        result.upper_max_void_m2 = round(max(upper_voids),6) if upper_voids else None
        result.upper_fragment_count=max(fragments,default=0)
        result.upper_continuity_pct=round(min(continuity),2) if continuity else None
    result.large_void_area_m2=round(result.large_void_area_m2,6)
    return result


def explain_solutions(request, solutions, status='heuristic'):
    high=next((s for s in solutions if s.profile=='high_fill'),None)
    for solution in solutions:
        d=diagnose_layout(request,solution.placements)
        unmet=[]
        if not d.complete:
            unmet.append('复杂布局的几何解释达到分析上限，空白和连续性指标不完整；安全校验独立执行')
        if d.large_void_count:
            unmet.append(f'仍有 {d.large_void_count} 处大空白，需查看对应高度与区域')
        if solution.metrics.length_imbalance_pct>10 or solution.metrics.width_imbalance_pct>10:
            unmet.append('前后或左右重心偏差仍超过 10%，需现场复核')
        if status=='budget_fallback':
            unmet.append('深度搜索达到时间预算，当前采用已校验的快速结果，可能尚有更优布局')
        missing=sum(solution.unloaded_counts.values())
        if missing:
            unmet.append(f'订单仍有 {missing} 件未装入')
        reserve=request.container.inner_length_mm-max((p.x_mm+p.length_mm for p in solution.placements),default=0)
        if reserve<request.door_buffer_mm:
            unmet.append(f'实际柜门预留 {reserve}mm，小于目标 {request.door_buffer_mm}mm，请现场复核操作空间')
        solution.assessment=SolutionAssessment(status=status,goal=GOALS[solution.profile],diagnostics=d,
            hard_constraints=['边界/碰撞/允许朝向','货物原尺寸及重量不变','完整支撑/叠放层数/承重','间隙/柜门通行尺寸/总载重/必装'],
            unmet_soft_goals=unmet,limits=LIMITS)
    if high is None:
        return
    for solution in solutions:
        a=solution.assessment
        baseline=high.assessment.diagnostics
        for field in ['loaded_pieces','loading_steps','cargo_zones','length_imbalance_pct','width_imbalance_pct']:
            a.deltas[field]=round(getattr(solution.metrics,field)-getattr(high.metrics,field),4)
        for field in ['upper_max_void_m2','floor_void_m2','estimated_handling_distance_m','sku_switches']:
            current,old=getattr(a.diagnostics,field),getattr(baseline,field)
            a.deltas[field]=None if current is None or old is None else round(current-old,6)
        if solution.profile!='high_fill':
            drop=high.metrics.loaded_pieces-solution.metrics.loaded_pieces
            a.tradeoffs.append(f'相对高装载率：少装 {drop} 件' if drop>0 else '相对高装载率：保持装入件数')
            a.tradeoffs.append(f"装载步骤变化 {a.deltas['loading_steps']:+g} 步，预计搬运距离变化 {a.deltas['estimated_handling_distance_m']:+g} m")
            if solution.profile=='easy' and a.deltas['loading_steps']>=0 and a.deltas['estimated_handling_distance_m']>=0:
                a.unmet_soft_goals.append('当前件数和约束下，步骤及估算搬运距离未优于高装载率方案')
            if a.deltas['upper_max_void_m2'] is not None and a.deltas['upper_max_void_m2']>0:
                a.tradeoffs.append(f"上层最大单层空白增加 {a.deltas['upper_max_void_m2']:.3f} m²")
