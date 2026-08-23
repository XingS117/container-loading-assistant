import { packOrder } from "./api";
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

test("sends an optional AI key only as a request header", async () => {
  const response = { request_id: "ai", solutions: [] };
  const fetchSpy = vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValue(new Response(JSON.stringify(response), { status: 200 }));

  await packOrder(container, [createCargo("AI-KEY")], 0, "sk-test");

  const request = fetchSpy.mock.calls[0][1] as RequestInit;
  expect(new Headers(request.headers).get("X-AI-API-Key")).toBe("sk-test");
  expect(request.body).not.toContain("sk-test");
});
