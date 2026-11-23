import { RunProgress, RunTimeline } from "./RequirementTools";
import type { PipelineStage } from "../types";
import { useRef, useState } from "react";
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  Eraser,
  FileSpreadsheet,
  FileText,
  Loader2,
  Paperclip,
  Play,
  RotateCcw,
  Send,
  Square,
  UploadCloud,
  User,
  X,
} from "lucide-react";
import type {
  ChatMessage,
  SampleDataset,
  UploadedFile,
} from "../types";

export type RunStage = "idle" | "parsing" | "extracting" | "filling" | "done";

interface ChatWorkbenchProps {
  ready: boolean;
  messages: ChatMessage[];
  files: UploadedFile[];
  samples: SampleDataset[];
  stage: RunStage;
  isRunning: boolean;
  progress: PipelineStage[];
  demoMode: boolean;
  onToggleDemoMode: (val: boolean) => void;
  onUploadFiles: (files: File[]) => Promise<UploadedFile[]>;
  onLoadSampleText: (sample: SampleDataset) => void;
  onLoadSampleCsv: (sample: SampleDataset) => Promise<void>;
  onPreviewFile: (file: UploadedFile) => void;
  onSendMessage: (text: string, attachedFileIds: string[]) => Promise<void>;
  onAbortRun: () => void;
  onRerunMessage: (messageId: string) => Promise<void>;
  onClearSession: () => Promise<void>;
  onSelectDebugMessage: (messageId: string) => void;
  onOpenAgentManager?: () => void;
  onOpenResult: (id: string) => void;
  revisionBase?: string;
  onClearRevision: () => void;
  strategy: "semantic" | "legacy";
  onStrategy: (strategy: "semantic" | "legacy") => void;
}

