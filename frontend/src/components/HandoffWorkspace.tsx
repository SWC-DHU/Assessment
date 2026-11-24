import { useEffect, useState } from "react";
import { Alert, Button, Empty } from "antd";
import { ArrowDownToLine, Cpu, GitBranch } from "lucide-react";
import { api } from "../api/client";
import type { ModelingTask, ResourceMatchingInput, SemanticEntity, SemanticModel } from "../types";
import { Citations, ReviewPill } from "./SemanticDetails";
import { modelingScopeLabels } from "./semanticPresentation";

export function constraintValue(item: SemanticEntity) {
  const bounds = item.comparator === "range" ? [item.lower, item.upper].join("–") : item.value;
  if (bounds == null) return item.description ?? item.name;
  const sign = item.comparator === "range" ? null : item.comparator?.replace("<=", "≤").replace(">=", "≥");
  return [sign, bounds, item.unit, item.basis ? `/ ${item.basis}` : null].filter((part) => part != null).join(" ");
}

export function TaskSummary({ model, task, onLocate, onSource }: {
  model: SemanticModel;
  task: ModelingTask | null | undefined;
  onLocate: (id: string) => void;
  onSource: (id: string) => void;
}) {
  const product = model.products.find((item) => item.id === task?.target_product_id);
  const objective = model.modeling_intents?.find((item) => task?.objective_ids.includes(item.id) && item.kind === task.task_type);
  const evidence = [...(objective?.evidence ?? []), ...(task?.review_evidence ?? [])];
  return <section className="task-summary">
    <header><h4>建模任务</h4>{!!task?.review_evidence.length && <ReviewPill state="confirmed" />}</header>
    {task ? <>
      <dl className="fact-grid">
        <div><dt>任务类型</dt><dd>数字孪生模型构建</dd></div>
        <div><dt>范围</dt><dd>{task.target_scope ? modelingScopeLabels[task.target_scope] : "待确认"}</dd></div>
        <div><dt>目标产品</dt><dd>{product ? <button type="button" className="text-action" onClick={() => onLocate(product.id)}>{product.name}</button> : "待确认"}</dd></div>
        <div><dt>预期产物</dt><dd>{task.required_outputs.includes("digital_twin_model") ? "数字孪生模型" : "待确认"}</dd></div>
      </dl>
      {!!evidence.length && <details><summary>任务来源</summary><Citations model={model} items={evidence} onSource={onSource} /></details>}
    </> : <p className="muted">尚无已明确的建模任务。可通过需求来源或人工审核补充。</p>}
  </section>;
}

function RelatedFacts({ keys, records, onLocate }: {
  keys: string[]; records: Record<string, SemanticEntity>; onLocate: (id: string) => void;
}) {
  if (!keys.length) return <span className="muted">未提供</span>;
  return <ul className="handoff-facts">{keys.map((key) => <li key={key}>
    {records[key] ? <button type="button" className="text-action" onClick={() => onLocate(key)}>{records[key].name}</button> : <code>{key}</code>}
  </li>)}</ul>;
}

