# AI 目标感知的紧凑布局实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法跟踪进度。

**目标：** 修复 AI 策略误解析，并让装载率优先、重心稳妥、易操作三种方案按不同目标生成更紧凑、可解释的布局。

**架构：** AI 只返回通用策略和 profile 策略，本地算法根据每个 profile 生成多个候选；所有坐标、数量边界和物理安全结论仍由本地算法及 `validate_solution()` 决定。`high_fill` 最大化件数，`stable` 保持 high_fill 的货物集合并配重，`easy` 在有限范围内允许删除非必装货物以换取连续区域和更少装卸步骤。

**技术栈：** Python 3.12、FastAPI、Pydantic、rectpack、pytest、React、TypeScript、Vitest。

---

## 文件职责

- 修改：`backend/app/ai_strategy.py`，负责 AI 策略 JSON 契约、兼容解析和无 `response_format` 重试。
- 修改：`backend/app/models.py`，仅在需要向前端展示紧凑度或易操作少装原因时增加响应字段。
- 修改：`backend/app/packing.py`，负责 profile 策略读取、紧凑度计算、候选评分和易操作少装候选。
- 修改：`backend/app/main.py`，负责传递 profile-aware AI 状态和诊断错误，不改变安全错误码。
- 修改：`backend/tests/test_ai_strategy.py`，覆盖不同 AI 返回形态和提供商兼容性。
- 修改：`backend/tests/test_packing.py`，覆盖 AI 引导、紧凑度评分和候选安全性。
- 修改：`backend/tests/test_large_order.py`，覆盖复杂订单服务预算。
- 修改：`backend/tests/test_api.py`，覆盖三方案 AI 状态和易操作少装披露。
- 修改：`frontend/src/types.ts`，同步响应字段。
- 修改：`frontend/src/components/SolutionWorkspace.tsx`，显示不同方案的实际策略、紧凑度和少装说明。
- 修改：`frontend/src/components/SolutionWorkspace.test.tsx`，验证状态和披露文案。
- 修改：`docs/HANDOFF.md`，记录新契约、评分边界和部署验证。

### 任务 1：扩展 AI 策略类型并修复解析

**文件：** `backend/app/ai_strategy.py`、`backend/tests/test_ai_strategy.py`

- [ ] **步骤 1：编写失败测试，覆盖结构化内容和 profile 策略。**

```python
def test_parses_structured_and_profile_ai_hint(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{
                    "message": {
                        "content": [{
                            "type": "text",
                            "text": '{"sku_order":["cargo-b","cargo-a"],'
                            '"profiles":{"easy":{"zone_order":["cargo-a","cargo-b"],"max_zones":2}}}',
                        }],
                    }
                }]
            }

    monkeypatch.setattr(httpx, "post", lambda *_args, **_kwargs: FakeResponse())

    hint = load_ai_layout_hint(ai_request(), api_key="test-key")

    assert hint is not None
    assert hint.sku_order == ("cargo-b", "cargo-a")
    assert hint.profiles["easy"].zone_order == ("cargo-a", "cargo-b")
    assert hint.profiles["easy"].max_zones == 2
```

- [ ] **步骤 2：运行测试确认当前解析器失败。**

运行：`.\.venv\Scripts\python.exe -m pytest backend\tests\test_ai_strategy.py::test_parses_structured_and_profile_ai_hint -q --basetemp .\_temp\pytest-ai-profile-red`

预期：FAIL，当前 `LayoutHint` 没有 `profiles`，且 `_content_json()` 不接受结构化内容片段。

- [ ] **步骤 3：增加最小类型和安全解析实现。**

实现 `ProfileHint`，字段固定为 `sku_order`、`orientations`、`row_groups`、`zone_order`、`max_zones`；扩展 `LayoutHint` 增加 `profiles`。新增 `_content_candidates()`，依次处理字典、结构化文本片段、代码块和文本中的最外层 JSON；新增 `_message_payload()`，读取 `content`、`parsed`、工具调用参数和 `reasoning_content`。所有字段仍使用货物 ID、允许朝向和 profile 白名单过滤。

