import { useEffect, useMemo, useState } from "react";
import { Alert, Button, Dropdown, Empty, Select } from "antd";
import { ArrowDownToLine, Braces, CircleAlert, Copy, Ellipsis, Expand, FlaskConical, GitBranch, Layers3, LayoutGrid, PackageCheck, Sheet, Terminal } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type {
  ChatMessage, OutputTemplate, ReviewState,
  SemanticModel, SemanticVersion, TemplatePreviewResponse, PipelineStage, ProcessGraph, ReviewRequest, ContractKind, Evidence,
} from "../types";
import { semanticFrom } from "../types";
import { api } from "../api/client";
import { HtmlPreview } from "./HtmlPreview";
import { ProcessTopology } from "./ProcessTopology";
import { entitiesInGroup, entityTitle, relationTitle, semanticEntities } from "./semanticPresentation";
import { Citations, EntityDetail, evidenceLocation, ReviewPill, statusLabels } from "./SemanticDetails";
import { SemanticWorkspace } from "./SemanticWorkspace";
import type { EntityGroup } from "./SemanticWorkspace";
import { OntologyBrowser, ReviewDialog, RunProgress, toolNames } from "./RequirementTools";
import type { Point } from "./useTopologyViewport";
import { ContractBrowser } from "./ContractBrowser";
import { constraintValue, HandoffWorkspace, TaskSummary } from "./HandoffWorkspace";

const tabs = [
  { id: "overview", name: "概览", icon: LayoutGrid },
  { id: "topology", name: "工艺拓扑", icon: GitBranch },
  { id: "semantics", name: "语义模型", icon: Layers3 },
  { id: "json", name: "结构化数据", icon: Braces },
  { id: "issues", name: "校验问题", icon: CircleAlert },
  { id: "handoff", name: "下游交接", icon: PackageCheck },
] as const;
const secondaryViews = [
  { id: "preview", name: "模板预览", icon: Sheet },
  { id: "debug", name: "Debug", icon: Terminal },
] as const;
export type ResultTab = (typeof tabs)[number]["id"] | (typeof secondaryViews)[number]["id"];
const emptyTopologyPositions: ReadonlyMap<string, Point> = new Map();

const stageLabels: Record<string, string> = {
  ...toolNames,
  file_parsing: "输入文件解析", input_selection: "来源选择", source_preprocessing: "来源预处理",
  deterministic_extraction: "确定性抽取", llm_extraction: "LLM 信息抽取", schema_validation: "候选 Schema 验证",
  llm_repair: "候选修复", ontology_mapping: "本体与术语映射", llm_disambiguation: "模型辅助消歧",
  consistency: "一致性检查", instantiation: "语义实例化", persistence: "结果保存", template_render: "模板填充",
};

