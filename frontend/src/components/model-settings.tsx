"use client";

import { useEffect, useRef, useState } from "react";
import { Dialog } from "@base-ui/react/dialog";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  getModelSettings, saveModelSettings, testModelSettings,
  type ModelSettingsStatus, type ThinkingMode, type ReasoningEffort,
} from "@/lib/api";

interface ModelSettingsProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: (status: ModelSettingsStatus) => void;
}

function supportsThinking(address: string) {
  try { return new URL(address).hostname === "api.deepseek.com"; }
  catch { return false; }
}

export function ModelSettings({ open, onOpenChange, onSaved }: ModelSettingsProps) {
  const [status, setStatus] = useState<ModelSettingsStatus | null>(null);
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<"save" | "test" | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [thinkingMode, setThinkingMode] = useState<ThinkingMode>("auto");
  const [reasoningEffort, setReasoningEffort] = useState<ReasoningEffort>("low");
  const keyRef = useRef<HTMLInputElement>(null);
  const testController = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!open) return;
    let active = true;
    getModelSettings().then((current) => {
      if (!active) return;
      setStatus(current);
      setBaseUrl(current.provider === "openai_compatible" ? current.base_url : "");
      setModel(current.provider === "openai_compatible" ? current.model : "");
      setThinkingMode(current.thinking_mode || "auto");
      setReasoningEffort(current.reasoning_effort || "low");
    }).catch(() => {
      if (active) setError("无法读取已有配置，请确认后端服务已启动；也可以重新填写完整配置。");
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; testController.current?.abort(); };
  }, [open]);

  function close() {
    if (busy === "save") return;
    testController.current?.abort();
    if (keyRef.current) keyRef.current.value = "";
    setMessage("");
    setError("");
    onOpenChange(false);
  }

  function changed() {
    setMessage("");
    setError("");
  }

  async function submit(action: "save" | "test") {
    const key = keyRef.current?.value.trim();
    if (!baseUrl.trim() || !model.trim()) {
      setError("请填写接口地址和模型名称。");
      return;
    }
    if (!key && (!status?.has_api_key || status.provider !== "openai_compatible" || status.base_url.replace(/\/$/, "") !== baseUrl.trim().replace(/\/$/, ""))) {
      setError("首次配置或更换接口地址时，请重新输入 API 密钥。");
      return;
    }
    setBusy(action);
    setError("");
    setMessage("");
    const config = { base_url: baseUrl.trim(), model: model.trim(), ...(key ? { api_key: key } : {}),
      thinking_mode: supportsThinking(baseUrl) ? thinkingMode : "auto" as ThinkingMode, reasoning_effort: reasoningEffort };
    try {
      if (action === "test") {
        const controller = new AbortController();
        testController.current = controller;
        const result = await testModelSettings(config, controller.signal);
        if (!controller.signal.aborted) setMessage(result);
      } else {
        const saved = await saveModelSettings(config);
        setStatus(saved);
        if (keyRef.current) keyRef.current.value = "";
        setMessage("已加密保存并立即生效，无需重启。保存不会发起模型请求。");
        onSaved(saved);
      }
    } catch (err) {
      if (!(err instanceof Error && err.name === "AbortError")) {
        setError(err instanceof TypeError ? "无法连接后端，请确认后端服务已启动。" : err instanceof Error ? err.message : "操作失败，请稍后重试。");
      }
    } finally {
      setBusy(null);
      testController.current = null;
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={(next) => { if (!next) close(); }}>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/40" />
        <Dialog.Popup className="fixed left-1/2 top-1/2 z-50 w-[calc(100vw-2rem)] max-w-lg max-h-[90dvh] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-xl border bg-background p-6 shadow-lg">
          <div className="flex items-center justify-between gap-4">
            <Dialog.Title className="text-lg font-semibold">模型设置</Dialog.Title>
            <Button variant="ghost" size="icon" aria-label="关闭模型设置" onClick={close} disabled={busy === "save"}>
              <X className="w-4 h-4" />
            </Button>
          </div>
          <Dialog.Description className="mt-2 text-sm text-muted-foreground">
            连接支持 OpenAI 兼容接口的模型服务，保存后即可开始分析。
          </Dialog.Description>
          <form className="mt-5 space-y-4" onSubmit={(event) => { event.preventDefault(); void submit("save"); }}>
            <fieldset className="space-y-4" disabled={loading || busy !== null}>
              <div className="space-y-1.5">
                <label htmlFor="model-base-url" className="text-sm font-medium">接口地址</label>
                <Input id="model-base-url" value={baseUrl} onChange={(event) => { setBaseUrl(event.target.value); if (!supportsThinking(event.target.value)) setThinkingMode("auto"); changed(); }} placeholder="https://你的服务商地址/v1" required maxLength={2048} autoComplete="off" />
                <p className="text-xs text-muted-foreground">填写基础地址，不要加 /chat/completions。外部接口需 HTTPS，本机模型可用 HTTP。</p>
              </div>
              <div className="space-y-1.5">
                <label htmlFor="model-name" className="text-sm font-medium">模型名称</label>
                <Input id="model-name" value={model} onChange={(event) => { setModel(event.target.value); changed(); }} placeholder="填写服务商提供的准确模型名称" required maxLength={200} autoComplete="off" />
              </div>
              <div className="space-y-1.5">
                {supportsThinking(baseUrl) ? <div className="rounded-lg border bg-muted/20 p-3 space-y-3">
                  <div className="space-y-1.5">
                    <label htmlFor="model-thinking-mode" className="text-sm font-medium">DeepSeek 思考模式</label>
                    <select id="model-thinking-mode" className="w-full rounded-md border bg-background px-3 py-2 text-sm" value={thinkingMode} onChange={(event) => { setThinkingMode(event.target.value as ThinkingMode); changed(); }}>
                      <option value="auto">自动兼容（推荐）</option><option value="enabled">开启思考</option><option value="disabled">关闭思考</option>
                    </select>
                    <p className="text-xs text-muted-foreground">{thinkingMode === "enabled" ? "所有请求开启思考；工具由模型自行选择，返回后仍会验证格式。失败不会自动关闭思考重试。" : thinkingMode === "disabled" ? "所有请求明确关闭思考，适合优先考虑响应速度的任务。" : "工具格式请求关闭思考，普通请求跟随服务商默认。不是按问题难度自动判断。"}</p>
                  </div>
                  {thinkingMode === "enabled" && <div className="space-y-1.5">
                    <label htmlFor="model-reasoning-effort" className="text-sm font-medium">思考强度</label>
                    <select id="model-reasoning-effort" className="w-full rounded-md border bg-background px-3 py-2 text-sm" value={reasoningEffort} onChange={(event) => { setReasoningEffort(event.target.value as ReasoningEffort); changed(); }}>
                      <option value="low">低（先从这里开始）</option><option value="high">高</option><option value="max">最高</option>
                    </select>
                    <p className="text-xs text-muted-foreground">更强的思考可能增加等待和 Token 消耗，不保证答案更准确。每次连接测试最多生成 1024 Token（包含思考），仍只发送一次请求。</p>
                  </div>}
                </div> : <p className="text-xs text-muted-foreground">思考模式控件目前支持官方 DeepSeek 接口；其他模型使用服务商默认行为。</p>}
              </div>
              <div className="space-y-1.5">
                <label htmlFor="model-api-key" className="text-sm font-medium">API 密钥</label>
                <Input id="model-api-key" ref={keyRef} type="password" placeholder={status?.has_api_key && status.provider === "openai_compatible" ? "已有密钥；接口地址不变时留空可沿用" : "粘贴服务商提供的 API 密钥"} onChange={changed} autoComplete="new-password" maxLength={8192} spellCheck={false} />
                <p className="text-xs text-muted-foreground">密钥由后端使用 Windows 系统加密保存，不会回传页面；更换电脑或 Windows 账号后通常需要重新填写。</p>
              </div>
            </fieldset>
            {loading && <p className="text-sm text-muted-foreground" role="status">正在读取配置…</p>}
            {status?.source === "env" && <p className="text-xs text-muted-foreground">当前配置来自环境文件；保存后优先使用这里的设置。{status.provider !== "openai_compatible" && "本表单保存后将切换到 OpenAI 兼容接口。"}</p>}
            <p className="text-xs text-muted-foreground">测试连接会发送一次小型模型请求，可能产生少量费用，不发送业务文件。使用云模型分析时，问题及必要的数据内容会发送到你填写的接口。</p>
            {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
            {message && <p role="status" className="text-sm text-green-600">{message}</p>}
            <div className="flex justify-end gap-2 pt-1">
              <Button type="button" variant="outline" onClick={() => void submit("test")} disabled={loading || busy !== null}>
                {busy === "test" ? "正在测试…" : "测试连接"}
              </Button>
              <Button type="submit" disabled={loading || busy !== null}>
                {busy === "save" ? "正在保存…" : "保存并生效"}
              </Button>
            </div>
          </form>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
