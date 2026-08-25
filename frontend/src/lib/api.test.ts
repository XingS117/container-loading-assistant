import { getContainerPresets, packOrder, testAIConnection } from "./api";
import { createCargo } from "./cargo";

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
