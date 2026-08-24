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
    name: "DeepSeek",
    baseUrl: "https://api.deepseek.com/v1",
    models: [
      { id: "deepseek-v4-flash", label: "deepseek-v4-flash" },
      { id: "deepseek-v4-pro", label: "deepseek-v4-pro" },
      { id: "deepseek-v4-flash-vision-exp", label: "deepseek-v4-flash-vision-exp" },
    ],
  },
  {
    id: "qwen",
    name: "通义千问",
    baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    models: [
      { id: "qwen3-max", label: "qwen3-max" },
      { id: "qwen3-plus", label: "qwen3-plus" },
    ],
  },
  {
    id: "zhipu",
    name: "智谱",
    baseUrl: "https://open.bigmodel.cn/api/paas/v4",
    models: [
      { id: "glm-5.2", label: "glm-5.2" },
      { id: "glm-5.3", label: "glm-5.3" },
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