export function HandoffWorkspace({ sessionId, model, onLocate, onSource, onTopology, onSchema, onStartMatching }: {
  sessionId: string;
  model: SemanticModel;
  onLocate: (id: string) => void;
  onSource: (id: string) => void;
  onTopology: () => void;
  onSchema: () => void;
  onStartMatching?: (sessionId: string, versionId: string) => void;
}) {
  const [data, setData] = useState<ResourceMatchingInput>();
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const request = new AbortController();
    setError("");
    api.getResourceMatchingInput(sessionId, model.run_id, request.signal).then(setData).catch((reason: unknown) => {
      if (!request.signal.aborted) setError(String(reason));
    });
    return () => request.abort();
  }, [sessionId, model.run_id, attempt]);
  if (error) return <div className="result-stack">
    <Alert type="error" title="读取下游标准输入失败" description={error} />
    <Button size="small" onClick={() => setAttempt((value) => value + 1)}>重新读取</Button>
  </div>;
  if (!data) return <p role="status" className="muted">正在读取所选版本的交接结果…</p>;
  const report = data.handoff_validation;
  const issues = [...report.issues].sort((a, b) => Number(b.severity === "blocker") - Number(a.severity === "blocker"));
  const operations = new Map(data.operation_requirements.map((op) => [op.operation_id, op]));
  const capabilityCount = new Set(data.operation_requirements.flatMap((op) => op.required_capability_codes)).size;
  const entities = new Set([...model.relations.map((item) => item.id),
    ...model.operations.map((op) => op.id), ...Object.keys(data.capabilities), ...Object.keys(data.constraints)]);
  const line = data.line_constraint_ids.map((key) => data.constraints[key]).filter(Boolean);
  return <div className="result-stack handoff-workspace">
    <div className="result-heading">
      <div><h3>下游标准输入</h3><p>契约 {data.contract_version} · 来源版本 {data.source_model.run_id.slice(0, 8)}</p></div>
      <div className="result-actions">
        {onStartMatching && report.status !== "blocked" && (
          <Button size="small" type="primary" icon={<Cpu size={14} />} onClick={() => onStartMatching(sessionId, model.run_id)}>
            进入资源匹配
          </Button>
        )}
        <Button size="small" onClick={onSchema}>查看 Schema</Button>
        <Button size="small" icon={<ArrowDownToLine size={14} />} href={api.exportResourceMatchingUrl(sessionId, model.run_id)}>
          {report.status === "blocked" ? "导出诊断 JSON" : "导出交接 JSON"}
        </Button>
      </div>
    </div>
    <div className="handoff-status" data-state={report.status} role="status">
      <ReviewPill state={report.status} />
      <span>{issues.filter((item) => item.severity === "blocker").length} 项阻塞 · {issues.filter((item) => item.severity === "warning").length} 项警告</span>
    </div>
    {!!issues.length && <details className="result-disclosure" open={report.status === "blocked" || undefined}>
      <summary>交接问题 · {issues.length}</summary>
      <div className="validation-list">{issues.map((item, index) => {
        const id = entities.has(item.path) ? item.path : item.operation_ids[0];
        return <article key={`${item.code}-${item.path}-${index}`}>
          <div className="validation-item-head"><strong>{item.severity === "blocker" ? "阻塞" : "警告"}</strong>
            {id && <button type="button" className="text-action" onClick={() => onLocate(id)}>定位事实</button>}
          </div>
          <p>{item.message}</p>
          <small>{item.operation_ids.map((key) => operations.get(key)?.operation_name ?? key).join("、") || item.path}</small>
        </article>;
      })}</div>
    </details>}
    <TaskSummary model={model} task={data.modeling_task} onLocate={onLocate} onSource={onSource} />
    <div className="runtime-metrics">
      <span>工序 <b>{operations.size}</b></span><span>已核验能力编码 <b>{capabilityCount}</b></span>
      <Button size="small" icon={<GitBranch size={14} />} onClick={onTopology}>工艺拓扑 · {data.process_graph.edges.length} 条边</Button>
    </div>
    {!!line.length && <section className="requirement-summary"><h4>整线约束</h4>
      {line.map((item) => <button type="button" key={item.id} onClick={() => onLocate(item.id)}>
        <span>{item.name}</span><strong>{constraintValue(item)}</strong><ReviewPill state={item.status} />
      </button>)}
    </section>}
    <section className="handoff-operations"><h4>工序资源需求</h4>
      {data.operation_requirements.length ? data.operation_requirements.map((op) =>
        <details className="result-disclosure" key={op.operation_id}><summary>
          <strong>{[op.operation_number, op.operation_name].filter(Boolean).join(" · ")}</strong>
          <span>{op.has_blocking_issues ? "存在阻塞 · " : ""}{op.required_capability_codes.join("、") || "能力待确认"}</span>
        </summary>
          <dl className="fact-grid">
            <div><dt>工序约束</dt><dd><RelatedFacts keys={op.constraint_ids} records={data.constraints} onLocate={onLocate} /></dd></div>
            <div><dt>输入物料</dt><dd><RelatedFacts keys={op.input_material_ids} records={data.materials} onLocate={onLocate} /></dd></div>
            <div><dt>输出物料</dt><dd><RelatedFacts keys={op.output_material_ids} records={data.materials} onLocate={onLocate} /></dd></div>
            <div><dt>直接前驱</dt><dd>{op.predecessor_ids.map((key) => operations.get(key)?.operation_name ?? key).join("、") || "未声明"}</dd></div>
          </dl>
          <button type="button" className="text-action" onClick={() => onLocate(op.operation_id)}>查看工序与来源</button>
        </details>) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="缺少可交接的工序" />}
    </section>
    <details className="result-disclosure"><summary>标准 JSON 预览</summary>
      <pre className="json-code">{JSON.stringify(data, null, 2)}</pre>
    </details>
  </div>;
}