- [ ] **步骤 4：运行 AI 策略全量测试确认通过。**

运行：`.\.venv\Scripts\python.exe -m pytest backend\tests\test_ai_strategy.py -q --basetemp .\_temp\pytest-ai-profile-green`

预期：现有测试与新增解析测试全部 PASS。

- [ ] **步骤 5：提交 AI 契约变更。**

```powershell
git add backend/app/ai_strategy.py backend/tests/test_ai_strategy.py
git commit -m "fix(AI): 兼容结构化策略返回"
```

### 任务 2：兼容不支持 JSON response format 的提供商

**文件：** `backend/app/ai_strategy.py`、`backend/tests/test_ai_strategy.py`

- [ ] **步骤 1：编写失败测试，模拟 HTTP 400 后无格式参数重试。**

```python
def test_retries_without_response_format_when_provider_rejects_it(monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, status):
            self.status_code = status

        def raise_for_status(self):
            if self.status_code == 400:
                request = httpx.Request("POST", "https://api.deepseek.com/v1/chat/completions")
                response = httpx.Response(400, request=request)
                raise httpx.HTTPStatusError("unsupported response format", request=request, response=response)

        def json(self):
            return {"choices": [{"message": {"content": '{"sku_order":["cargo-a","cargo-b"]}'}}]}

    def fake_post(_url, **kwargs):
        calls.append(kwargs["json"])
        return FakeResponse(400 if len(calls) == 1 else 200)

    monkeypatch.setattr(httpx, "post", fake_post)

    hint = load_ai_layout_hint(ai_request(), api_key="test-key")

    assert hint is not None
    assert len(calls) == 2
    assert "response_format" in calls[0]
    assert "response_format" not in calls[1]
```

- [ ] **步骤 2：运行测试确认失败。**

运行：`.\.venv\Scripts\python.exe -m pytest backend\tests\test_ai_strategy.py::test_retries_without_response_format_when_provider_rejects_it -q --basetemp .\_temp\pytest-ai-format-red`

预期：FAIL，当前 HTTP 400 直接返回 `http_error`。

- [ ] **步骤 3：实现一次性兼容重试并保留错误分类。**

让 `_post_chat_completion()` 在首次请求带 `response_format` 且服务返回 HTTP 400 时，以相同 URL、消息、模型和 token 预算重试一次但移除 `response_format`；其他 HTTP 状态不重试。日志记录 provider、model、status 和耗时，不记录密钥或完整响应。

- [ ] **步骤 4：运行测试确认通过。**

运行：`.\.venv\Scripts\python.exe -m pytest backend\tests\test_ai_strategy.py -q --basetemp .\_temp\pytest-ai-format-green`

预期：全部 PASS。

### 任务 3：让 AI 策略按三种方案进入候选生成

**文件：** `backend/app/packing.py`、`backend/app/main.py`、`backend/tests/test_packing.py`、`backend/tests/test_api.py`

- [ ] **步骤 1：编写失败测试，验证 profile 策略影响不同方案但不绕过物理校验。**

```python
def test_profile_ai_hints_are_used_without_reducing_required_high_fill_count():
    request = PackRequest(
        container=small_container(),
        cargo_items=[
            boxes(id="cargo-a", quantity=2, length_mm=600, width_mm=400, height_mm=400,
                  allowed_orientations=["LWH", "WLH"], stackable=True, max_layers=2,
                  max_top_load_g=500_000),
            boxes(id="cargo-b", quantity=2, length_mm=500, width_mm=500, height_mm=400,
                  allowed_orientations=["LWH", "WLH"], stackable=True, max_layers=2,
                  max_top_load_g=500_000),
        ],
        ai_layout_hint={
            "sku_order": ["cargo-b", "cargo-a"],
            "orientations": {"cargo-a": "WLH", "cargo-b": "LWH"},
            "row_groups": [["cargo-a", "cargo-b"]],
            "profiles": {
                "high_fill": {"sku_order": ["cargo-b", "cargo-a"]},
                "stable": {"sku_order": ["cargo-a", "cargo-b"]},
                "easy": {"zone_order": ["cargo-a", "cargo-b"], "max_zones": 2},
            },
        },
    )
    response = pack_order(request)

    assert response.solutions[0].metrics.loaded_pieces >= response.solutions[2].metrics.loaded_pieces
    assert response.solutions[0].metrics.loaded_pieces == response.solutions[1].metrics.loaded_pieces
    assert all(
        validate_solution(request.container, request.cargo_items, solution.placements).valid
        for solution in response.solutions
    )
    assert response.solutions[2].metrics.loading_steps <= response.solutions[0].metrics.loading_steps
```

