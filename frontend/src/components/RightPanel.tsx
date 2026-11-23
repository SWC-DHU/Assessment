import { useEffect, useMemo, useState } from "react";
import { Alert, Button, Empty, Select } from "antd";
import { ArrowDownToLine, Braces, Copy, Expand, FlaskConical, GitBranch, Layers3, LayoutGrid, Sheet, Terminal } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type {
  ChatMessage, Evidence, OutputTemplate, ReviewState, SemanticEntity, SemanticGroup,
  SemanticModel, SemanticVersion, TemplatePreviewResponse, PipelineStage, ProcessGraph, ReviewRequest,
} from "../types";
import { semanticFrom } from "../types";
import { HtmlPreview } from "./HtmlPreview";
import { groups, relationLabel, SemanticGraph } from "./SemanticGraph";
import { OntologyBrowser, ReviewDialog, RunProgress, toolNames } from "./RequirementTools";
import type { Point } from "./useTopologyViewport";

const tabs = [
  { id: "overview", name: "概览", icon: LayoutGrid },
  { id: "topology", name: "工艺拓扑", icon: GitBranch },
  { id: "semantics", name: "语义模型", icon: Layers3 },
  { id: "json", name: "结构化数据", icon: Braces },
  { id: "preview", name: "模板预览", icon: Sheet },
  { id: "debug", name: "Debug", icon: Terminal },
] as const;
export type ResultTab = (typeof tabs)[number]["id"];
const emptyTopologyPositions: ReadonlyMap<string, Point> = new Map();

const statusLabels: Record<string, string> = {
  verified: "语义校验通过", pending: "待确认", conflict: "来源冲突", invalid: "校验不通过",
  confirmed: "人工确认", complete: "解析完成", failed: "解析失败", located: "来源已定位",
  unprocessed: "未处理", success: "完成", skipped: "未执行", error: "失败", partial: "部分完成",
};

const stageLabels: Record<string, string> = {
  ...toolNames,
  file_parsing: "输入文件解析", input_selection: "来源选择", source_preprocessing: "来源预处理",
  deterministic_extraction: "确定性抽取", llm_extraction: "LLM 信息抽取", schema_validation: "候选 Schema 验证",
  llm_repair: "候选修复", ontology_mapping: "本体与术语映射", llm_disambiguation: "模型辅助消歧",
  consistency: "一致性检查", instantiation: "语义实例化", persistence: "结果保存", template_render: "模板填充",
};

interface RightPanelProps {
  templates: OutputTemplate[];
  activeTemplateId: string | null;
  onSelectTemplate: (id: string) => void;
  extractedJson: Record<string, unknown> | null;
  templatePreview: TemplatePreviewResponse | null;
  onApplyJsonEdit: (data: Record<string, unknown>) => Promise<void>;
  onExport: (format: "xlsx" | "docx" | "md" | "json") => void;
  debugMessage: ChatMessage | null;
  tab: ResultTab;
  onTab: (tab: ResultTab) => void;
  versions: SemanticVersion[];
  onVersion: (id: string | null) => void;
  onRevise: (id: string) => void;
  onVerify: (id: string) => Promise<SemanticModel["validation"]>;
  onProject: () => void;
  onOpenSource: (id: string) => void;
  onReview: (id: string, payload: ReviewRequest) => Promise<void>;
  onGraph: (id: string) => Promise<ProcessGraph>;
  progress: PipelineStage[];
  isRunning: boolean;
}

export function ReviewPill({ state }: { state: string }) {
  return <span className="review-pill" data-state={state}>{statusLabels[state] || state}</span>;
}

function location(cite: Evidence) {
  const loc = cite.location;
  const parts = [loc.sheet, loc.cell, loc.page != null ? `第 ${loc.page} 页` : null,
    loc.line != null ? `第 ${loc.line} 行` : null, loc.paragraph != null ? `段落 ${loc.paragraph}` : null,
    loc.message_id ? "用户消息" : null].filter(Boolean);
  return parts.join(" / ") || cite.fragment_id;
}

