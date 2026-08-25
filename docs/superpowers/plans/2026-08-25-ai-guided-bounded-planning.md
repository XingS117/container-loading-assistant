# AI 引导的受限装柜规划实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让 AI 参与候选生成而非仅排序，并让复杂订单在有限搜索预算内返回安全方案。

**架构：** `ai_strategy.py` 解析带行组的紧凑策略；`packing.py` 使用该策略生成并校验优先候选，同时为通用搜索设置候选上限；`main.py` 返回可感知的采纳结果。所有坐标仍由本地算法产生，所有结果仍由独立校验器验证。

**技术栈：** Python 3.12、FastAPI、Pydantic、rectpack、pytest、React、TypeScript、Vitest。

---

### 任务 1：扩展 AI 策略契约

**文件：**
- 修改：`backend/app/ai_strategy.py`
- 修改：`backend/tests/test_ai_strategy.py`

- [ ] **步骤 1：编写失败的策略行组解析测试**

```python
def test_parses_legal_ai_row_groups(monkeypatch):
    hint = load_ai_layout_hint(ai_request(), api_key="test-key")
    assert hint.row_groups == (("cargo-a", "cargo-b"),)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv\\Scripts\\python.exe -m pytest backend\\tests\\test_ai_strategy.py::test_parses_legal_ai_row_groups -q`

预期：失败，`LayoutHint` 尚无 `row_groups`。

- [ ] **步骤 3：实现最少解析与紧凑提示字段**

```python
@dataclass(frozen=True)
class LayoutHint:
    sku_order: tuple[str, ...] = ()
    orientations: dict[str, str] | None = None
    row_groups: tuple[tuple[str, ...], ...] = ()
```

只接受 1-2 个不重复、存在于请求中的 SKU；在系统提示和请求 JSON 中要求 `row_groups`。

- [ ] **步骤 4：运行策略测试验证通过**

运行：`.venv\\Scripts\\python.exe -m pytest backend\\tests\\test_ai_strategy.py -q --basetemp .pytest-tmp-ai-contract`

预期：通过。

### 任务 2：AI 引导的确定性候选

**文件：**
- 修改：`backend/app/packing.py`
- 修改：`backend/tests/test_packing.py`

- [ ] **步骤 1：编写失败的引导候选测试**

```python
def test_ai_guided_floor_layout_uses_requested_order_and_orientation():
    solution = pack_order(request_with_ai_hint())
    assert first_floor_skus(solution)[:2] == ["cargo-b", "cargo-a"]
    assert all(item.rotation == Orientation.WLH for item in cargo_a_placements(solution))
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv\\Scripts\\python.exe -m pytest backend\\tests\\test_packing.py::test_ai_guided_floor_layout_uses_requested_order_and_orientation -q`

预期：失败，当前建栈阶段忽略 AI 朝向，候选生成不以 AI 顺序为优先。

- [ ] **步骤 3：实现首选合法朝向和引导行组候选**

```python
def _hinted_orientation(request: PackRequest, cargo: CargoSpec) -> Orientation | None:
    value = (request.ai_layout_hint or {}).get("orientations", {}).get(cargo.id)
    return Orientation(value) if value in {item.value for item in cargo.allowed_orientations} else None
```

将合法首选朝向传入建栈和行布局；先生成 AI 引导候选并通过 `_validated_candidate()`，失败才回退既有布局链。

- [ ] **步骤 4：运行引导候选测试验证通过**

运行：`.venv\\Scripts\\python.exe -m pytest backend\\tests\\test_packing.py::test_ai_guided_floor_layout_uses_requested_order_and_orientation -q`

预期：通过。

### 任务 3：给通用搜索增加候选预算和安全回退

**文件：**
- 修改：`backend/app/packing.py`
- 修改：`backend/tests/test_packing.py`

- [ ] **步骤 1：编写失败的候选预算测试**

