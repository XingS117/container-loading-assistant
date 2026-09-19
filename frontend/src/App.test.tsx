import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import App from "./App";
import * as excel from './lib/excel';
import { createCargo } from './lib/cargo';
import * as analytics from './lib/analytics';
import { loadSavedOrders, saveOrder } from './lib/orderHistory';

beforeEach(() => { localStorage.clear(); sessionStorage.clear(); vi.restoreAllMocks(); });


const preset = {
  id: "20gp",
  name: "20GP",
  inner_length_mm: 5898,
  inner_width_mm: 2352,
  inner_height_mm: 2393,
  door_width_mm: 2340,
  door_height_mm: 2280,
  max_payload_g: 28200000,
  clearance_mm: 0,
};

const secondPreset = {
  id: "40hq",
  name: "40HQ",
  inner_length_mm: 12032,
  inner_width_mm: 2352,
  inner_height_mm: 2698,
  door_width_mm: 2340,
  door_height_mm: 2585,
  max_payload_g: 28600000,
  clearance_mm: 0,
};

const response = {
  request_id: "abc123",
  solutions: ["high_fill", "stable", "easy"].map((profile, index) => ({
    profile,
    name: ["装载率优先", "重心稳妥", "易操作"][index],
    placements: [],
    loaded_counts: { cargo_1: 0 },
    unloaded_counts: { cargo_1: 10 },
    metrics: {
      loaded_pieces: 0,
      loaded_weight_g: 0,
      volume_utilization_pct: 0,
      weight_utilization_pct: 0,
      center_of_gravity: { x_mm: 0, y_mm: 0, z_mm: 0 },
      length_imbalance_pct: 0,
      width_imbalance_pct: 0,
      weight_imbalance_pct: 0,
      loading_steps: 0,
      cargo_zones: 0,
    },
    zones: [],
    pros: ["测试优点"],
    cons: ["测试缺点"],
    warnings: [],
    identical_to: index ? "high_fill" : null,
  })),
};

test('automatically saves calculation and restores the selected profile after remount without recalculating', async () => {
  const fetch = vi.spyOn(globalThis,'fetch')
    .mockResolvedValueOnce(new Response(JSON.stringify([preset])))
    .mockResolvedValueOnce(new Response(JSON.stringify(response)))
    .mockResolvedValueOnce(new Response(JSON.stringify([preset])));
  const app = render(<App />);
  await screen.findByRole('button',{name:/20GP/});
  await userEvent.click(screen.getByRole('button',{name:'生成装柜方案'}));
  await screen.findByText('方案比较');
  expect(loadSavedOrders()).toHaveLength(1);
  await userEvent.click(screen.getByRole('button',{name:/易操作/}));
  expect(loadSavedOrders()[0].workspace?.selectedProfile).toBe('easy');
  app.unmount(); render(<App />);
  expect(await screen.findByText('方案比较')).toBeInTheDocument();
  expect(screen.getByRole('button',{name:/易操作/})).toHaveClass('is-active');
  expect(fetch.mock.calls.filter(([url]) => url === '/api/v1/pack')).toHaveLength(1);
});

test('saved layout history can be copied as new input without reusing old results', async () => {
  const cargo = createCargo('COPY');
  saveOrder({name:'可复制订单',container:preset,cargoItems:[cargo],itemGapCm:2,clearanceCm:1,response:response as never,workspace:{selectedProfile:'easy',solutionOverrides:{},lockedCargoIds:[]},preferredProfile:'stable'});
  vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response(JSON.stringify([preset])));
  render(<App />);
  await screen.findByRole('button',{name:/20GP/});
  await userEvent.click(screen.getByText(/本机历史/));
  await userEvent.click(screen.getByRole('button',{name:'复制为新订单'}));
  expect(screen.getByLabelText('货物代号或名称 1')).toHaveValue('COPY');
  expect(screen.queryByText('方案比较')).not.toBeInTheDocument();
  expect(screen.getByLabelText('本次优先目标')).toHaveValue('stable');
  expect(loadSavedOrders()).toHaveLength(1);
});