function Citations({ model, items, onSource }: {
  model: SemanticModel; items: Evidence[]; onSource: (id: string) => void;
}) {
  if (!items.length) return <p className="muted">当前事实没有可核验的逐字引文。</p>;
  return <div className="citation-list">
    {items.map((cite, index) => {
      const source = model.source_refs.find((entry) => entry.document_id === cite.document_id);
      return <figure key={`${cite.document_id}-${cite.fragment_id}-${index}`}>
        <blockquote>{cite.quote}</blockquote>
        <figcaption>
          {source?.kind === "file"
            ? <button type="button" className="text-action" onClick={() => onSource(cite.document_id)}>{source.name}</button>
            : source?.name || cite.document_id}
          <span>{location(cite)}</span>
          <span>{{ structured: "表格提取", llm: "模型候选", rule: "规则提取", manual: "人工确认", legacy: "历史来源" }[cite.method ?? "legacy"]}</span>
          <ReviewPill state={cite.status === "verified" ? "located" : cite.status} />
        </figcaption>
      </figure>;
    })}
  </div>;
}

function EntityDetail({ model, selected, onSelect, onSource }: {
  model: SemanticModel; selected: SemanticEntity; onSelect: (id: string) => void; onSource: (id: string) => void;
}) {
  const lookup = new Map(groups.flatMap(([key]) => model[key]).map((entity) => [entity.id, entity]));
  const relations = model.relations.filter((edge) => edge.source_id === selected.id || edge.target_id === selected.id);
  const fields = [
    ["实体标识", selected.id], ["语义类型", selected.concept], ["工序编号", selected.number],
    ["标准术语", selected.standard_term], ["映射依据", selected.mapping_basis],
    ["基础物料", selected.material_id ? lookup.get(selected.material_id)?.name : null],
    ["输入状态", selected.input_state_ids?.map((id) => lookup.get(id)?.name).filter(Boolean).join("、")],
    ["输出状态", selected.output_state_ids?.map((id) => lookup.get(id)?.name).filter(Boolean).join("、")],
    ["工序类型", selected.operation_type], ["产品图号", selected.part_number], ["原始能力描述", selected.raw_names?.join("；")], ["设备要求", selected.equipment_requirement],
    ["能力编码", selected.code], ["映射候选", selected.candidates?.join("、")], ["物料类型", selected.material_type],
    ["参数编码", selected.parameter_code], ["比较符", selected.comparator],
    ["数值", selected.value == null ? null : String(selected.value)],
    ["上下限", selected.lower != null || selected.upper != null ? `${selected.lower ?? "—"} – ${selected.upper ?? "—"}` : null],
    ["单位", selected.unit], ["计量基准", selected.basis], ["约束说明", selected.description],
  ].filter(([, value]) => value != null && value !== "");
  return <article className="entity-detail">
    <header><div><small>{selected.concept}</small><h3>{[selected.number, selected.name].filter(Boolean).join(" · ")}</h3></div>
      <div className="result-actions"><ReviewPill state={selected.status} />{selected.review === "confirmed" && <ReviewPill state="confirmed" />}</div></header>
    <dl className="fact-grid">
      {fields.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
    </dl>
    <h4>原文证据</h4>
    <Citations model={model} items={[...selected.evidence, ...(selected.equipment_evidence ?? [])]} onSource={onSource} />
    {!!selected.review_evidence?.length && <><h4>人工审核依据</h4><Citations model={model} items={selected.review_evidence} onSource={onSource} /></>}
    {relations.length > 0 && <><h4>关联事实</h4><div className="relation-list">
      {relations.map((edge) => {
        const otherId = edge.source_id === selected.id ? edge.target_id : edge.source_id;
        const other = lookup.get(otherId);
        return <details key={edge.id}>
          <summary><span>{edge.source_id === selected.id ? "→" : "←"} {relationLabel(edge)} · {other?.name ?? (otherId === "line" ? "整线" : otherId)}</span>
            <ReviewPill state={edge.status} /></summary>
          {other && <button className="text-action" type="button" onClick={() => onSelect(other.id)}>查看 {other.name}</button>}
          {edge.asserted_as === "inferred" && <p className="muted">候选推断 · 待人工确认</p>}
          {edge.quantity && <p>组成数量 {edge.quantity.value} {edge.quantity.unit} {edge.quantity.basis && `· ${edge.quantity.basis}`}</p>}
          {edge.review === "confirmed" && <ReviewPill state="confirmed" />}
          {edge.condition && <p>{edge.condition}</p>}
          {edge.max_iterations != null && <p>返工上限 {edge.max_iterations} 次</p>}
          <Citations model={model} items={[...edge.evidence, ...(edge.review_evidence ?? [])]} onSource={onSource} />
        </details>;
      })}
    </div></>}
  </article>;
}

function saveJson(value: unknown, filename: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

function CopyButton({ value }: { value: string }) {
  const [response, setResponse] = useState("");
  return <Button size="small" icon={<Copy size={14} />} onClick={async () => {
    try { await navigator.clipboard.writeText(value); setResponse("已复制"); }
    catch { setResponse("剪贴板不可用"); }
  }}>{response || "复制"}</Button>;
}

function DebugView({ message }: { message: ChatMessage | null }) {
  const debug = message?.debug_info;
  if (!debug) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="选择一条运行记录查看诊断" />;
  const raw = [
    ["系统 Prompt", debug.system_prompt], ["用户与文档上下文", debug.user_content],
    ["原始响应", debug.raw_response], ["请求详情", debug.provider_attempts?.length ? JSON.stringify(debug.provider_attempts, null, 2) : ""],
  ];
  return <div className="result-stack">
    <div className="result-heading"><div><h3>执行追踪</h3><p>{debug.model_used || debug.requested_model || "本次没有模型响应"}</p></div>
      <Button size="small" icon={<ArrowDownToLine size={15} />} onClick={() => saveJson(debug, "assessment-debug.json")}>导出</Button></div>
    {debug.error_message && <Alert type="error" showIcon title={debug.error_code ?? "执行错误"} description={debug.error_message} />}
    {debug.activation_note && <Alert type="warning" title={debug.activation_note} />}
    <div className="runtime-metrics">
      <span>请求 <b>{debug.request_count ?? "未记录"}</b>{debug.request_budget != null && ` / ${debug.request_budget}`}</span>
      <span>Token <b>{debug.usage?.total_tokens ?? "未返回"}</b></span>
      {debug.finish_reason && <span>结束原因 <b>{debug.finish_reason}</b></span>}
      <span>阶段耗时 <b>{debug.duration_ms != null ? `${debug.duration_ms} ms` : "未记录"}</b></span>
    </div>
    {!!debug.batches?.length && <details className="result-disclosure" open><summary>抽取批次 · {debug.batches.length}</summary>
      <div className="batch-list">{debug.batches.map((batch) => <article key={batch.id}>
        <div><strong>{batch.id}</strong> · {batch.scope}<br /><small>{batch.input_characters} 字符 · {batch.accepted_rows} 条候选 {batch.reason && `· ${batch.reason}`}</small></div>
        <ReviewPill state={batch.status} />
      </article>)}</div></details>}
    <ol className="run-stages">
      {debug.pipeline_stages?.map((stage, idx) => <li key={`${stage.stage}-${idx}`}>
        <details>
          <summary><span>{stageLabels[stage.stage] ?? stage.stage}</span>
            <small>{stage.duration_ms != null && `${stage.duration_ms} ms`}</small><ReviewPill state={stage.status} /></summary>
          <p>{stage.summary}</p>
          {(stage.started_at || stage.ended_at) && <p className="muted">
            {[stage.started_at, stage.ended_at].filter(Boolean).map((time) => new Date(time!).toLocaleTimeString()).join(" → ")}
          </p>}
          {stage.error_code && <code>{stage.error_code}</code>}
        </details>
      </li>)}
    </ol>
    {!debug.pipeline_stages?.length && <p className="muted">旧版运行只记录了模型响应和校验消息。</p>}
    {!!debug.validation_errors?.length && <details className="result-disclosure"><summary>校验诊断（{debug.validation_errors.length}）</summary>
      <ul>{debug.validation_errors.map((error, idx) => <li key={idx}>{error}</li>)}</ul>
    </details>}
    {raw.map(([title, value]) => value && <details className="result-disclosure" key={title}><summary>{title}</summary>
      <CopyButton value={value} /><pre className="json-code">{value}</pre>
    </details>)}
  </div>;
}

function Preview({ value, model, onSource }: {
  value: TemplatePreviewResponse | null; model: SemanticModel | null; onSource: (id: string) => void;
}) {
  const [row, setRow] = useState<number | null>(null);
  useEffect(() => setRow(null), [value]);
  if (!value) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="选择模板并生成结果后查看填充" />;
  if (value.error) return <Alert type="error" showIcon title={value.error} />;
  const entity = model && row != null
    ? groups.flatMap(([key]) => model[key]).find((item) => item.id === value.row_ids?.[row]) : null;
  return <div className="result-stack">
    {!!value.mapping_issues?.length && <Alert type="warning" showIcon title="部分映射需检查"
      description={value.mapping_issues.join(" · ")} />}
    {value.table_headers.length > 0 ? <>
      <div className="table-scroll" tabIndex={0}>
        <table className="preview-table">
          <thead><tr><th scope="col"><span className="sr-only">行详情</span></th>
            {value.table_headers.map((heading, index) => <th key={index} scope="col">{heading}</th>)}</tr></thead>
          <tbody>{value.table_rows.map((line, index) => <tr key={index}>
            <td><button type="button" className="text-action" aria-label={`查看第 ${index + 1} 行详情`} aria-pressed={row === index} onClick={() => setRow(index)}>
              <Expand size={15} /></button></td>
            {line.map((cell, col) => <td key={col} data-state={value.cell_states?.[index]?.[col]}><span title={cell}>{cell}</span></td>)}
          </tr>)}</tbody>
        </table>
      </div>
      {row != null && value.table_rows[row] && <section className="entity-detail">
        <header><h3>第 {row + 1} 行</h3><button type="button" className="text-action" onClick={() => setRow(null)}>收起</button></header>
        <dl className="fact-grid">{value.table_rows[row].map((cell, index) => <div key={index}>
          <dt>{value.table_headers[index]}</dt><dd>{cell}</dd></div>)}</dl>
        {model && entity && <><h4>原文位置与引文</h4>
          <Citations model={model} items={[...entity.evidence, ...(entity.equipment_evidence ?? [])]} onSource={onSource} /></>}
      </section>}
    </> : value.format === "html" ? <HtmlPreview value={value.rendered_text} /> : <div className="markdown-preview"><ReactMarkdown remarkPlugins={[remarkGfm]}>{value.rendered_text}</ReactMarkdown></div>}
  </div>;
}

export function RightPanel(props: RightPanelProps) {
  const model = semanticFrom(props.extractedJson);
  const [ontologyOpen, setOntologyOpen] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [group, setGroup] = useState<SemanticGroup>("operations");
  const [selection, setSelection] = useState<string | null>(null);
  const [topologySelection, setTopologySelection] = useState<string | null>(null);
  const [topologyPositions, setTopologyPositions] = useState(() => new Map<string, ReadonlyMap<string, Point>>());
  const [scope, setScope] = useState<"list" | "graph">("list");
  const [edit, setEdit] = useState(false);
  const [draft, setDraft] = useState("");
  const [diagnostic, setDiagnostic] = useState("");
  const [verification, setVerification] = useState<SemanticModel["validation"] | null>(null);
  const [verifying, setVerifying] = useState(false);
  const json = useMemo(() => JSON.stringify(model || props.extractedJson, null, 2), [model, props.extractedJson]);
  useEffect(() => {
    setSelection(null); setTopologySelection(null); setEdit(false); setDiagnostic(""); setVerification(null);
  }, [model?.run_id, props.extractedJson]);
  const currentSelection = props.tab === "topology" ? topologySelection : selection;
  const selected = model && (groups.flatMap(([key]) => model[key]).find((entity) => entity.id === currentSelection)
    ?? (currentSelection === "line" ? {
      id: "line", name: "整线", concept: "ProductionLine", status: "verified" as const,
      evidence: model.constraints.filter((item) => item.target_id === "line").flatMap((item) => item.evidence),
    } : null));
  const select = (id: string) => {
    setSelection(id);
    const match = model && groups.find(([key]) => model[key].some((entity) => entity.id === id));
    if (match) setGroup(match[0]);
  };
  const showVersion = !!props.versions.length;
  const canProject = !model && Boolean(props.debugMessage?.extracted_json);
  const status: ReviewState | undefined = verification?.status ?? model?.validation.status;
  const issues = verification?.issues ?? model?.validation.issues ?? [];
  const showDetail = selected && model && <EntityDetail model={model} selected={selected}
    onSelect={props.tab === "topology" ? setTopologySelection : select} onSource={props.onOpenSource} />;

  return <section className="result-panel" aria-label="结构化结果工作区">
    <header className="result-header">
      <div className="result-title"><Layers3 size={17} /><h2>结果工作区</h2>{status && <ReviewPill state={status} />}</div>
      {showVersion && <Select aria-label="结果版本" size="small" virtual={false} value={model?.run_id ?? "active"}
        popupMatchSelectWidth={false} onChange={(id) => props.onVersion(id === "active" ? null : id)}
        options={[{ value: "active", label: "当前结果" }, ...props.versions.map((version) => ({
          value: version.id, label: `${new Date(version.created_at).toLocaleString("zh-CN")} · ${version.id.slice(0, 6)}`,
        }))]} />}
    </header>
    <div className="result-tabs" role="tablist" aria-label="结果视图">
      {tabs.map(({ id, name, icon: Icon }, index) => <button type="button" role="tab" id={`result-tab-${id}`}
        aria-selected={props.tab === id} aria-controls="result-contents" tabIndex={props.tab === id ? 0 : -1}
        key={id} onClick={() => props.onTab(id)} onKeyDown={(event) => {
          if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
          event.preventDefault();
          const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 :
            (index + (event.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length;
          props.onTab(tabs[next].id);
          document.getElementById(`result-tab-${tabs[next].id}`)?.focus();
        }}><Icon size={15} /><span>{name}</span></button>)}
    </div>
    <div className="result-body" data-view={props.tab} role="tabpanel" id="result-contents" aria-labelledby={`result-tab-${props.tab}`}>
      {props.tab === "overview" && (model ? <div className="result-stack">
        <div className="result-heading"><div><h3>制造需求概览</h3>
          <p>Schema {model.schema_version} · 本体 {model.ontology_version} · 词表 {model.vocabulary_version}</p></div>
        </div>
        {model.metadata && <p className="muted">覆盖状态 <ReviewPill state={model.metadata.extraction_status} /></p>}
        {!!model.modeling_intents?.length && <section className="intent-list"><h4>后续建模意图</h4>
          <ul>{model.modeling_intents.map((item) => <li key={item.id}>{item.description}</li>)}</ul>
        </section>}
        <div className="semantic-stats">
          {groups.map(([id, label]) => <button type="button" key={id} onClick={() => {
            setGroup(id); setScope("list"); setSelection(null); props.onTab("semantics");
          }}><span>{label}</span><strong>{model[id].length}</strong></button>)}
          <button type="button" onClick={() => props.onTab("semantics")}><span>语义关系</span><strong>{model.relations.length}</strong></button>
          <button type="button" onClick={() => document.getElementById("semantic-issues")?.scrollIntoView({ block: "nearest" })}>
            <span>待确认与诊断</span><strong>{issues.length}</strong></button>
        </div>
        <div className="result-actions">
          <Button size="small" icon={<FlaskConical size={15} />} loading={verifying} onClick={async () => {
            setVerifying(true); setDiagnostic("");
            try { setVerification(await props.onVerify(model.run_id)); }
            catch (err) { setDiagnostic(String(err)); }
            finally { setVerifying(false); }
          }}>重新核验来源</Button>
          <Button size="small" onClick={() => props.onRevise(model.run_id)}>基于此版本修订</Button>
          <Button size="small" onClick={() => setReviewOpen(true)}>人工审核</Button>
        </div>
        {diagnostic && <Alert type="error" title={diagnostic} />}
        <section id="semantic-issues" className="issue-list"><h4>待确认与诊断</h4>
          {issues.length ? issues.map((issue, idx) => <article key={`${issue.path}-${idx}`}>
            <ReviewPill state={issue.status} /><div><p>{issue.message}</p><code>{issue.code} · {issue.path}</code></div>
          </article>) : <p className="muted">当前核验未发现问题。</p>}
        </section>
      </div> : <div className="result-placeholder"><Empty image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={props.extractedJson ? "当前会话包含旧版抽取结果" : "生成一次解析结果后，查看制造需求和来源"} />
        {canProject && <Button onClick={props.onProject}>从历史抽取生成语义版本</Button>}</div>)}
      {props.tab === "topology" && (model ? <div className="topology-stack">
        <div className="result-heading"><div><h3>工艺拓扑</h3><p>查看直接转序、前置依赖与条件关系</p></div>
          <Button size="small" icon={<ArrowDownToLine size={15} />} onClick={async () => {
            try { saveJson(model.process_graph ?? await props.onGraph(model.run_id), `process-graph-${model.run_id}.json`); }
            catch (err) { setDiagnostic(String(err)); }
          }}>导出图 JSON</Button></div>
        {diagnostic && <Alert type="error" title={diagnostic} />}
        <SemanticGraph key={model.run_id} model={model} selected={topologySelection} onSelect={setTopologySelection}
          topology={{
            positions: topologyPositions.get(model.run_id) ?? emptyTopologyPositions,
            onMove: (id, point) => setTopologyPositions((previous) => {
              const updated = new Map(previous);
              const positions = new Map(previous.get(model.run_id));
              positions.set(id, point);
              updated.set(model.run_id, positions);
              return updated;
            }),
            detail: showDetail,
            onCloseDetail: () => setTopologySelection(null),
          }} />
      </div> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前没有语义版本可用于绘制拓扑" />)}
      {props.tab === "semantics" && (model ? <div className="result-stack">
        <div className="result-heading"><h3>领域语义</h3><div className="result-actions">
          <Button size="small" onClick={() => setOntologyOpen(true)}>浏览本体</Button>
          <Button size="small" type={scope === "list" ? "primary" : "default"} onClick={() => setScope("list")}>实体</Button>
          <Button size="small" type={scope === "graph" ? "primary" : "default"} onClick={() => setScope("graph")}>关系图</Button>
        </div></div>
        {scope === "graph" ? <SemanticGraph key="semantic" model={model} selected={selection} onSelect={select} /> :
          <><Select size="small" aria-label="实体类型" value={group} onChange={(id) => { setGroup(id); setSelection(null); }}
              options={groups.map(([value, label]) => ({ value, label: `${label} · ${model[value].length}` }))} />
            <div className="entity-list">
              {model[group].map((item) => <button type="button" key={item.id} aria-pressed={selection === item.id} onClick={() => select(item.id)}>
                <span>{[item.number, item.name].filter(Boolean).join(" · ")}</span><ReviewPill state={item.status} />
              </button>)}
              {model[group].length === 0 && <p className="muted">当前版本没有此类实体。</p>}
            </div></>}
        {showDetail}
      </div> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前没有统一语义模型" />)}
      {props.tab === "json" && (props.extractedJson ? <div className="result-stack">
        <div className="result-heading"><div><h3>结构化数据</h3><p>{model ? `内容摘要 ${model.content_digest.slice(0, 16)}` : "旧版抽取 JSON"}</p></div>
          <div className="result-actions"><CopyButton value={json} />
          <Button size="small" icon={<ArrowDownToLine size={15} />} onClick={() => props.onExport("json")}>JSON</Button>
          {!model && <Button size="small" onClick={() => { setDraft(json); setEdit((value) => !value); }}>编辑</Button>}</div>
        </div>
        {edit ? <><textarea aria-label="编辑抽取 JSON" className="json-code json-editor" value={draft} onChange={(event) => setDraft(event.target.value)} />
          <Button size="small" type="primary" onClick={async () => {
            try {
              const data = JSON.parse(draft);
              if (!data || Array.isArray(data) || typeof data !== "object") throw new Error("需要一个 JSON 对象");
              await props.onApplyJsonEdit(data); setEdit(false);
            } catch (err) { setDiagnostic(String(err)); }
          }}>保存</Button>{diagnostic && <Alert type="error" title={diagnostic} />}</> : <pre className="json-code">{json}</pre>}
      </div> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无结构化输出" />)}
      {props.tab === "preview" && <div className="result-stack">
        <div className="result-heading"><div><h3>输出模板</h3><p>切换模板即时填充当前版本</p></div></div>
        <div className="result-actions"><Select aria-label="输出模板" virtual={false} value={props.activeTemplateId ?? undefined} size="small"
          onChange={props.onSelectTemplate} options={props.templates.map((template) => ({ value: template.id, label: `${template.name} · v${template.version}` }))} />
          {(["xlsx", "md", "docx"] as const).map((format) => <Button key={format} size="small" disabled={!props.extractedJson}
            onClick={() => props.onExport(format)}>{format.toUpperCase()}</Button>)}
        </div>
        <Preview value={props.templatePreview} model={model} onSource={props.onOpenSource} />
      </div>}
      {props.tab === "debug" && (props.isRunning ? <RunProgress steps={props.progress} /> : <DebugView message={props.debugMessage} />)}
      {ontologyOpen && <OntologyBrowser version={model?.ontology_version} onClose={() => setOntologyOpen(false)} />}
      {reviewOpen && model && <ReviewDialog model={model} onClose={() => setReviewOpen(false)}
        onSubmit={(payload) => props.onReview(model.run_id, payload)} />}
    </div>
  </section>;
}