```python
def test_generic_floor_search_returns_best_safe_candidate_when_budget_is_reached():
    layout = _generic_floor_band_layout(request, units, quantities, capacities, "fill", candidate_limit=1)
    assert validate_solution(request.container, request.cargo_items, _expand_stacks(request, layout, "high_fill")).valid
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv\\Scripts\\python.exe -m pytest backend\\tests\\test_packing.py::test_generic_floor_search_returns_best_safe_candidate_when_budget_is_reached -q`

预期：失败，函数尚无 `candidate_limit` 参数。

- [ ] **步骤 3：实现候选上限和当前最佳候选返回**

```python
if examined_candidates >= candidate_limit:
    return max(candidates, key=lambda candidate: candidate[0])[1] if candidates else None
```

对 AI 有效请求使用较小上限，对本地回退使用固定上限；每个候选仍经 `_validated_candidate()` 和 `validate_solution()`。

- [ ] **步骤 4：运行预算测试验证通过**

运行：`.venv\\Scripts\\python.exe -m pytest backend\\tests\\test_packing.py::test_generic_floor_search_returns_best_safe_candidate_when_budget_is_reached -q`

预期：通过。

### 任务 4：暴露策略采纳状态

**文件：**
- 修改：`backend/app/models.py`
- 修改：`backend/app/main.py`
- 修改：`backend/tests/test_api.py`
- 修改：`frontend/src/types.ts`
- 修改：`frontend/src/components/SolutionWorkspace.tsx`
- 修改：`frontend/src/components/SolutionWorkspace.test.tsx`

- [ ] **步骤 1：编写失败的 API/前端采纳状态测试**

```python
assert response.json()["ai_strategy"]["applied"] is True
assert response.json()["ai_strategy"]["row_groups"] == [["a", "b"]]
```

```tsx
expect(screen.getByLabelText("AI 策略状态")).toHaveTextContent("已采纳 2 个行组建议")
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv\\Scripts\\python.exe -m pytest backend\\tests\\test_api.py -q --basetemp .pytest-tmp-ai-status`

运行：`npm.cmd test -- --run frontend/src/components/SolutionWorkspace.test.tsx`

预期：失败，响应和界面没有 `applied` / `row_groups`。

- [ ] **步骤 3：实现状态字段和前端展示**

```python
class AIStrategyStatus(BaseModel):
    applied: bool = False
    row_groups: list[list[str]] = Field(default_factory=list)
```

仅在引导候选通过本地校验时设置 `applied=True`；前端继续显示已有安全提示，并增加一行非阻塞的采纳摘要。

- [ ] **步骤 4：运行 API 与前端状态测试验证通过**

运行：`.venv\\Scripts\\python.exe -m pytest backend\\tests\\test_api.py -q --basetemp .pytest-tmp-ai-status`

运行：`npm.cmd test -- --run frontend/src/components/SolutionWorkspace.test.tsx`

预期：通过。

### 任务 5：全量回归、构建和发布

**文件：**
- 修改：`docs/HANDOFF.md`

- [ ] **步骤 1：运行后端全量回归**

运行：`.venv\\Scripts\\python.exe -m pytest backend\\tests -q --basetemp .pytest-tmp-ai-guided-full`

预期：所有后端测试通过。

- [ ] **步骤 2：运行前端全量测试和生产构建**

运行：`npm.cmd test -- --run`

运行：`npm.cmd run build`

预期：测试和构建均通过。

- [ ] **步骤 3：更新交接、提交、推送和部署**

```powershell
git add backend frontend docs/HANDOFF.md docs/superpowers/specs/2026-08-25-ai-guided-bounded-planning-design.md docs/superpowers/plans/2026-08-25-ai-guided-bounded-planning.md
git commit -m "feat(AI): 引导受限候选生成"
git -c http.version=HTTP/1.1 push origin main
```

发布包包含 `backend/app` 与 `frontend/dist`，服务重启后验证 `/health`、AI 不可用回退和 AI 已采纳状态。
