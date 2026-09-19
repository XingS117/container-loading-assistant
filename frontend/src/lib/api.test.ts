import { getContainerPresets, packOrder, testAIConnection } from "./api";
import { createCargo } from "./cargo";
import type { Placement } from "../types";

beforeEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });
afterEach(() => { vi.useRealTimers(); });

const container = {
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

test("does not send an order when a cargo weight is missing", async () => {
  const fetchSpy = vi.spyOn(globalThis, "fetch");
  const cargo = createCargo("MISSING-WEIGHT");
  cargo.weight_kg = null;

  await expect(packOrder(container, [cargo], 0)).rejects.toThrow(
    "请先补充所有货物的单托重量",
  );
  expect(fetchSpy).not.toHaveBeenCalled();
});

test("sends AI provider settings only as request headers", async () => {
  const response = { request_id: "ai", solutions: [] };
  const fetchSpy = vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValue(new Response(JSON.stringify(response), { status: 200 }));

  await packOrder(container, [createCargo("AI-KEY")], 0, {
    provider: "qwen",
    model: "qwen3-max",
    baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    apiKey: "sk-test",
  });

  const request = fetchSpy.mock.calls[0][1] as RequestInit;
  expect(new Headers(request.headers).get("X-AI-API-Key")).toBe("sk-test");
  expect(new Headers(request.headers).get("X-AI-Provider")).toBe("qwen");
  expect(new Headers(request.headers).get("X-AI-Model")).toBe("qwen3-max");
  expect(request.body).not.toContain("sk-test");
});

test("sends the user's layout priority in the packing request", async () => {
  const fetchSpy = vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValue(new Response(JSON.stringify({ request_id: "goal", solutions: [] }), { status: 200 }));

  await packOrder(container, [createCargo("GOAL")], 0, undefined, "easy");

  expect(JSON.parse(fetchSpy.mock.calls[0][1]?.body as string)).toMatchObject({
    preferred_profile: "easy",
  });
});

test("sends locked placements when recalculating a layout", async () => {
  const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ request_id: "locked", solutions: [] }), { status: 200 }));
  const cargo = createCargo("LOCKED");
  const locked: Placement[] = [{ id: "locked-0", cargo_id: cargo.id, instance_index: 0, x_mm: 0, y_mm: 0, z_mm: 0, length_mm: 600, width_mm: 400, height_mm: 400, rotation: "LWH", weight_g: 18000, step: 1 }];
  await packOrder(container, [cargo], 0, undefined, "high_fill", locked);
  expect(JSON.parse(fetchSpy.mock.calls[0][1]?.body as string).locked_placements).toHaveLength(1);
});

test("explains an HTML gateway response instead of exposing a JSON parse error", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response("<html><h1>502 Bad Gateway</h1></html>", {
      status: 502,
      headers: { "Content-Type": "text/html" },
    }),
  );

  await expect(getContainerPresets()).rejects.toThrow(
    "读取标准柜型失败（HTTP 502）：服务器返回了网页而不是接口数据",
  );
});

test("explains an HTML response from the packing endpoint", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response("<html><h1>Service Unavailable</h1></html>", {
      status: 503,
      headers: { "Content-Type": "text/html" },
    }),
  );

  await expect(packOrder(container, [createCargo("HTML-RESPONSE")], 0)).rejects.toThrow(
    "装柜服务返回了无效响应（HTTP 503）：服务器返回了网页而不是接口数据",
  );
});

test("explains a calculation timeout with a next step", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
    error: { code: "CALCULATION_TIMEOUT", message: "订单较复杂，45 秒内未完成计算" },
  }), { status: 504 }));

  await expect(packOrder(container, [createCargo("TIMEOUT")], 0)).rejects.toThrow(
    "本次计算等待超时",
  );
});

test('classifies gateway HTML timeouts and network failures without losing the action hint', async () => {
  const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response('<html>timeout</html>', {status:504})).mockRejectedValueOnce(new TypeError('Failed to fetch'));
  await expect(packOrder(container, [createCargo('A')], 0)).rejects.toMatchObject({category:'timeout',code:'GATEWAY_TIMEOUT'});
  await expect(packOrder(container, [createCargo('A')], 0)).rejects.toMatchObject({category:'network'});
  spy.mockRestore();
});