test('deleting a saved version requires confirmation and never clears the visible result', async () => {
  vi.spyOn(globalThis,'fetch').mockResolvedValueOnce(new Response(JSON.stringify([preset]))).mockResolvedValueOnce(new Response(JSON.stringify(response)));
  const confirm = vi.spyOn(window,'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true);
  render(<App />);
  await screen.findByRole('button',{name:/20GP/});
  await userEvent.click(screen.getByRole('button',{name:'生成装柜方案'}));
  await screen.findByText('方案比较');
  await userEvent.click(screen.getByText(/本机历史/));
  await userEvent.click(screen.getByRole('button',{name:'删除版本'}));
  expect(loadSavedOrders()).toHaveLength(1);
  await userEvent.click(screen.getByRole('button',{name:'删除版本'}));
  expect(confirm).toHaveBeenCalledTimes(2);
  expect(loadSavedOrders()).toHaveLength(0);
  expect(screen.getByText('方案比较')).toBeInTheDocument();
  expect(JSON.parse(localStorage.getItem('container-loading-assistant-draft-v1')!).activeSavedId).toBeNull();
});

test('storage failures keep calculated results visible and show an explicit unsaved warning', async () => {
  vi.spyOn(globalThis,'fetch').mockResolvedValueOnce(new Response(JSON.stringify([preset]))).mockResolvedValueOnce(new Response(JSON.stringify(response)));
  const write = Storage.prototype.setItem;
  vi.spyOn(Storage.prototype,'setItem').mockImplementation(function(this: Storage, key,value) {
    if (key === 'container-loading-assistant-orders-v1') throw new DOMException('full','QuotaExceededError');
    write.call(this,key,value);
  });
  render(<App />);
  await screen.findByRole('button',{name:/20GP/});
  await userEvent.click(screen.getByRole('button',{name:'生成装柜方案'}));
  expect(await screen.findByText('方案比较')).toBeInTheDocument();
  expect(screen.getByText(/本机空间不足.*未保存/)).toBeInTheDocument();
});

test('applied removal and locks survive reload, with original layout recoverable as a new version', async () => {
  const cargo = {...createCargo('HISTORY'),id:'history',quantity:2};
  localStorage.setItem('container-loading-assistant-draft-v1',JSON.stringify({containerId:preset.id,container:preset,cargoItems:[cargo],itemGapCm:0,clearanceCm:0}));
  const packed = {...response,solutions:response.solutions.map(s => ({...s,placements:[0,1].map(i => ({id:`history-${i}`,cargo_id:'history',instance_index:i,x_mm:i*600,y_mm:0,z_mm:0,length_mm:600,width_mm:400,height_mm:400,rotation:'LWH',weight_g:18000,step:1})),loaded_counts:{history:2},unloaded_counts:{history:0},metrics:{...s.metrics,loaded_pieces:2}}))};
  vi.spyOn(globalThis,'fetch').mockImplementation(async (url,init) => {
    if (url === '/api/v1/layout/review') {
      const {placements} = JSON.parse(init?.body as string);
      return new Response(JSON.stringify({valid:true,errors:[],placements,zones:[],metrics:{...packed.solutions[0].metrics,loaded_pieces:placements.length}}));
    }
    return new Response(JSON.stringify(url === '/api/v1/pack' ? packed : [preset]));
  });
  const app = render(<App />);
  await screen.findByRole('button',{name:/20GP/});
  await userEvent.click(screen.getByRole('button',{name:'生成装柜方案'}));
  await userEvent.click(await screen.findByRole('button',{name:'编辑布局'}));
  await userEvent.click(screen.getByRole('button',{name:'HISTORY · 第 1 件'}));
  await userEvent.click(screen.getByRole('button',{name:'移出选中单件'}));
  await waitFor(() => expect(screen.queryByRole('button',{name:'HISTORY · 第 1 件'})).not.toBeInTheDocument());
  await userEvent.click(screen.getByRole('button',{name:'HISTORY · 第 2 件'}));
  await userEvent.click(screen.getByRole('button',{name:'锁定该 SKU'}));
  await waitFor(() => expect(screen.getByRole('button',{name:'应用调整'})).toBeEnabled());
  await userEvent.click(screen.getByRole('button',{name:'应用调整'}));
  expect(loadSavedOrders()).toHaveLength(2);
  const saved = loadSavedOrders()[0];
  expect(saved.workspace?.lockedCargoIds).toEqual(['history']);
  expect(saved.workspace?.solutionOverrides.high_fill?.unloaded_counts).toEqual({history:1});
  app.unmount(); render(<App />);
  expect(await screen.findByText('当前方案已人工调整')).toBeInTheDocument();
  expect(loadSavedOrders()).toHaveLength(2);
  await userEvent.click(screen.getByRole('button',{name:'编辑布局'}));
  await userEvent.click(screen.getByRole('button',{name:/HISTORY · 第 2 件\s*已锁定/}));
  expect(screen.getByRole('button',{name:'移出选中单件'})).toBeDisabled();
  await userEvent.click(screen.getByRole('button',{name:'返回方案'}));
  await userEvent.click(screen.getByRole('button',{name:'恢复原始布局'}));
  expect(loadSavedOrders()).toHaveLength(3);
  expect(loadSavedOrders()[0].workspace?.solutionOverrides).toEqual({});
  expect(loadSavedOrders()[1].workspace?.solutionOverrides.high_fill?.placements).toHaveLength(1);
});

test('preserves the input on timeout, prevents edits while waiting and allows manual retry', async () => {
  let finish!: (value: Response) => void;
  vi.spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(new Response(JSON.stringify([preset])))
    .mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }))
    .mockResolvedValueOnce(new Response(JSON.stringify(response)));
  render(<App />);
  await screen.findByRole('button', {name:/20GP/});
  await userEvent.click(screen.getByRole('button', {name:'生成装柜方案'}));
  expect(await screen.findByText('等待计算结果')).toBeInTheDocument();
  expect(screen.getByLabelText('货物代号或名称 1')).toBeDisabled();
  finish(new Response('<html>timeout</html>', {status:504}));
  expect(await screen.findByText(/本次计算等待超时/)).toBeInTheDocument();
  expect(screen.getByLabelText('货物代号或名称 1')).toHaveValue('SKU-001');
  await userEvent.click(screen.getByRole('button', {name:'重试计算'}));
  expect(await screen.findByText('方案比较')).toBeInTheDocument();
});

