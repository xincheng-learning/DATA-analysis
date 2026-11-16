"use client";

import { useEffect, useRef, useState } from "react";
import { Dialog } from "@base-ui/react/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getDatabaseSettings, saveDatabaseSettings, checkDatabase, type DatabaseConfig, type DatabaseTable } from "@/lib/api";

export function DatabaseSettings({ onClose, onImport }: { onClose: () => void; onImport: (tables: DatabaseTable[]) => Promise<void> }) {
  const [config, setConfig] = useState<DatabaseConfig>({ host: "127.0.0.1", port: 55432, database: "datapilot_lab", username: "datapilot_reader" });
  const [tables, setTables] = useState<DatabaseTable[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const password = useRef<HTMLInputElement>(null);
  useEffect(() => {
    let active = true;
    getDatabaseSettings().then((value) => { if (active && value.configured) setConfig(value); }).catch(() => { if (active) setError("读取设置失败，请确认后端已启动。"); });
    return () => { active = false; };
  }, []);

  async function act(action: "save" | "check" | "import") {
    setBusy(true); setError(""); setMessage("");
    try {
      if (action === "save") {
        const { host, port, database, username } = config;
        const value = await saveDatabaseSettings({ host, port, database, username, password: password.current?.value || undefined });
        if (password.current) password.current.value = "";
        setConfig(value); setTables([]); setSelected([]); setMessage("已加密保存，点击“检查连接并列出表”读取可用表。");
      } else if (action === "check") {
        setTables((await checkDatabase()).tables); setSelected([]); setMessage("连接检查通过，只列出有读取权限的表。空列表表示没有可读表。");
      } else {
        await onImport(tables.filter((table) => selected.includes(JSON.stringify(table))));
        onClose();
      }
    } catch (err) { setError(err instanceof Error ? err.message : "操作失败，请重试。"); }
    finally { setBusy(false); }
  }

  function change(field: keyof DatabaseConfig, value: string | number) {
    setConfig((previous) => ({ ...previous, [field]: value, configured: false })); setTables([]); setSelected([]); setMessage("");
  }
  return <Dialog.Root open onOpenChange={(open) => { if (!open && !busy) onClose(); }}>
    <Dialog.Portal><Dialog.Backdrop className="fixed inset-0 z-40 bg-black/40" />
      <Dialog.Popup className="fixed left-1/2 top-1/2 z-50 w-[calc(100vw-2rem)] max-w-lg max-h-[90dvh] -translate-x-1/2 -translate-y-1/2 overflow-auto rounded-xl border bg-background p-6 shadow-lg">
        <Dialog.Title className="text-lg font-semibold">PostgreSQL 数据来源</Dialog.Title>
        <Dialog.Description className="text-sm text-muted-foreground mt-2">连接本机数据库，将所选表保存为本次会话的只读快照，与已上传的 Excel 一起分析。</Dialog.Description>
        <form className="space-y-3 mt-4" onSubmit={(event) => { event.preventDefault(); void act("save"); }}>
          <fieldset disabled={busy} className="space-y-3">
            {([ ["host", "主机地址"], ["port", "端口"], ["database", "数据库名"], ["username", "只读用户名"] ] as const).map(([field, label]) => <div key={field}><label className="text-sm" htmlFor={`db-${field}`}>{label}</label><Input id={`db-${field}`} type={field === "port" ? "number" : "text"} value={config[field]} required onChange={(event) => change(field, field === "port" ? Number(event.target.value) : event.target.value)} /></div>)}
            <label className="text-sm block" htmlFor="db-password">密码</label><Input id="db-password" ref={password} type="password" autoComplete="new-password" onChange={() => { setConfig((previous) => ({ ...previous, configured: false })); setTables([]); setSelected([]); }} placeholder={config.configured ? "连接完全不变时留空沿用" : "输入数据库密码"} />
            <p className="text-xs text-muted-foreground">密码使用当前 Windows 用户加密，页面不会读取密码。请使用只有 SELECT 权限的账号。每表最多 50,000 行、每次最多 5 表，超过上限会拒绝，避免截断误判。</p>
            <div className="flex gap-2"><Button type="submit" variant="outline">保存连接</Button><Button type="button" onClick={() => void act("check")} disabled={!config.configured}>检查连接并列出表</Button></div>
            {tables.map((table) => { const key = JSON.stringify(table); return <label key={key} className="flex gap-2 text-sm"><input type="checkbox" checked={selected.includes(key)} onChange={(event) => setSelected((current) => event.target.checked ? [...current, key] : current.filter((item) => item !== key))} />{table.schema_name}.{table.table_name}</label>; })}
          </fieldset>
          {message && <p role="status" className="text-sm">{message}</p>}{error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          <div className="flex justify-end gap-2"><Button type="button" variant="ghost" disabled={busy} onClick={onClose}>关闭</Button><Button type="button" disabled={busy || !selected.length || selected.length > 5} onClick={() => void act("import")}>{busy ? "正在处理…" : "导入所选表"}</Button></div>
        </form>
      </Dialog.Popup></Dialog.Portal>
  </Dialog.Root>;
}