test('reports real request phases and rejects malformed successful responses', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}', {status:200}));
  const phase = vi.fn();
  await expect(packOrder(container, [createCargo('A')], 0, undefined, 'high_fill', [], phase)).rejects.toMatchObject({category:'service'});
  expect(phase.mock.calls.flat()).toEqual(['submitting','waiting','reading']);
});

test('bounds waiting with an abort signal and classifies client timeout', async () => {
  vi.useFakeTimers();
  vi.spyOn(globalThis, 'fetch').mockImplementation((_url, options) => new Promise((_resolve, reject) => options?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))));
  const assertion = expect(packOrder(container, [createCargo('A')], 0)).rejects.toMatchObject({category:'timeout',code:'CLIENT_TIMEOUT'});
  await vi.advanceTimersByTimeAsync(80000);
  await assertion;
  vi.useRealTimers();
});

test.each([
  [422, JSON.stringify({error:{message:'参数不合法'}}), 'input'],
  [503, JSON.stringify({error:{code:'CALCULATION_BUSY'}}), 'busy'],
  [429, '<html>Rate limited</html>', 'busy'],
])('classifies HTTP %s without automatic retries', async (status, body, category) => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(body as string, {status:status as number}));
  await expect(packOrder(container, [createCargo('A')], 0)).rejects.toMatchObject({category});
  expect(fetch).toHaveBeenCalledTimes(1);
});

test("explains an HTML response from the AI connection endpoint", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response("<html><h1>Bad Gateway</h1></html>", {
      status: 502,
      headers: { "Content-Type": "text/html" },
    }),
  );

  await expect(
    testAIConnection({
      provider: "deepseek",
      model: "deepseek-v4-flash",
      baseUrl: "https://api.deepseek.com/v1",
      apiKey: "sk-test",
    }),
  ).rejects.toThrow(
    "AI 连接接口返回了无效响应（HTTP 502）：服务器返回了网页而不是接口数据",
  );
});

test('polls the accepted job through real stages without resubmitting the order or AI key', async () => {
  vi.useFakeTimers();
  const job='a'.repeat(32);
  const fetch=vi.spyOn(globalThis,'fetch')
    .mockResolvedValueOnce(new Response(JSON.stringify({job_id:job,phase:'queued'}),{status:202}))
    .mockResolvedValueOnce(new Response(JSON.stringify({job_id:job,phase:'solving'})))
    .mockRejectedValueOnce(new TypeError('connection reset'))
    .mockResolvedValueOnce(new Response(JSON.stringify({job_id:job,phase:'complete',result:{request_id:'done',solutions:[]}})));
  const phase=vi.fn();
  const result=packOrder(container,[createCargo('A')],0,{provider:'deepseek',model:'test',baseUrl:'https://api.deepseek.com',apiKey:'secret'},'stable',[],phase);
  await vi.advanceTimersByTimeAsync(5000);
  expect((await result).request_id).toBe('done');
  expect(fetch.mock.calls.filter(([,o])=>o?.method==='POST')).toHaveLength(1);
  expect(fetch.mock.calls[0][0]).toBe('/api/v1/pack/jobs');
  expect(fetch.mock.calls.slice(1).every(([url,o])=>url===`/api/v1/pack/jobs/${job}` && !new Headers(o?.headers).has('X-AI-API-Key'))).toBe(true);
  expect(phase).toHaveBeenCalledWith('solving');
});

test('falls back only when job submission is unsupported on an older server', async () => {
  const fetch=vi.spyOn(globalThis,'fetch').mockResolvedValueOnce(new Response('{}',{status:404}))
    .mockResolvedValueOnce(new Response(JSON.stringify({request_id:'legacy',solutions:[]})));
  expect((await packOrder(container,[createCargo('A')],0)).request_id).toBe('legacy');
  expect(fetch.mock.calls.map(([url])=>url)).toEqual(['/api/v1/pack/jobs','/api/v1/pack']);
});