- [ ] **步骤 2：运行测试确认当前实现没有 profile-aware 候选。**

运行：`.\.venv\Scripts\python.exe -m pytest backend\tests\test_packing.py::test_profile_ai_hints_are_used_without_reducing_required_high_fill_count -q --basetemp .\_temp\pytest-profile-red`

预期：FAIL，当前所有候选读取同一组全局顺序，易操作没有独立的 AI 区域策略。

- [ ] **步骤 3：实现 profile hint 读取和候选隔离。**

新增 `_profile_hint(request, profile)` 和 `_profile_sku_order(request, profile)`；保留旧字段作为 profile 缺省值。`_build_stack_units()`、`_sku_block_layout()`、floor-band 生成器和 `_pack_units()` 在已知 profile 时读取对应 hint，但仍只接受合法朝向和已存在 ID。`pack_order()` 为三个方案分别保留本地基线和 AI 引导候选，stable 使用 high_fill 的完整货物计数，easy 使用自己的数量边界。

- [ ] **步骤 4：更新 API 状态，使状态反映实际采用的 profile 策略。**

在 `main.py` 计算每个方案的实际顺序、朝向和行组命中情况，保持 `AIStrategyStatus` 兼容，同时将“已采纳”定义为至少一个 profile 候选通过物理校验并被该方案选择。

- [ ] **步骤 5：运行 packing/API 测试确认通过。**

运行：`.\.venv\Scripts\python.exe -m pytest backend\tests\test_packing.py backend\tests\test_api.py -q --basetemp .\_temp\pytest-profile-green`

预期：现有测试与新增 profile 候选测试全部 PASS。

### 任务 4：增加紧凑度指标和填洞评分

**文件：** `backend/app/packing.py`、`backend/app/models.py`、`backend/tests/test_packing.py`

- [ ] **步骤 1：编写失败测试，要求同件数候选优先减少内部空洞。**

```python
def test_compact_score_prefers_fewer_internal_floor_gaps():
    request = PackRequest(container=small_container(), cargo_items=[boxes(quantity=3)])

    def floor_piece(instance_index, x_mm):
        return Placement(
            id=f"piece-{instance_index}", cargo_id="box-a", instance_index=instance_index,
            x_mm=x_mm, y_mm=0, z_mm=0, length_mm=200, width_mm=200, height_mm=200,
            rotation=Orientation.LWH, weight_g=100_000, step=1,
        )

    compact = [floor_piece(0, 0), floor_piece(1, 400), floor_piece(2, 800)]
    gapped = [floor_piece(0, 0), floor_piece(1, 300), floor_piece(2, 800)]

    assert _layout_quality_score(request, compact, "high_fill") > _layout_quality_score(
        request, gapped, "high_fill"
    )
```

- [ ] **步骤 2：运行测试确认当前评分不能区分该差异。**

运行：`.\.venv\Scripts\python.exe -m pytest backend\tests\test_packing.py::test_compact_score_prefers_fewer_internal_floor_gaps -q --basetemp .\_temp\pytest-compact-red`

预期：FAIL，当前质量元组只包含 X 轴覆盖率和区域数，没有内部最大空隙/空洞指标。

- [ ] **步骤 3：实现线性紧凑度指标。**

在 `LayoutQuality` 增加 `floor_internal_gap_mm`、`floor_largest_gap_mm` 和 `floor_bbox_void_pct`。指标基于底层矩形 X 区间与底层包围盒计算；对大订单使用已存在的近似路径，避免重新引入 O(n²) 最坏搜索。将指标加入 `_layout_quality_score()`：件数仍是第一关键字，随后按 profile 分别加入连续覆盖、空洞、最大空隙、重心和区域指标。

