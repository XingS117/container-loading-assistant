import { ArrowLeft, CheckCircle2, CircleAlert, KeyRound, LoaderCircle, Save, TestTube2 } from "lucide-react";
import { useState } from "react";

import { AI_PROVIDERS, providerConfig } from "../lib/aiConfig";
import type { AIModelConfig, AIProvider } from "../types";

interface ModelSettingsProps {
  config: AIModelConfig;
  onBack: () => void;
  onSave: (config: AIModelConfig) => void;
  onTest: (config: AIModelConfig) => Promise<string>;
}

export function ModelSettings({ config, onBack, onSave, onTest }: ModelSettingsProps) {
  const [draft, setDraft] = useState(config);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ kind: "success" | "error"; message: string } | null>(null);
  const provider = providerConfig(draft.provider);

  const changeProvider = (providerId: AIProvider) => {
    const next = providerConfig(providerId);
    setDraft({ provider: next.id, model: next.models[0].id, baseUrl: next.baseUrl, apiKey: draft.apiKey });
    setTestResult(null);
  };

  const testConnection = async () => {
    if (!draft.apiKey.trim()) {
      setTestResult({ kind: "error", message: "请先填写 API Key" });
      return;
    }
    setTesting(true);
    setTestResult(null);
    try {
      setTestResult({ kind: "success", message: await onTest(draft) });
    } catch (reason) {
      setTestResult({ kind: "error", message: reason instanceof Error ? reason.message : "连接测试失败" });
    } finally {
      setTesting(false);
    }
  };

  return (
    <main className="model-settings-page">
      <header className="settings-topbar">
        <button type="button" className="back-button" onClick={onBack}><ArrowLeft size={17} />返回装柜</button>
        <div><span className="eyebrow">AI STRATEGY</span><h1>模型配置</h1></div>
      </header>
      <section className="model-settings-panel" aria-labelledby="model-settings-heading">
        <div className="settings-panel-heading">
          <div className="settings-icon"><KeyRound size={19} /></div>
          <div><h2 id="model-settings-heading">模型连接</h2><p>用于生成装柜策略建议，物理校验始终由本地算法执行。</p></div>
        </div>
        <div className="model-form">
          <label><span>模型提供商</span><select aria-label="模型提供商" value={draft.provider} onChange={(event) => changeProvider(event.target.value as AIProvider)}>{AI_PROVIDERS.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
          <label><span>模型名称</span><select aria-label="模型名称" value={draft.model} onChange={(event) => { setDraft({ ...draft, model: event.target.value }); setTestResult(null); }}>{provider.models.map((model) => <option key={model.id} value={model.id}>{model.label}</option>)}</select></label>
          <label><span>API Key</span><input aria-label="API Key" type="password" value={draft.apiKey} onChange={(event) => { setDraft({ ...draft, apiKey: event.target.value }); setTestResult(null); }} placeholder="请输入 API Key" autoComplete="off" /></label>
          <label><span>API 地址</span><input aria-label="API 地址" type="url" value={draft.baseUrl} onChange={(event) => { setDraft({ ...draft, baseUrl: event.target.value }); setTestResult(null); }} placeholder="https://.../v1" autoComplete="off" /></label>
        </div>
        {testResult && <div className={`connection-result ${testResult.kind}`} role="status">{testResult.kind === "success" ? <CheckCircle2 size={17} /> : <CircleAlert size={17} />}<span>{testResult.message}</span></div>}
        <div className="model-form-actions">
          <button type="button" className="model-save-button" onClick={() => onSave(draft)}><Save size={17} />保存配置</button>
          <button type="button" className="model-test-button" onClick={testConnection} disabled={testing}>{testing ? <LoaderCircle className="spin" size={17} /> : <TestTube2 size={17} />}{testing ? "正在测试" : "测试连接"}</button>
        </div>
        <p className="model-security-note">仅支持当前提供商的官方 HTTPS 地址。配置仅保存在当前浏览器会话中，关闭浏览器后会自动清除，不写入货物草稿。</p>
      </section>
    </main>
  );
}
