import { orientationsFor, validateCargo } from "./cargo";
import type { AIModelConfig, CargoInput, ContainerSpec, LayoutReviewResponse, PackResponse, Placement, SolutionProfile } from "../types";

export type CalculationPhase = 'submitting' | 'waiting' | 'ai' | 'solving' | 'finalizing' | 'reading';
export class CalculationError extends Error {
  category: 'timeout' | 'network' | 'input' | 'busy' | 'service';
  code: string;
  constructor(message: string, category: CalculationError['category'], code: string) {
    super(message); this.category = category; this.code = code;
  }
}
const timeoutMessage = '本次计算等待超时，清单和原方案已保留。可稍后重试；若再次超时，请拆分订单或减少本次货物种类。服务器可能仍在结束本次任务，请勿连续重复提交。';

function cargoPayload(cargoItems: CargoInput[]) {
  return cargoItems.map((item) => ({
    id: item.id,
    sku: item.sku.trim(),
    name: item.name.trim() || item.sku.trim(),
    kind: item.kind,
    length_mm: Math.round(item.length_cm * 10),
    width_mm: Math.round(item.width_cm * 10),
    height_mm: Math.round(item.height_cm * 10),
    weight_g: Math.round(item.weight_kg! * 1000),
    quantity: item.quantity,
    allowed_orientations: orientationsFor(item.orientation_mode),
    stackable: item.stackable,
    max_layers: item.stackable ? item.max_layers : 1,
    max_top_load_g: item.stackable || item.kind === "pallet" ? Math.round(item.max_top_load_kg * 1000) : 0,
    fragile: item.fragile,
    must_load: item.must_load,
    unload_order: item.unload_order ?? 0,
  }));
}

async function readJsonResponse<T>(response: Response, context: string): Promise<T> {
  const body = await response.text();
  try {
    return JSON.parse(body) as T;
  } catch {
    const status = response.status ? `（HTTP ${response.status}）` : "";
    throw new Error(
      `${context}${status}：服务器返回了网页而不是接口数据，请检查服务状态后重试`,
    );
  }
}

export async function getContainerPresets(): Promise<ContainerSpec[]> {
  const response = await fetch("/api/v1/container-presets");
  const payload = await readJsonResponse<ContainerSpec[]>(response, "读取标准柜型失败");
  if (!response.ok) throw new Error("无法读取标准柜型，请稍后重试");
  return payload;
}

async function waitForPackJob(submitted: Response, signal: AbortSignal, onPhase?: (phase: CalculationPhase) => void): Promise<Response> {
  const accepted = await readJsonResponse<{job_id?: string}>(submitted, '提交任务失败');
  if (!accepted || typeof accepted.job_id !== 'string' || !/^[a-f0-9]{32}$/.test(accepted.job_id)) throw new CalculationError('服务未返回有效任务编号，原订单仍保留。', 'service', 'INVALID_RESPONSE');
  let retries = 0;
  while (!signal.aborted) {
    try {
      const response = await fetch(`/api/v1/pack/jobs/${accepted.job_id}`, {signal: AbortSignal.any([signal, AbortSignal.timeout(10000)]), cache:'no-store'});
      if ([502,503,504].includes(response.status) && retries < 3) throw new TypeError('Polling temporarily unavailable');
      if (!response.ok) return response;
      const state = await readJsonResponse<{phase: string; result?: PackResponse; error?: object; http_status?: number}>(response, '查询计算任务失败');
      if (!state || typeof state.phase !== 'string') throw new CalculationError('计算任务状态无法识别，原订单仍保留。', 'service', 'INVALID_RESPONSE');
      if (state.phase === 'complete') return new Response(JSON.stringify(state.result), {status:200});
      if (state.phase === 'failed') return new Response(JSON.stringify({error:state.error}), {status:state.http_status ?? 500});
      if (!['queued','ai','solving','finalizing'].includes(state.phase)) throw new CalculationError('计算任务状态无法识别，原订单仍保留。', 'service', 'INVALID_RESPONSE');
      onPhase?.(state.phase === 'queued' ? 'waiting' : state.phase as CalculationPhase);
      retries = 0;
    } catch (reason) {
      if (signal.aborted) throw reason;
      if (!(reason instanceof TypeError || reason instanceof DOMException && reason.name === 'TimeoutError') || retries >= 3) throw reason;
      retries++;
    }
    await new Promise<void>((resolve, reject) => {
      const abort = () => { window.clearTimeout(timer); reject(new DOMException('aborted','AbortError')); };
      const timer = window.setTimeout(() => { signal.removeEventListener('abort',abort); resolve(); }, 1000);
      signal.addEventListener('abort',abort,{once:true});
      if(signal.aborted) abort();
    });
  }
  throw new DOMException('aborted','AbortError');
}