- [ ] **步骤 4：实现安全紧凑化候选。**

新增 `_compact_floor_candidate()`，只在同一 `z_mm`、同一支撑关系和不改变朝向的前提下沿 X/Y 方向贴合可移动栈；移动后调用 `validate_solution()`，失败则丢弃该变换。候选生成器同时保留未紧凑化版本，防止紧凑化损害卸货顺序或支撑。

- [ ] **步骤 5：运行紧凑度及既有物理测试。**

运行：`.\.venv\Scripts\python.exe -m pytest backend\tests\test_packing.py backend\tests\test_floor_first_layout.py backend\tests\test_common_layouts.py -q --basetemp .\_temp\pytest-compact-green`

预期：全部 PASS，且新测试证明同件数时连续候选优先。

### 任务 5：实现易操作方案的有界少装策略

**文件：** `backend/app/packing.py`、`backend/app/models.py`、`backend/tests/test_packing.py`、`backend/tests/test_api.py`

- [ ] **步骤 1：编写失败测试，验证 easy 只删除非必装货物并披露改善。**

```python
def test_easy_may_drop_optional_cargo_for_compact_regions():
    request = PackRequest(
        container=ContainerSpec(
            id="easy-compaction", name="易操作测试柜", inner_length_mm=3000,
            inner_width_mm=1000, inner_height_mm=1000, door_width_mm=1000,
            door_height_mm=1000, max_payload_g=1_000_000, clearance_mm=0,
        ),
        cargo_items=[
            boxes(id="must-load", quantity=2, length_mm=1000, width_mm=500,
                  height_mm=500, must_load=True),
            boxes(id="optional-a", quantity=2, length_mm=600, width_mm=500,
                  height_mm=500),
            boxes(id="optional-b", quantity=2, length_mm=600, width_mm=500,
                  height_mm=500),
        ],
    )
    response = pack_order(request)
    high_fill, easy = response.solutions[0], response.solutions[2]

    assert easy.metrics.loaded_pieces <= high_fill.metrics.loaded_pieces
    assert high_fill.metrics.loaded_pieces - easy.metrics.loaded_pieces <= max(1, round(high_fill.metrics.loaded_pieces * 0.05))
    assert easy.loaded_counts["must-load"] == request.cargo_items[0].quantity
    assert any("易操作方案少装" in warning for warning in easy.warnings)
    assert validate_solution(request.container, request.cargo_items, easy.placements).valid
```

- [ ] **步骤 2：运行测试确认当前 easy 没有有界的目标比较。**

运行：`.\.venv\Scripts\python.exe -m pytest backend\tests\test_packing.py::test_easy_may_drop_optional_cargo_for_compact_regions -q --basetemp .\_temp\pytest-easy-drop-red`

预期：FAIL，当前 `_easy_region_layout()` 首次找到候选即返回，未比较件数、连续性和少装上限。

- [ ] **步骤 3：实现有界 easy 候选选择。**

新增 `EASY_MAX_DROP_RATIO = 0.05` 和 `_easy_drop_limit()`；修改 `_easy_region_layout()` 收集保持件数、逐 SKU 删除可选单位和整体少装候选，最多比较少装上限内的候选。候选选择顺序为：物理有效、少装不超过上限、货区/步骤和紧凑度明显改善、最后才是装入件数。必装货物通过 `_required_satisfied()` 强制保留。

- [ ] **步骤 4：增加结果披露字段和前端展示。**

在 `PackingSolution` 或 `warnings` 中记录 high_fill 与 easy 的件数差、少装 SKU 和紧凑度改善原因。前端在易操作方案卡片显示“少装 N 件换取连续分区”，没有少装时不显示误导性文案。

- [ ] **步骤 5：运行 easy/API 测试确认通过。**

运行：`.\.venv\Scripts\python.exe -m pytest backend\tests\test_packing.py backend\tests\test_api.py -q --basetemp .\_temp\pytest-easy-drop-green`

