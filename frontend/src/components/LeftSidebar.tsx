import { useState } from "react";
import { Button, Input, Popconfirm, Tooltip } from "antd";
import { Eye, Files, History, Plus, RotateCcw, Search, Trash2 } from "lucide-react";
import type { TestSessionSummary, UploadedFile } from "../types";
import { ReviewPill } from "./SemanticDetails";

interface LeftSidebarProps {
  sessions: TestSessionSummary[];
  activeSessionId: string | null;
  files: UploadedFile[];
  onCreateSession: () => void;
  onSelectSession: (id: string) => void;
  onDeleteSession: (id: string) => void;
  onPreviewFile: (file: UploadedFile) => void;
  onDeleteFile: (fileId: string) => void;
  onReparseFile: (fileId: string) => Promise<void>;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function LeftSidebar(props: LeftSidebarProps) {
  const [query, setQuery] = useState("");
  const [parsing, setParsing] = useState<string | null>(null);
  const visible = props.sessions.filter((item) => item.title.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  return <section className="workspace-sidebar">
    <header><div><span className="sidebar-heading">项目工作区</span><h2>素材与会话</h2></div></header>
    <Button type="primary" block icon={<Plus size={16} />} onClick={props.onCreateSession}>新建会话</Button>
    <section className="sidebar-section">
      <h3><Files size={15} />工艺资料 <small>{props.files.length}</small></h3>
      {!props.files.length && <p className="muted">在需求对话中添加工艺文件。</p>}
      <div className="file-list">
        {props.files.map((file) => <article key={file.id}>
          <div className="side-row">
            <button className="side-select" type="button" title={file.filename} onClick={() => props.onPreviewFile(file)}>
              <span>{file.filename}</span><small>{file.file_ext.replace(".", "").toUpperCase()} · {formatBytes(file.file_size)}</small>
            </button>
            <Tooltip title="预览解析原文"><Button size="small" type="text" aria-label={`预览 ${file.filename}`}
              icon={<Eye size={15} />} onClick={() => props.onPreviewFile(file)} /></Tooltip>
            <Popconfirm title="移除此文件？" description="此操作会从会话中删除原始文件。" okText="移除"
              cancelText="取消" onConfirm={() => props.onDeleteFile(file.id)}>
              <Button size="small" type="text" aria-label={`移除 ${file.filename}`} icon={<Trash2 size={14} />} />
            </Popconfirm>
          </div>
          <div className="file-meta"><ReviewPill state={file.parse_status === "error" ? "error" : "success"} />
            {file.parse_status === "error" && <Button size="small" type="link" icon={<RotateCcw size={13} />}
              loading={parsing === file.id} onClick={async () => {
                setParsing(file.id);
                try { await props.onReparseFile(file.id); } finally { setParsing(null); }
              }}>重新解析</Button>}</div>
          {file.parse_error && <p className="file-error">{file.parse_error}</p>}
        </article>)}
      </div>
    </section>
    <section className="sidebar-section sidebar-history">
      <h3><History size={15} />历史会话 <small>{props.sessions.length}</small></h3>
      <Input allowClear size="small" prefix={<Search size={14} />} aria-label="搜索历史会话"
        placeholder="搜索会话" value={query} onChange={(event) => setQuery(event.target.value)} />
      <div className="session-list">
        {visible.length === 0 && <p className="muted">{query ? "没有符合条件的会话" : "暂无会话"}</p>}
        {visible.map((item) => <div className="side-row session-row" key={item.id} data-active={item.id === props.activeSessionId}>
          <button className="side-select" type="button" title={item.title}
            aria-current={item.id === props.activeSessionId ? "true" : undefined} onClick={() => props.onSelectSession(item.id)}>
            <span>{item.title}</span><small>{new Date(item.updated_at).toLocaleString("zh-CN", {
              month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
            })}</small>
          </button>
          <Popconfirm title="删除此测试会话？" description="会话、语义版本及上传文件将删除。" okText="删除"
            cancelText="取消" okButtonProps={{ danger: true }} onConfirm={() => props.onDeleteSession(item.id)}>
            <Button size="small" type="text" aria-label={`删除 ${item.title}`} icon={<Trash2 size={14} />} />
          </Popconfirm>
        </div>)}
      </div>
    </section>
  </section>;
}
