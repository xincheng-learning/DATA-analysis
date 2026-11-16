"use client";

import { useEffect, useState } from "react";
import { Dialog } from "@base-ui/react/dialog";
import { Check, FolderOpen, HardDrive, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { browseStorageDirectory, getStorageSettings, saveStorageSettings, type StorageSettingsStatus } from "@/lib/api";

function formatSize(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024)).toLocaleString()} KB`;
  return `${(bytes / (1024 * 1024)).toLocaleString(undefined, { maximumFractionDigits: 1 })} MB`;
}

export function StorageSettings({ open, onOpenChange, onSaved }: {
  open: boolean; onOpenChange: (open: boolean) => void; onSaved: () => Promise<void>;
}) {
  return open?<StorageSettingsDialog onOpenChange={onOpenChange} onSaved={onSaved}/>:null;
}

function StorageSettingsDialog({onOpenChange,onSaved}:{onOpenChange:(open:boolean)=>void;onSaved:()=>Promise<void>}){
  const [status, setStatus] = useState<StorageSettingsStatus | null>(null);
  const [directory, setDirectory] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [picking, setPicking] = useState(false);
  const [error, setError] = useState("");
  const [previous, setPrevious] = useState("");
  const busy = saving || picking;

  useEffect(() => {
    let active = true;
    getStorageSettings().then((current) => {
      if (active) { setStatus(current); setDirectory(current.directory); }
    }).catch((reason) => {
      if (active) { setStatus(null); setError(reason instanceof Error ? reason.message : "无法读取保存位置。"); }
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  async function save() {
    setSaving(true); setError(""); setPrevious("");
    try {
      const result = await saveStorageSettings(directory.trim());
      setStatus(result); setDirectory(result.directory); setPrevious(result.previous_directory || "");
      await onSaved();
    } catch (reason) {
      setError(reason instanceof TypeError ? "暂时无法确认切换结果，请重新打开设置查看当前目录，避免重复操作。" : reason instanceof Error ? reason.message : "保存失败，请查看当前目录后重试。");
    } finally { setSaving(false); }
  }

  async function browse() {
    setPicking(true); setError(''); setPrevious('');
    try {
      const chosen = await browseStorageDirectory(directory);
      if (chosen !== null) setDirectory(chosen);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '无法打开文件夹选择窗口，请直接填写路径。');
    } finally { setPicking(false); }
  }

  return <Dialog.Root open onOpenChange={(next) => { if (!busy) onOpenChange(next); }}>
    <Dialog.Portal>
      <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/40" />
      <Dialog.Popup className="storage-settings fixed left-1/2 top-1/2 z-50 w-[calc(100vw-2rem)] max-w-lg max-h-[90dvh] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-xl border bg-background p-6 shadow-lg">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3"><span className="rounded-lg bg-primary/10 p-2.5 text-primary"><HardDrive size={20} /></span><Dialog.Title className="text-lg font-semibold">聊天记录存储</Dialog.Title></div>
          <Button variant="ghost" size="icon" aria-label="关闭存储设置" onClick={() => onOpenChange(false)} disabled={busy}><X size={18} /></Button>
        </div>
        <Dialog.Description className="mt-3 text-sm leading-6 text-muted-foreground">将全部聊天与关联数据保存在你指定的本地文件夹。</Dialog.Description>
        {loading && <p role="status" className="mt-5 text-sm text-muted-foreground">正在读取保存位置…</p>}
        {status && !loading && <section className="mt-5 rounded-lg border bg-muted/20 p-4" aria-label="当前保存位置">
          <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground"><FolderOpen size={14} /> 当前保存位置</div>
          <p className="mt-2 break-all text-sm leading-6" data-current-directory>{status.directory}</p>
          <p className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground"><span>{status.session_count.toLocaleString()} 个聊天</span><span>{status.file_count.toLocaleString()} 个关联文件</span><span>占用 {formatSize(status.size_bytes)}</span></p>
        </section>}
        <form className="mt-5 space-y-4" onSubmit={(event) => { event.preventDefault(); void save(); }}>
          <fieldset disabled={loading || busy || !status}>
            <label htmlFor="chat-storage-directory" className="text-sm font-medium">新的保存文件夹</label>
            <div className="storage-path-picker"><Input id="chat-storage-directory" value={directory} onChange={(event) => { setDirectory(event.target.value); setError(""); setPrevious(""); }} placeholder="例如 G:\Data analysis聊天" autoComplete="off" spellCheck={false} required maxLength={4096} />
              <Button type="button" variant="outline" onClick={() => void browse()} disabled={!status?.browse_available} title={status?.browse_available ? '打开本机文件夹选择窗口' : '当前运行方式请手动填写完整路径'}><FolderOpen size={15} />{picking ? '正在选择…' : '浏览文件夹'}</Button></div>
            <p className="mt-2 text-xs leading-5 text-muted-foreground">浏览并选择空文件夹，也可以直接填写新文件夹路径。</p>
          </fieldset>
          {picking && <p role="status" className="text-xs text-muted-foreground">请在本机弹出的窗口中选择文件夹，或点击取消。</p>}
          <div className="rounded-lg border border-primary/15 bg-primary/5 px-4 py-3 text-xs leading-6 text-muted-foreground">保存时会复制已有聊天、上传数据及分析结果，核对完成后切换使用。原目录保留为当时的副本，之后不会继续同步。</div>
          {error && <p role="alert" className="text-sm leading-6 text-destructive">{error}</p>}
          {previous && <div role="status" className="rounded-lg border border-primary/20 px-4 py-3 text-sm leading-6"><p className="flex items-center gap-2 font-medium text-primary"><Check size={16} /> 已复制并切换，所有聊天可继续使用</p><p className="mt-1 break-all text-xs text-muted-foreground">原目录保留：{previous}</p></div>}
          <div className="flex justify-end gap-2 pt-1"><Button variant="outline" type="button" onClick={() => onOpenChange(false)} disabled={busy}>关闭</Button><Button type="submit" disabled={loading || busy || !status || !directory.trim() || directory.trim() === status.directory}>{saving ? "正在复制与核对…" : "保存并切换"}</Button></div>
        </form>
      </Dialog.Popup>
    </Dialog.Portal>
  </Dialog.Root>;
}