test('retains the original solution after failed recalculation and records its duration and category', async () => {
  vi.spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(new Response(JSON.stringify([preset, secondPreset])))
    .mockResolvedValueOnce(new Response(JSON.stringify(response)))
    .mockRejectedValueOnce(new TypeError('Failed to fetch'));
  const track = vi.spyOn(analytics, 'trackAnalyticsEvent');
  render(<App />);
  await screen.findByRole('button', {name:/20GP/});
  await userEvent.click(screen.getByRole('button', {name:'生成装柜方案'}));
  await screen.findByText('方案比较');
  await userEvent.selectOptions(screen.getByLabelText('重算柜型'), '40hq');
  await userEvent.click(screen.getByRole('button', {name:'确认重算'}));
  expect(await screen.findByText(/网络连接中断/)).toBeInTheDocument();
  expect(screen.getByText('计算结果 · abc123')).toBeInTheDocument();
  expect(track).toHaveBeenCalledWith('pack_calculation_failed', expect.objectContaining({mode:'recalculate', category:'network', reason:'NETWORK_ERROR', elapsed_ms:expect.any(Number)}));
});

test('focuses an invalid cargo field and prevents submission', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify([preset])));
  render(<App />);
  await screen.findByRole('button', {name:/20GP/});
  await userEvent.clear(screen.getByLabelText('长 SKU-001'));
  await userEvent.click(screen.getByRole('button', {name:/第 1 种货物：长.*定位修改/}));
  expect(screen.getByLabelText('长 SKU-001')).toHaveFocus();
  expect(screen.getByRole('button', {name:'生成装柜方案'})).toBeDisabled();
});