export async function packOrder(
  container: ContainerSpec,
  cargoItems: CargoInput[],
  itemGapCm: number,
  aiConfig?: AIModelConfig,
  preferredProfile: SolutionProfile = "high_fill",
  lockedPlacements: import("../types").Placement[] = [],
  onPhase?: (phase: CalculationPhase) => void,
): Promise<PackResponse> {
  const validationError = validateCargo(cargoItems);
  if (validationError) throw new Error(validationError);
  if (cargoItems.some((item) => item.weight_kg == null)) {
    throw new Error("请先补充所有货物的单托重量");
  }
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 80000);
  try {
    onPhase?.('submitting');
    const options: RequestInit = {
      signal: controller.signal,
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(aiConfig?.apiKey?.trim() ? {
          "X-AI-API-Key": aiConfig.apiKey.trim(),
          "X-AI-Provider": aiConfig.provider,
          "X-AI-Model": aiConfig.model,
          "X-AI-Base-URL": aiConfig.baseUrl,
        } : {}),
      },
      body: JSON.stringify({
        container,
        item_gap_mm: Math.round(itemGapCm * 10),
        preferred_profile: preferredProfile,
        locked_placements: lockedPlacements,
        cargo_items: cargoPayload(cargoItems),
      }),
    };
    const pending = fetch('/api/v1/pack/jobs', options);
    onPhase?.('waiting');
    let response = await pending;
    if (response.status === 404 || response.status === 405) response = await fetch('/api/v1/pack', options);
    if (response.status === 202) response = await waitForPackJob(response, controller.signal, onPhase);
    onPhase?.('reading');
    if (response.status === 504 || response.status === 408) throw new CalculationError(timeoutMessage, 'timeout', 'GATEWAY_TIMEOUT');
    if (response.status === 429) throw new CalculationError('请求过于频繁，请稍后重试，原方案已保留。', 'busy', 'RATE_LIMITED');
    const payload = await readJsonResponse<PackResponse & { error?: { code?: string; message?: string; hint?: string } }>(response, "装柜服务返回了无效响应");
    if (!response.ok) {
      const message = payload?.error?.message ?? "计算失败，请检查货物参数";
      const hint = payload?.error?.hint;
      const code = payload?.error?.code;
      if (code === "CALCULATION_TIMEOUT" || code === 'LOCKED_RECALCULATION_TIMEOUT') {
        throw new CalculationError(timeoutMessage, 'timeout', code);
      }
      if (code === "CALCULATION_BUSY") {
        throw new CalculationError('当前计算任务较多，清单和原方案已保留，请稍后重试。', 'busy', code);
      }
      throw new CalculationError(hint ? `${message}\n${hint}` : message, response.status === 422 ? 'input' : 'service', code ?? `HTTP_${response.status}`);
    }
    if (!payload || typeof payload.request_id !== 'string' || !Array.isArray(payload.solutions)) throw new CalculationError('服务返回的方案不完整，请稍后重试。清单和原方案已保留。', 'service', 'INVALID_RESPONSE');
    return payload;
  } catch (reason) {
    if (controller.signal.aborted) throw new CalculationError(timeoutMessage, 'timeout', 'CLIENT_TIMEOUT');
    if (reason instanceof CalculationError) throw reason;
    if (reason instanceof TypeError) throw new CalculationError('网络连接中断，清单和原方案已保留。请检查网络后重试。', 'network', 'NETWORK_ERROR');
    if (reason instanceof DOMException && reason.name === 'TimeoutError') throw new CalculationError('任务状态查询暂时无法连接，清单和原方案已保留，请稍后重试。', 'network', 'POLL_TIMEOUT');
    throw new CalculationError(reason instanceof Error ? reason.message : '装柜服务异常，请稍后重试', 'service', 'INVALID_RESPONSE');
  } finally { window.clearTimeout(timeout); }
}

export async function reviewLayout(
  container: ContainerSpec,
  cargoItems: CargoInput[],
  placements: Placement[],
  itemGapCm: number,
): Promise<LayoutReviewResponse> {
  const response = await fetch("/api/v1/layout/review", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ container, item_gap_mm: Math.round(itemGapCm * 10), cargo_items: cargoPayload(cargoItems), placements }),
  });
  const payload = await readJsonResponse<LayoutReviewResponse & { error?: { message?: string } }>(response, "布局检查返回了无效响应");
  if (!response.ok) throw new Error(payload.error?.message ?? "布局检查失败，请稍后重试");
  return payload;
}

export async function testAIConnection(config: AIModelConfig): Promise<string> {
  const response = await fetch("/api/v1/ai/test", {
    method: "POST",
    headers: {
      "X-AI-API-Key": config.apiKey.trim(),
      "X-AI-Provider": config.provider,
      "X-AI-Model": config.model,
      "X-AI-Base-URL": config.baseUrl,
    },
  });
  const payload = await readJsonResponse<{ message?: string; error?: { message?: string } }>(response, "AI 连接接口返回了无效响应");
  if (!response.ok) throw new Error(payload?.error?.message ?? "连接测试失败，请检查配置");
  return payload.message as string;
}