export function ChatWorkbench({
  ready,
  messages,
  files,
  samples,
  stage,
  isRunning,
  progress,
  demoMode,
  onToggleDemoMode,
  onUploadFiles,
  onLoadSampleText,
  onLoadSampleCsv,
  onPreviewFile,
  onSendMessage,
  onAbortRun,
  onRerunMessage,
  onClearSession,
  onSelectDebugMessage,
  onOpenAgentManager,
  onOpenResult, revisionBase, onClearRevision, strategy, onStrategy,
}: ChatWorkbenchProps) {
  const [input, setInput] = useState("");
  const [pendingFileIds, setPendingFileIds] = useState<string[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const fileMap = new Map(files.map((f) => [f.id, f]));

  const handleFilesSelected = async (selected: FileList | File[] | null) => {
    if (!ready || isRunning || !selected || selected.length === 0) return;
    const arr = Array.from(selected);
    setIsUploading(true);
    try {
      const uploaded = await onUploadFiles(arr);
      setPendingFileIds((prev) => [...new Set([...prev, ...uploaded.map((u) => u.id)])]);
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const handleSubmit = async () => {
    if (!ready || isRunning || isUploading) return;
    if (!input.trim() && pendingFileIds.length === 0 && files.length === 0) return;
    const text = input;
    const fids = pendingFileIds.filter((id) => fileMap.has(id));
    setInput("");
    setPendingFileIds([]);
    await onSendMessage(text, fids);
  };

  const handleLoadSample = async (sample: SampleDataset) => {
    if (sample.kind === "text") {
      setInput(sample.content);
      onLoadSampleText(sample);
    } else {
      await onLoadSampleCsv(sample);
    }
  };

  return (
    <section
      className="chat-workbench flex flex-col h-full min-w-0 relative"
      onDragOver={(e) => {
        e.preventDefault();
        setIsDragging(true);
      }}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node)) {
          setIsDragging(false);
        }
      }}
      onDrop={async (e) => {
        e.preventDefault();
        setIsDragging(false);
        await handleFilesSelected(e.dataTransfer.files);
      }}
    >
      {/* Drag overlay */}
      {isDragging && (
        <div className="absolute inset-3 z-30 rounded-2xl border-2 border-dashed border-indigo-500 bg-indigo-50/90 flex flex-col items-center justify-center pointer-events-none">
          <UploadCloud className="w-10 h-10 text-indigo-600 mb-2" />
          <p className="text-sm font-semibold text-indigo-900">松开以上传工业文档或表格</p>
          <p className="text-xs text-indigo-600 mt-1">支持 .xlsx / .docx / .pdf / .csv / .txt / .md</p>
        </div>
      )}

      {/* Subheader with stage status & clear button */}
      <div className="h-11 px-4 bg-white border-b border-slate-200 flex items-center justify-between gap-2 shrink-0">
        <div className="flex items-center gap-2 text-xs">
          <span className="font-semibold text-slate-700">需求对话</span>
          {stage !== "idle" && (
            <div className="flex items-center gap-1.5 ml-2 px-2.5 py-0.5 rounded-full bg-indigo-50 border border-indigo-200 text-[11px] text-indigo-700">
              {stage !== "done" ? (
                <Loader2 className="w-3 h-3 animate-spin" />
              ) : (
                <CheckCircle2 className="w-3 h-3 text-emerald-600" />
              )}
              <span>
                {stage === "parsing" && "文件解析中"}
                {stage === "extracting" && "正在执行"}
                {stage === "filling" && "正在读取结果"}
                {stage === "done" && "运行已返回"}
              </span>
            </div>
          )}
        </div>

        <div className="flex items-center gap-2">
          <label
            title="离线语义由表格列生成，旧版模式可运行合成样例"
            className="inline-flex items-center gap-1.5 px-2 py-1 rounded-md text-[11px] text-slate-600 hover:bg-slate-100 cursor-pointer select-none"
          >
            <input
              type="checkbox"
              checked={demoMode}
              onChange={(e) => onToggleDemoMode(e.target.checked)}
              className="rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
            />
            <span>离线运行</span>
          </label>

          <button
            type="button"
            onClick={() => { if (window.confirm("清空当前对话记录？")) void onClearSession(); }}
            disabled={isRunning || messages.length === 0}
            className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs text-slate-600 hover:text-rose-600 hover:bg-rose-50 disabled:opacity-40 transition-colors"
          >
            <Eraser className="w-3.5 h-3.5" />
            <span>清空会话</span>
          </button>
        </div>
      </div>

      {isRunning && <RunProgress steps={progress} />}

      {/* Messages Stream */}
      <div className="chat-stream flex-1 overflow-y-auto p-4 space-y-4" aria-live="polite">
        {messages.length === 0 ? (
          <div className="chat-start">
            <div className="chat-start-copy">
              <h2>从工艺资料开始</h2>
              <p>上传工艺表格或输入制造需求。解析完成后，可逐项核对工序、约束及来源。</p>
              <button className="chat-start-upload" type="button" onClick={() => fileInputRef.current?.click()}
                disabled={!ready || isUploading}><UploadCloud size={17} />添加工艺资料</button>
            </div>
            {!!samples.length && <details className="sample-disclosure"><summary>试用示例</summary>
            {/* Built-in Sample Cards */}
            <div className="sample-grid">
              {samples.map((s) => (
                <div
                  key={s.id}
                  className="bg-white border border-slate-200 rounded-xl p-3.5 hover:border-indigo-300 hover:shadow-xs transition-all flex flex-col justify-between"
                >
                  <div>
                    <div className="flex items-center gap-2">
                      {s.kind === "csv" ? (
                        <FileSpreadsheet className="w-4 h-4 text-emerald-600 shrink-0" />
                      ) : (
                        <FileText className="w-4 h-4 text-indigo-600 shrink-0" />
                      )}
                      <span className="text-xs font-semibold text-slate-800">{s.title}</span>
                    </div>
                    <p className="text-[11px] text-slate-500 mt-1.5 leading-relaxed">
                      {s.description}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => handleLoadSample(s)}
                    className="mt-3 w-full inline-flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-indigo-50 text-indigo-700 hover:bg-indigo-100 transition-colors"
                  >
                    <Play className="w-3 h-3" />
                    <span>{s.kind === "csv" ? "生成样例 CSV 到当前会话" : "填入输入框并测试"}</span>
                  </button>
                </div>
              ))}
            </div>
            </details>}
          </div>
        ) : (
          messages.map((msg) => {
            const isUser = msg.role === "user";
            const attachedFiles = (msg.file_ids || [])
              .map((fid) => fileMap.get(fid))
              .filter((f): f is UploadedFile => Boolean(f));

            return (
              <div
                key={msg.id}
                className={`flex gap-3 ${isUser ? "justify-end" : "justify-start"}`}
              >
                {!isUser && (
                  <div
                    className={`w-7 h-7 rounded-lg flex items-center justify-center shrink-0 mt-0.5 ${
                      msg.status === "error"
                        ? "bg-rose-100 text-rose-600"
                        : "bg-indigo-600 text-white"
                    }`}
                  >
                    {msg.status === "error" ? (
                      <AlertTriangle className="w-4 h-4" />
                    ) : (
                      <Bot className="w-4 h-4" />
                    )}
                  </div>
                )}

                <div
                  className={`chat-message max-w-[88%] rounded-xl px-4 py-3 text-xs leading-relaxed ${
                    isUser
                      ? "user-message"
                      : msg.status === "error"
                        ? "bg-rose-50 border border-rose-200 text-rose-900"
                        : "bg-white border border-slate-200 text-slate-800"
                  }`}
                >
                  {/* Header meta for assistant */}
                  {!isUser && (
                    <div className="flex flex-wrap items-center justify-between gap-2 mb-1.5 pb-1.5 border-b border-slate-100">
                      <div className="flex items-center gap-2">
                        <span className="font-semibold text-slate-900">
                          {msg.agent_name || "智能体"}{msg.status === "partial" && <small className="chat-partial">部分完成</small>}
                        </span>
                      </div>

                      <div className="flex items-center gap-1.5">
                        <button
                          type="button"
                          onClick={() => onSelectDebugMessage(msg.id)}
                          className="px-1.5 py-0.5 rounded text-[10px] font-medium text-slate-600 hover:bg-slate-100"
                          title="查看 Debug"
                        >
                          诊断
                        </button>
                        {msg.debug_info?.strategy !== "manual_review" && <button
                          type="button"
                          disabled={isRunning}
                          onClick={() => onRerunMessage(msg.id)}
                          title="使用当前智能体重新运行此轮"
                          className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-slate-100 text-slate-600 hover:bg-indigo-50 hover:text-indigo-700 disabled:opacity-40"
                        >
                          <RotateCcw className="w-2.5 h-2.5" />
                          <span>重跑</span>
                        </button>}
                      </div>
                    </div>
                  )}

                  {/* Message Text */}
                  <div className="whitespace-pre-wrap break-words">{msg.content}</div>
                  {!isUser && Boolean(msg.extracted_json?.semantic) && <button type="button"
                    className="text-action mt-2" onClick={() => onOpenResult(msg.id)}>打开语义结果 →</button>}

                  {!isUser && msg.status === "error" && onOpenAgentManager && (
                    <div className="mt-2">
                      <button
                        type="button"
                        onClick={onOpenAgentManager}
                        className="px-2 py-1 rounded-md bg-rose-100 hover:bg-rose-200 text-rose-800 text-[11px] font-medium transition-colors"
                      >
                        打开智能体配置并测试连接
                      </button>
                    </div>
                  )}

                  {/* Attached file chips */}
                  {isUser && attachedFiles.length > 0 && (
                    <div className="flex flex-wrap gap-1.5 mt-2">
                      {attachedFiles.map((af) => (
                        <button
                          key={af.id}
                          type="button"
                          onClick={() => onPreviewFile(af)}
                          className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] ${
                            isUser
                              ? "bg-white/70 text-slate-700"
                              : "bg-slate-100 hover:bg-slate-200 text-slate-700"
                          }`}
                        >
                          <FileText className="w-3 h-3" />
                          <span>{af.filename}</span>
                        </button>
                      ))}
                    </div>
                  )}

                  {!isUser && <RunTimeline steps={msg.debug_info?.tool_trace?.length
                    ? msg.debug_info.tool_trace : msg.debug_info?.pipeline_stages ?? []} />}
                </div>

                {isUser && (
                  <div className="w-7 h-7 rounded-lg bg-slate-200 text-slate-600 flex items-center justify-center shrink-0 mt-0.5">
                    <User className="w-4 h-4" />
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>

      {/* Composer */}
      <div className="p-3 bg-white border-t border-slate-200 shrink-0 space-y-2">
        {/* Pending files chips */}
        {pendingFileIds.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {pendingFileIds.map((fid) => {
              const f = fileMap.get(fid);
              if (!f) return null;
              return (
                <span
                  key={fid}
                  className="inline-flex items-center gap-1.5 px-2 py-1 rounded-md bg-indigo-50 border border-indigo-200 text-xs text-indigo-800"
                >
                  <FileText className="w-3.5 h-3.5" />
                  <button
                    type="button"
                    onClick={() => onPreviewFile(f)}
                    className="hover:underline"
                  >
                    {f.filename}
                  </button>
                  <button
                    type="button"
                    onClick={() =>
                      setPendingFileIds((prev) => prev.filter((id) => id !== fid))
                    }
                    className="text-indigo-400 hover:text-indigo-700"
                  >
                    <X className="w-3 h-3" />
                  </button>
                </span>
              );
            })}
          </div>
        )}

        <div className="composer-options">
          <details className="attachment-picker">
            <summary>解析选项</summary>
            <div><label>抽取策略 <select aria-label="抽取策略" value={strategy} disabled={isRunning} onChange={(event) =>
              onStrategy(event.target.value === "legacy" ? "legacy" : "semantic")}>
              <option value="semantic">领域语义</option><option value="legacy">旧版自定义 Schema</option>
            </select></label></div>
          </details>
          {files.length > 0 && <details className="attachment-picker">
            <summary>{pendingFileIds.length > 0 ? `已引用 ${pendingFileIds.length} 份` : "使用会话文件"}</summary>
            <div>{files.map((file) => <label key={file.id}><input type="checkbox"
              checked={pendingFileIds.includes(file.id)}
              onChange={(event) => setPendingFileIds((current) => event.target.checked
                ? [...current, file.id] : current.filter((id) => id !== file.id))} />{file.filename}</label>)}</div>
          </details>}
          {revisionBase && <button type="button" className="text-action" onClick={onClearRevision}>
            修订版本 {revisionBase.slice(0, 6)} ×</button>}
        </div>
        <div className="flex items-end gap-2">
          <input
            id="workbench-upload"
            ref={fileInputRef}
            type="file"
            multiple
            accept=".txt,.md,.csv,.xlsx,.docx,.pdf"
            className="hidden"
            onChange={(e) => handleFilesSelected(e.target.files)}
          />

          <button
            type="button"
            disabled={!ready || isUploading || isRunning}
            onClick={() => fileInputRef.current?.click()}
            aria-label="上传文件"
            title="上传文件 (.xlsx, .docx, .pdf, .csv, .txt, .md)"
            className="p-2.5 rounded-xl border border-slate-200 bg-slate-50 hover:bg-slate-100 text-slate-600 disabled:opacity-40 transition-colors shrink-0"
          >
            {isUploading ? (
              <Loader2 className="w-4 h-4 animate-spin text-indigo-600" />
            ) : (
              <Paperclip className="w-4 h-4" />
            )}
          </button>

          <textarea
            id="workbench-input"
            disabled={!ready}
            aria-label="输入工艺需求"
            rows={2}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (!e.nativeEvent.isComposing && (e.metaKey || e.ctrlKey) && e.key === "Enter") {
                e.preventDefault();
                handleSubmit();
              }
            }}
            placeholder={
              files.length > 0
                ? "输入补充需求或修订说明，Ctrl / ⌘ + Enter 发送"
                : "输入工艺需求或拖入文件，Ctrl / ⌘ + Enter 发送"
            }
            className="flex-1 min-w-0 resize-none rounded-xl border border-slate-200 bg-slate-50 focus:bg-white focus:border-indigo-400 focus:outline-none px-3 py-2 text-xs text-slate-800 leading-relaxed"
          />

          {isRunning ? (
            <button
              type="button"
              onClick={onAbortRun}
              className="stop-action px-3.5 py-2.5 rounded-xl bg-rose-600 hover:bg-rose-700 text-white text-xs font-semibold inline-flex items-center gap-1.5 shadow-sm shrink-0"
            >
              <Square className="w-3.5 h-3.5 fill-current" />
              <span>中止</span>
            </button>
          ) : (
            <button
              type="button"
              onClick={handleSubmit}
              disabled={!ready || isUploading || (!input.trim() && pendingFileIds.length === 0 && files.length === 0)}
              className="send-action px-4 py-2.5 rounded-xl text-xs font-semibold inline-flex items-center gap-1.5 transition-colors shrink-0"
            >
              <Send className="w-3.5 h-3.5" />
              <span>开始解析</span>
            </button>
          )}
        </div>
      </div>
    </section>
  );
}