test('keeps the current order when Excel has errors and previews a valid replacement', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify([preset]), {status:200}));
  const read = vi.spyOn(excel, 'readCargoExcelReport').mockResolvedValueOnce({rows:[], issues:[{row:4,column:'单重(kg)',message:'必须大于 0'}], conversions:[], rowCount:1})
    .mockResolvedValueOnce({rows:[createCargo('IMPORT')],issues:[],conversions:['长(mm) → 长(cm)（数值 × 0.1）'],rowCount:1});
  render(<App />);
  await screen.findByRole('button', {name:/20GP/});
  const file = new File(['fixture'], 'cargo.xlsx', {type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'});
  await userEvent.upload(screen.getByLabelText('选择 Excel 文件'), file);
  expect(await screen.findByText(/第 4 行.*单重/)).toBeInTheDocument();
  expect(screen.getByLabelText('货物代号或名称 1')).toHaveValue('SKU-001');
  expect(screen.queryByRole('button', {name:'应用导入并替换清单'})).not.toBeInTheDocument();
  await userEvent.upload(screen.getByLabelText('选择 Excel 文件'), file);
  await userEvent.click(await screen.findByRole('button', {name:'应用导入并替换清单'}));
  expect(screen.getByLabelText('货物代号或名称 1')).toHaveValue('IMPORT');
  read.mockRestore();
});


test("loads presets and switches from input to comparable solutions", async () => {
  vi.spyOn(globalThis, "fetch")
    .mockResolvedValueOnce(new Response(JSON.stringify([preset, secondPreset]), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify(response), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify(response), { status: 200 }));

  render(<App />);

  expect(screen.getByRole("heading", { name: "装柜方案助手" })).toBeInTheDocument();
  expect(screen.getByText("选择柜型，录入货物，生成可执行装柜方案。")).toBeInTheDocument();
  expect(screen.getByAltText("一帆风顺，满载启航海运横幅")).toBeInTheDocument();
  await screen.findByRole("button", { name: /20GP/ });

  await userEvent.click(screen.getByRole("button", { name: "生成装柜方案" }));

  await waitFor(() => expect(screen.getByText("方案比较")).toBeInTheDocument());
  expect(screen.getAllByRole("button", { name: /优先|稳妥|易操作/ })).toHaveLength(3);
  expect(screen.getByRole("button", { name: /装载率优先/ })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /重心稳妥/ })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /易操作/ })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /底层优先/ })).not.toBeInTheDocument();
  expect(screen.queryByText("互叠高装载")).not.toBeInTheDocument();

  const containerSelect = screen.getByRole("combobox", { name: "重算柜型" });
  await userEvent.selectOptions(containerSelect, "40hq");
  await userEvent.click(screen.getByRole("button", { name: "确认重算" }));

  await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledTimes(3));
  expect(screen.getByRole("combobox", { name: "重算柜型" })).toHaveValue("40hq");
});


test("selecting pallet kind keeps the selection and applies pallet defaults", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify([preset, secondPreset]), { status: 200 }),
  );

  render(<App />);
  await screen.findByRole("button", { name: /20GP/ });

  const kindSelect = screen.getByRole("combobox", { name: "货物类型 SKU-001" });
  await userEvent.selectOptions(kindSelect, "pallet");

  // 关键回归：选中整托后下拉必须保持整托（此前多次 update 互相覆盖导致跳回散箱）
  expect(screen.getByRole("combobox", { name: "货物类型 SKU-001" })).toHaveValue("pallet");
  // 整托默认可叠两层，特殊规格由用户取消或调整。
  expect(screen.getByRole("checkbox", { name: /可叠/ })).toBeChecked();
  expect(screen.getByLabelText("最大层数 SKU-001")).toHaveValue(2);
  // 整托默认顶部承重 500kg。
  expect(screen.getByLabelText("顶部承重 SKU-001")).toHaveValue(500);
});