interface RightPanelProps {
  sessionId: string | null;
  templates: OutputTemplate[];
  activeTemplateId: string | null;
  onSelectTemplate: (id: string) => void;
  extractedJson: Record<string, unknown> | null;
  templatePreview: TemplatePreviewResponse | null;
  onApplyJsonEdit: (data: Record<string, unknown>) => Promise<void>;
  onExport: (format: "xlsx" | "docx" | "md") => void;
  debugMessage: ChatMessage | null;
  tab: ResultTab;
  onTab: (tab: ResultTab) => void;
  versions: SemanticVersion[];
  onVersion: (id: string | null) => void;
  onRevise: (id: string) => void;
  onVerify: (id: string) => Promise<SemanticModel["validation"]>;
  onProject: () => void;
  onOpenSource: (id: string, cite?: Evidence) => void;
  onReview: (id: string, payload: ReviewRequest) => Promise<void>;
  onStartMatching?: (sessionId: string, versionId: string) => void;
  progress: PipelineStage[];
  isRunning: boolean;
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
      {debug.resumed_from_version_id && <span>续跑自版本 <b title={debug.resumed_from_version_id}>{debug.resumed_from_version_id.slice(0, 8)}</b></span>}
      <span>请求 <b>{debug.request_count ?? "未记录"}</b>{debug.request_budget != null && ` / ${debug.request_budget}`}</span>
      <span>Token <b>{debug.usage?.total_tokens ?? "未返回"}</b></span>
      {debug.max_tokens != null && <span>配置上限 <b>{debug.max_tokens.toLocaleString()}</b></span>}
      {debug.batch_max_tokens != null && <span>单批发送上限 <b>{debug.batch_max_tokens.toLocaleString()}</b></span>}
      {debug.finish_reason && <span>结束原因 <b>{debug.finish_reason}</b></span>}
      <span>阶段耗时 <b>{debug.duration_ms != null ? `${debug.duration_ms} ms` : "未记录"}</b></span>
    </div>
    {!!debug.provider_attempts?.length && <details className="result-disclosure" open><summary>模型调用 · {debug.provider_attempts.length} 项</summary>
      <div className="batch-list">{debug.provider_attempts.map((attempt, index) => <article key={index}>
        <div><strong>{attempt.batch_id ?? `第 ${index + 1} 项`}</strong>
          {attempt.stage && ` · ${stageLabels[attempt.stage] ?? attempt.stage}`}<br />
          <small>{[
            (attempt.max_tokens ?? debug.batch_max_tokens) != null && `发送上限 ${(attempt.max_tokens ?? debug.batch_max_tokens)?.toLocaleString()}`,
            attempt.usage?.prompt_tokens != null && `输入 ${attempt.usage.prompt_tokens.toLocaleString()}`,
            attempt.usage?.completion_tokens != null && `输出 ${attempt.usage.completion_tokens.toLocaleString()}`,
            attempt.usage?.reasoning_tokens != null && `推理 ${attempt.usage.reasoning_tokens.toLocaleString()}`,
            attempt.thinking === "disabled" && `已请求关闭思考${attempt.thinking_parameter ? `（${attempt.thinking_parameter}）` : ""}`,
            attempt.thinking_result === "reasoning_returned" && "服务仍报告推理",
            attempt.thinking_result === "unverified" && "服务未报告推理用量",
            attempt.thinking_result === "rejected" && "关闭思考参数被拒绝",
            attempt.finish_reason && `结束原因 ${attempt.finish_reason}`,
            !attempt.finish_reason && attempt.error_code,
            attempt.request_count != null && `${attempt.request_count} 次请求`,
          ].filter(Boolean).join(" · ")}</small></div>
      </article>)}</div></details>}
    {!!debug.batches?.length && <details className="result-disclosure" open><summary>抽取批次 · {debug.batches.length}</summary>
      <div className="batch-list">{debug.batches.map((batch) => <article key={batch.id}>
        <div><strong>{batch.id}</strong> · {batch.scope}<br /><small>{batch.input_characters} 字符 · {batch.accepted_rows} 条候选 {batch.reason && `· ${batchReasons[batch.reason] ?? batch.reason}`}</small></div>
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
    ? semanticEntities(model).get(value.row_ids?.[row] ?? "") : null;
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

const issueTips: Record<string, string> = {
  conditional_route_missing: "补充异常产出的下一工序、转序条件和来源。",
  merge_predecessors_pending: "在工艺资料中列出参与汇合的各直接前驱。",
  graph_isolated: "核对该工序是否独立，或补充其明确的前置依赖。",
  capability_unmapped: "核对原始能力描述，在人工审核中选择受控编码。",
  constraint_conflict: "核对各来源的适用范围、单位和版本，确认采用的边界。",
  measurement_unparsed: "补充清晰的参数名称、比较符、数值及单位。",
  component_quantity_conflict: "核对产品组成与计量基准，再提交准确的组件数量。",
  relation_reference: "核对工序、物料或能力是否已定义，并检查关系两端的类型。",
  source_not_found: "恢复缺失的原始资料并重新核验。",
};
const issueStates = ["invalid", "conflict", "pending", "unprocessed"] as const;
const batchReasons: Record<string, string> = {
  offline: "离线运行已跳过剩余自然语言",
  unconfigured: "模型连接尚未配置",
  review_required: "需要先明确修订对象",
  request_budget_exhausted: "本轮模型请求预算已用完，本批尚未执行",
  output_truncated: "本批模型输出达到长度限制",
  reasoning_budget_exhausted: "推理耗尽本批输出额度，未生成 JSON",
  thinking_parameter_rejected: "模型服务拒绝了关闭思考参数",
  semantic_evidence_pending: "部分候选缺少可核验的原文依据",
  service_unavailable: "模型服务暂不可用",
};
const batchTips: Record<string, string> = {
  output_truncated: "检查实际发送的单批输出上限。调整后可继续解析未完成批次。",
  reasoning_budget_exhausted: "在智能体设置中探测关闭思考参数，或提高单批输出上限后继续解析。",
  thinking_parameter_rejected: "使用 JSON 探测核对网关支持的参数，保存后继续解析。",
  request_budget_exhausted: "增加单次运行请求预算，然后继续解析未完成批次。",
};

function IssuesView({ model, issues, onLocate, onSource }: {
  model: SemanticModel | null; issues: SemanticModel["validation"]["issues"];
  onLocate: (id: string) => void; onSource: (id: string) => void;
}) {
  const [filter, setFilter] = useState("");
  const entities = model ? semanticEntities(model) : new Map();
  const relations = new Map(model?.relations.map((edge) => [edge.id, edge]));
  const visible = (filter ? issues.filter((issue) => issue.status === filter) : issues)
    .slice().sort((a, b) => issueStates.findIndex((key) => key === a.status) - issueStates.findIndex((key) => key === b.status));
  return <div className="result-stack">
    <div className="result-heading"><div><h3>校验问题</h3><p>{model ? `${issues.length} 项诊断 · 来自当前语义版本` : "运行解析后查看校验问题"}</p></div>
      {!!issues.length && <Select size="small" aria-label="筛选校验问题" value={filter} onChange={setFilter}
        options={[{ value: "", label: "全部状态" }, ...issueStates.map((state) => ({
          value: state, label: `${statusLabels[state]} · ${issues.filter((issue) => issue.status === state).length}`,
        }))]} />}</div>
    {!visible.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={model ? "当前筛选下没有校验问题" : "暂无语义版本"} />}
    <div className="validation-list">{visible.map((issue, index) => {
      const entity = entities.get(issue.path);
      const edge = relations.get(issue.path);
      const batch = model?.metadata?.batches.find((item) => item.id === issue.path);
      const evidence = edge?.evidence ?? entity?.evidence ?? [];
      const cite = evidence[0];
      const name = cite && model?.source_refs.find((source) => source.document_id === cite.document_id)?.name;
      const tip = (batch?.reason ? batchTips[batch.reason] : undefined) ?? issueTips[issue.code] ?? (issue.code.startsWith("batch_")
        ? "检查资料及模型连接，然后重跑对应解析。"
        : "对照原文补充或确认受影响的字段，然后重新核验。");
      return <article key={`${issue.path}-${issue.code}-${index}`}>
        <div className="validation-item-head"><ReviewPill state={issue.status} />
          {(entity || edge) && <button type="button" className="text-action" onClick={() => onLocate(issue.path)}>定位事实</button>}</div>
        <strong>{edge ? relationTitle(edge, entities) : entity ? entityTitle(entity) : batch?.scope ?? issue.path}</strong>
        {cite && <small>{[name, evidenceLocation(cite)].filter(Boolean).join(" · ")}</small>}
        <p>{(batch?.reason && batchReasons[batch.reason]) || issue.message}</p><p className="muted">{tip}</p>
        {model && !!evidence.length && <details><summary>原文证据</summary><Citations model={model} items={evidence} onSource={onSource} /></details>}
      </article>;
    })}</div>
  </div>;
}

export function RightPanel(props: RightPanelProps) {
  const model = useMemo(() => semanticFrom(props.extractedJson), [props.extractedJson]);
  const [ontologyOpen, setOntologyOpen] = useState(false);
  const [schemaKind, setSchemaKind] = useState<ContractKind | null>(null);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [group, setGroup] = useState<EntityGroup>("all");
  const [locatePath, setLocatePath] = useState<string | null>(null);
  const [topologySelection, setTopologySelection] = useState<string | null>(null);
  const [topologyPositions, setTopologyPositions] = useState(() => new Map<string, ReadonlyMap<string, Point>>());
  const [historicalGraph, setHistoricalGraph] = useState<{ version: string; graph: ProcessGraph }>();
  const [graphError, setGraphError] = useState("");
  const [edit, setEdit] = useState(false);
  const [draft, setDraft] = useState("");
  const [diagnostic, setDiagnostic] = useState("");
  const [verification, setVerification] = useState<SemanticModel["validation"] | null>(null);
  const [verifying, setVerifying] = useState(false);
  const json = useMemo(() => JSON.stringify(model || props.extractedJson, null, 2), [model, props.extractedJson]);
  useEffect(() => {
    setLocatePath(null); setTopologySelection(null); setEdit(false); setDiagnostic(""); setVerification(null);
  }, [model?.run_id, props.extractedJson]);
  useEffect(() => {
    if (props.tab !== "topology" || !props.sessionId || !model || model.process_graph) return;
    const request = new AbortController();
    setGraphError("");
    api.getProcessGraph(props.sessionId, model.run_id, request.signal)
      .then((graph) => setHistoricalGraph({ version: model.run_id, graph }))
      .catch((reason: unknown) => { if (!request.signal.aborted) setGraphError(String(reason)); });
    return () => request.abort();
  }, [model, props.tab, props.sessionId]);
  const topologyModel = useMemo(() => model?.process_graph ? model : model && historicalGraph?.version === model.run_id
    ? { ...model, process_graph: historicalGraph.graph } : null, [model, historicalGraph]);
  const selected = model?.operations.find((entity) => entity.id === topologySelection);
  const showVersion = !!props.versions.length;
  const canProject = !model && Boolean(props.debugMessage?.extracted_json);
  const status: ReviewState | undefined = verification?.status ?? model?.validation.status;
  const issues = verification?.issues ?? model?.validation.issues ?? [];
  const openIssues = () => props.onTab("issues");
  const extraction = model?.metadata?.extraction_status ?? (props.debugMessage?.status === "error" ? "failed" : undefined);
  const summaryEntities = model ? semanticEntities(model) : new Map();
  const quantifiedComponents = model?.relations.filter((edge) => edge.type === "hasComponent" && edge.quantity) ?? [];
  const lineLimits = model?.constraints.filter((item) => item.target_id === "line") ?? [];
  const primaryTab = tabs.some(({ id }) => id === props.tab);
  const locate = (id: string) => { setLocatePath(id); props.onTab("semantics"); };

  return <section className="result-panel" aria-label="结构化结果工作区">
    <header className="result-header">
      <div className="result-title"><Layers3 size={18} /><h2>解析结果</h2>{status && <button type="button" className="result-validation-button"
        aria-label={`${statusLabels[status]}，查看校验问题（${issues.length}）`} onClick={openIssues}><ReviewPill state={status} /></button>}</div>
      <div className="result-header-actions">
      {showVersion && <Select aria-label="结果版本" size="small" virtual={false} value={model?.run_id ?? "active"}
        popupMatchSelectWidth={false} onChange={(id) => props.onVersion(id === "active" ? null : id)}
        options={[{ value: "active", label: "当前结果" }, ...props.versions.map((version) => ({
          value: version.id, label: `${new Date(version.created_at).toLocaleString("zh-CN")} · ${version.id.slice(0, 6)}`,
        }))]} />}
      <Dropdown trigger={["click"]} menu={{ items: [...secondaryViews.map(({ id, name, icon: Icon }) => ({
        key: id, label: name, icon: <Icon size={15} />,
      })), { key: "schemas", label: "数据契约", icon: <Braces size={15} /> }],
      selectedKeys: primaryTab ? [] : [props.tab],
      onClick: ({ key }) => key === "schemas" ? setSchemaKind("formal") : props.onTab(key as ResultTab) }}>
        <Button size="small" type="text" aria-label="更多结果视图" id="result-more" icon={<Ellipsis size={19} />} />
      </Dropdown></div>
    </header>
    <div className="result-tabs" role="tablist" aria-label="结果视图">
      {tabs.map(({ id, name, icon: Icon }, index) => <button type="button" role="tab" id={`result-tab-${id}`}
        aria-selected={props.tab === id} aria-controls="result-contents" tabIndex={props.tab === id || (!primaryTab && index === 0) ? 0 : -1}
        key={id} onClick={() => props.onTab(id)} onKeyDown={(event) => {
          if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
          event.preventDefault();
          const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 :
            (index + (event.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length;
          props.onTab(tabs[next].id);
          document.getElementById(`result-tab-${tabs[next].id}`)?.focus();
        }}><Icon size={15} /><span>{name}</span>{id === "issues" && issues.length > 0 && <small>{issues.length}</small>}</button>)}
    </div>
    <div className="result-body" data-view={props.tab} role="tabpanel" id="result-contents" aria-labelledby={primaryTab ? `result-tab-${props.tab}` : "result-more"}>
      {props.tab === "overview" && (model ? <div className="result-stack">
        <div className="result-heading"><div><h3>制造需求概览</h3>
          <p>Schema {model.schema_version} · 本体 {model.ontology_version} · 词表 {model.vocabulary_version}</p></div>
          <Button size="small" onClick={() => setSchemaKind("formal")}>数据契约</Button>
        </div>
        {extraction && <div className="result-coverage"><ReviewPill state={extraction} /><span>{model.source_refs.length} 项来源</span></div>}
        {(quantifiedComponents.length > 0 || lineLimits.length > 0) && <section className="requirement-summary">
          <h4>关键需求</h4>
          {quantifiedComponents.map((edge) => <button type="button" key={edge.id} onClick={() => locate(edge.id)}>
            <span>{summaryEntities.get(edge.source_id)?.name} · {summaryEntities.get(edge.target_id)?.name}</span>
            <strong>{edge.quantity?.value} {edge.quantity?.unit}</strong><ReviewPill state={edge.status} /></button>)}
          {lineLimits.map((item) => <button type="button" key={item.id} onClick={() => locate(item.id)}>
            <span>整线 · {item.name}</span><strong>{constraintValue(item)}</strong><ReviewPill state={item.status} /></button>)}
        </section>}
        <TaskSummary model={model} task={model.modeling_task} onLocate={locate} onSource={props.onOpenSource} />
        {!!model.modeling_intents?.length && <section className="intent-list"><h4>建模目标</h4>
          <ul>{model.modeling_intents.map((item) => <li key={item.id}><button type="button" className="text-action" onClick={() => locate(item.id)}>{item.description}</button></li>)}</ul>
        </section>}
        <div className="semantic-stats">
          {([["operations", "工序"], ["materials", "物料"], ["capabilities", "能力"], ["constraints", "约束"]] as const)
            .map(([id, label]) => <button type="button" key={id} onClick={() => {
              setGroup(id); props.onTab("semantics");
            }}><span>{label}</span><strong>{entitiesInGroup(model, id).length}</strong></button>)}
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
      </div> : <div className="result-placeholder"><Empty image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={props.isRunning ? "正在生成解析结果" : props.extractedJson ? "当前会话包含旧版抽取结果"
          : extraction === "failed" ? "最近一次解析失败，请查看该轮对话" : "等待工艺解析结果"} />
        {canProject && <Button onClick={props.onProject}>从历史抽取生成语义版本</Button>}</div>)}
      {props.tab === "topology" && (topologyModel ? <div className="topology-stack">
        <div className="result-heading"><div><h3>工艺拓扑</h3><p>查看直接转序、前置依赖与条件关系</p></div>
          <Button size="small" icon={<ArrowDownToLine size={15} />} onClick={() =>
            saveJson(topologyModel.process_graph, `process-graph-${topologyModel.run_id}.json`)}>导出图 JSON</Button></div>
        <ProcessTopology key={topologyModel.run_id} model={topologyModel} selected={topologySelection} onSelect={setTopologySelection}
          positions={topologyPositions.get(topologyModel.run_id) ?? emptyTopologyPositions}
          onResetLayout={() => setTopologyPositions((previous) => {
            const updated = new Map(previous);
            updated.delete(topologyModel.run_id);
            return updated;
          })}
          onMove={(id, point) => setTopologyPositions((previous) => {
            const updated = new Map(previous);
            const positions = new Map(previous.get(topologyModel.run_id));
            positions.set(id, point);
            updated.set(topologyModel.run_id, positions);
            return updated;
          })}
          renderDetail={selected ? (close) => <EntityDetail key={selected.id} model={topologyModel} selected={selected}
            onSelect={(id) => topologyModel.operations.some((op) => op.id === id) ? setTopologySelection(id) : locate(id)}
            onSource={props.onOpenSource} onClose={close} /> : undefined} />
      </div> : graphError ? <Alert type="error" title="读取工艺拓扑失败" description={graphError} />
        : model ? <p role="status" className="muted">正在读取历史版本的拓扑…</p>
          : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前没有语义版本可用于绘制拓扑" />)}
      {props.tab === "semantics" && (model ? <SemanticWorkspace key={model.run_id} model={model}
        group={group} onGroup={setGroup} issues={issues} locatePath={locatePath} onLocated={() => setLocatePath(null)}
        onOntology={() => setOntologyOpen(true)} onSource={props.onOpenSource} />
        : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前没有统一语义模型" />)}
      {props.tab === "issues" && <IssuesView key={model?.run_id} model={model} issues={issues} onLocate={locate} onSource={props.onOpenSource} />}
      {props.tab === "handoff" && (model && props.sessionId ?
        <HandoffWorkspace key={`${props.sessionId}/${model.run_id}`} sessionId={props.sessionId} model={model}
          onLocate={locate} onSource={props.onOpenSource} onTopology={() => props.onTab("topology")} onSchema={() => setSchemaKind("handoff")}
          onStartMatching={props.onStartMatching} />
        : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="生成正式语义版本后查看下游标准输入" />)}
      {props.tab === "json" && (props.extractedJson ? <div className="result-stack">
        <div className="result-heading"><div><h3>结构化数据</h3><p>{model ? `内容摘要 ${model.content_digest.slice(0, 16)}` : "旧版抽取 JSON"}</p></div>
          <div className="result-actions"><CopyButton value={json} />
          <Button size="small" icon={<ArrowDownToLine size={15} />}
            onClick={() => {
              if (model && props.sessionId) {
                const link = document.createElement("a");
                link.href = api.exportSemanticVersionUrl(props.sessionId, model.run_id);
                link.click();
              } else saveJson(props.extractedJson, "requirement.json");
            }}>JSON</Button>
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
      {schemaKind && <ContractBrowser initialKind={schemaKind} onClose={() => setSchemaKind(null)} />}
      {reviewOpen && model && <ReviewDialog model={model} onClose={() => setReviewOpen(false)}
        onSubmit={(payload) => props.onReview(model.run_id, payload)} />}
    </div>
  </section>;
}