预期：全部 PASS。

### 任务 6：完善提示词、前端状态和文档

**文件：** `backend/app/ai_strategy.py`、`frontend/src/types.ts`、`frontend/src/components/SolutionWorkspace.tsx`、`frontend/src/components/SolutionWorkspace.test.tsx`、`docs/HANDOFF.md`

- [ ] **步骤 1：编写失败的前端状态测试。**

```tsx
it("shows profile-specific AI adoption and easy-layout disclosure", () => {
  const responseWithProfileHints = {
    ...makeResponse(8, 3),
    ai_strategy: {
      status: "considered",
      applied: true,
      provider: "DeepSeek",
      model: "deepseek-v4-flash",
      message: "三种方案分别优化",
      sku_order: ["a"],
      orientations: {},
      row_groups: [],
      profiles: { easy: { zone_order: ["a"], max_zones: 2 } },
    },
  } as PackResponse;
  responseWithProfileHints.solutions[2].warnings = ["易操作方案少装 2 件换取连续分区"];
  render(
    <SolutionWorkspace
      response={responseWithProfileHints}
      container={container}
      presets={presets}
      cargoItems={cargoItems}
      onBack={() => undefined}
      onRecalculate={async () => undefined}
      recalculating={false}
    />,
  );
  expect(screen.getByLabelText("AI 策略状态")).toHaveTextContent("三种方案分别优化");
  expect(screen.getByText(/少装 2 件换取连续分区/)).toBeInTheDocument();
});
```

- [ ] **步骤 2：运行测试确认当前界面缺少 profile 说明。**

运行：`$env:CI="true"; pnpm test -- --run frontend/src/components/SolutionWorkspace.test.tsx`

预期：FAIL，当前只显示全局 AI 状态和行组数量。

- [ ] **步骤 3：更新 AI 提示和前端展示。**

提示词明确要求分别为 high_fill、stable、easy 返回策略，禁止坐标；前端按当前方案展示采用状态、紧凑度和易操作少装披露，不显示原始 JSON 或 API Key。

- [ ] **步骤 4：运行前端测试确认通过。**

运行：`$env:CI="true"; pnpm test -- --run frontend/src/components/SolutionWorkspace.test.tsx`

预期：全部 PASS。

- [ ] **步骤 5：更新交接文档。**

在 `docs/HANDOFF.md` 记录三目标评分、AI 解析兼容范围、易操作少装上限和线上验证步骤，明确 AI 仍不是坐标生成器。

### 任务 7：全量回归、构建、提交和部署

**文件：** 本计划列出的实现文件与测试文件。

- [ ] **步骤 1：运行后端全量测试。**

运行：`.\.venv\Scripts\python.exe -m pytest backend\tests -q --basetemp .\_temp\pytest-ai-goal-aware-full`

预期：所有后端测试 PASS，复杂订单不再出现服务预算超时。

- [ ] **步骤 2：运行前端全量测试和生产构建。**

运行：`$env:CI="true"; pnpm test -- --run`

运行：`$env:CI="true"; pnpm build`

预期：前端测试和构建均成功；只允许保留已知的大 chunk warning。

- [ ] **步骤 3：检查差异和未跟踪文件。**

运行：`git diff --check; git status --short`

预期：差异只包含本计划文件；既有 `reasonix.toml` 和 PDF 不加入提交。

- [ ] **步骤 4：提交并推送。**

```powershell
git add backend frontend docs/HANDOFF.md docs/superpowers/plans/2026-08-26-ai-goal-aware-compact-layout.md
git commit -m "feat(AI): 按目标生成紧凑装柜方案"
git push origin main
```

- [ ] **步骤 5：发布并验证线上三种方案。**

打包 `backend/app` 和 `frontend/dist`，上传服务器后重启 `packing-assistant`。验证 `/health`、首页主 JS、AI 配置请求、AI 结构化返回兼容回退，并提交截图类似多 SKU 订单，确认：high_fill/stable 件数不低于原基线，easy 少装不超过 5% 且有披露，三套布局全部通过物理校验，内部大空洞和货区/步骤按目标改善。