test("keeps model API settings in session storage only", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify([preset, secondPreset]), { status: 200 }),
  );

  render(<App />);
  await screen.findByRole("button", { name: /20GP/ });

  await userEvent.click(screen.getByRole("button", { name: "模型配置" }));
  const keyInput = screen.getByLabelText("API Key");
  await userEvent.type(keyInput, "sk-session-test");
  await userEvent.click(screen.getByRole("button", { name: "保存配置" }));

  expect(sessionStorage.getItem("container-loading-assistant-ai-config-v1")).toContain("sk-session-test");
  expect(localStorage.getItem("container-loading-assistant-ai-config-v1")).toBeNull();
});


test("opens model configuration and keeps the selected provider settings in session storage", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify([preset, secondPreset]), { status: 200 }),
  );

  render(<App />);
  await screen.findByRole("button", { name: /20GP/ });

  await userEvent.click(screen.getByRole("button", { name: "模型配置" }));
  expect(screen.getByRole("heading", { name: "模型配置" })).toBeInTheDocument();
  expect(screen.getByLabelText("模型提供商")).toHaveValue("deepseek");

  await userEvent.selectOptions(screen.getByLabelText("模型提供商"), "qwen");
  await userEvent.click(screen.getByRole("button", { name: "保存配置" }));

  expect(JSON.parse(sessionStorage.getItem("container-loading-assistant-ai-config-v1") ?? "{}")).toMatchObject({
    provider: "qwen",
    model: "qwen3-max",
    baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
  });
});


test("lists official model identifiers without marketing labels", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify([preset, secondPreset]), { status: 200 }),
  );

  render(<App />);
  await screen.findByRole("button", { name: /20GP/ });
  await userEvent.click(screen.getByRole("button", { name: "模型配置" }));
  await userEvent.selectOptions(screen.getByLabelText("模型提供商"), "zhipu");

  expect(screen.getByRole("option", { name: "glm-5.2" })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "glm-5.3" })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "glm-5.3-flash" })).toBeInTheDocument();
  expect(screen.queryByText(/旗舰版|快速版|均衡版|深蓝科技/)).not.toBeInTheDocument();
});


test("lists the three configured DeepSeek models", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify([preset, secondPreset]), { status: 200 }),
  );

  render(<App />);
  await screen.findByRole("button", { name: /20GP/ });
  await userEvent.click(screen.getByRole("button", { name: "模型配置" }));
  await userEvent.selectOptions(screen.getByLabelText("模型提供商"), "deepseek");

  expect(screen.getByRole("option", { name: "deepseek-v4-flash" })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "deepseek-v4-pro" })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "deepseek-v4-flash-vision-exp" })).toBeInTheDocument();
  expect(screen.getByLabelText("模型名称")).toHaveValue("deepseek-v4-flash");
});


test("loads a common preset and blocks calculation until weights are filled", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify([preset, secondPreset]), { status: 200 }),
  );
  vi.spyOn(window, "confirm").mockReturnValue(true);

  render(<App />);
  await screen.findByRole("button", { name: /20GP/ });
  await userEvent.click(screen.getByRole("button", { name: /20GP/ }));

  expect(screen.getByRole("button", { name: "常见产品规格" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "常见产品规格" }));
  await userEvent.click(screen.getByRole("menuitem", { name: /^四 SKU 案例（4 种整托，50 托）/ }));

  expect(screen.getByRole("button", { name: /40HQ/ })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getAllByText("需补充重量").length).toBeGreaterThan(0);
  expect(screen.getByRole("button", { name: "生成装柜方案" })).toBeDisabled();
});
