import type { AIModelConfig, AIProvider } from "../types";

export const AI_CONFIG_STORAGE_KEY = "container-loading-assistant-ai-config-v1";

export const AI_PROVIDERS: Array<{
  id: AIProvider;
  name: string;
  baseUrl: string;
  models: Array<{ id: string; label: string }>;
}> = [
  {
    id: "deepseek",
    name: "DeepSeek 深蓝科技",
    baseUrl: "https://api.deepseek.com/v1",
    models: [
      { id: "deepseek-v4", label: "DeepSeek V4 旗舰版" },
      { id: "deepseek-v4-flash", label: "DeepSeek V4 Flash 快速版" },
    ],
  },
  {
    id: "qwen",
    name: "通义千问",
    baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    models: [
      { id: "qwen3-max", label: "Qwen3 Max 旗舰版" },
      { id: "qwen3-plus", label: "Qwen3 Plus 均衡版" },
    ],
  },
  {
    id: "zhipu",
    name: "智谱 AI",
    baseUrl: "https://open.bigmodel.cn/api/paas/v4",
    models: [
      { id: "glm-4.5", label: "GLM-4.5 旗舰版" },
      { id: "glm-4.5-air", label: "GLM-4.5 Air 快速版" },
    ],
  },
];

export const defaultAIConfig = (): AIModelConfig => {
  const provider = AI_PROVIDERS[0];
  return { provider: provider.id, model: provider.models[0].id, baseUrl: provider.baseUrl, apiKey: "" };
};

export function providerConfig(providerId: AIProvider) {
  return AI_PROVIDERS.find((provider) => provider.id === providerId) ?? AI_PROVIDERS[0];
}

export function loadAIConfig(): AIModelConfig {
  try {
    const value = JSON.parse(sessionStorage.getItem(AI_CONFIG_STORAGE_KEY) ?? "{}") as Partial<AIModelConfig>;
    if (!AI_PROVIDERS.some((provider) => provider.id === value.provider)) return defaultAIConfig();
    const provider = providerConfig(value.provider!);
    return {
      provider: provider.id,
      model: typeof value.model === "string" && provider.models.some((model) => model.id === value.model)
        ? value.model
        : provider.models[0].id,
      baseUrl: typeof value.baseUrl === "string" && value.baseUrl.trim() ? value.baseUrl : provider.baseUrl,
      apiKey: typeof value.apiKey === "string" ? value.apiKey : "",
    };
  } catch {
    return defaultAIConfig();
  }
}

export function saveAIConfig(config: AIModelConfig) {
  sessionStorage.setItem(AI_CONFIG_STORAGE_KEY, JSON.stringify(config));
}
