import { useEffect, useId, useRef, useState } from "react";
import type { ReactNode } from "react";
import { X } from "lucide-react";
import type { Evidence, SemanticEntity, SemanticIssue, SemanticModel, SemanticRelation } from "../types";
import { conceptLabel, entityTitle, relationLabel, relationTitle, semanticEntities } from "./semanticPresentation";
import { wheelPixels } from "./useTopologyViewport";

export const statusLabels: Record<string, string> = {
  verified: "语义校验通过", pending: "待确认", conflict: "来源冲突", invalid: "校验不通过",
  confirmed: "人工确认", complete: "解析完成", failed: "解析失败", located: "来源已定位",
  unprocessed: "未处理", success: "完成", skipped: "未执行", error: "失败", partial: "部分完成",
  ready: "可交接", ready_with_warnings: "带警告可交接", blocked: "交接阻塞",
};

export function ReviewPill({ state }: { state: string }) {
  return <span className="review-pill" data-state={state}>{statusLabels[state] || state}</span>;
}

export function evidenceLocation(cite: Evidence) {
  const loc = cite.location;
  const parts = [loc.sheet, loc.cell, loc.page != null ? `第 ${loc.page} 页` : null,
    loc.line != null ? `第 ${loc.line} 行` : null, loc.paragraph != null ? `段落 ${loc.paragraph}` : null,
    loc.message_id ? "用户消息" : null].filter(Boolean);
  return parts.join(" / ") || cite.fragment_id;
}

export function Citations({ model, items, onSource }: {
  model: SemanticModel; items: Evidence[]; onSource: (id: string, cite?: Evidence) => void;
}) {
  if (!items.length) return <p className="muted">当前事实没有可核验的逐字引文。</p>;
  return <div className="citation-list">
    {items.map((cite, index) => {
      const source = model.source_refs.find((entry) => entry.document_id === cite.document_id);
      return <figure key={`${cite.document_id}-${cite.fragment_id}-${index}`}>
        <blockquote>{cite.quote}</blockquote>
        <figcaption>
          {source?.kind === "file" || source?.kind === "message"
            ? <button type="button" className="text-action" onClick={() => onSource(cite.document_id, cite)}>{source.name}</button>
            : source?.name || cite.document_id}
          <span>{evidenceLocation(cite)}</span>
          <span>{{ structured: "表格提取", llm: "模型候选", rule: "规则提取", manual: "人工确认", legacy: "历史来源" }[cite.method ?? "legacy"]}</span>
          <ReviewPill state={cite.status === "verified" ? "located" : cite.status} />
        </figcaption>
      </figure>;
    })}
  </div>;
}

function DetailTabs({ sections }: { sections: { label: string; body: ReactNode }[] }) {
  const [active, setActive] = useState(0);
  const id = useId();
  const content = useRef<HTMLDivElement>(null);
  const activate = (index: number) => {
    if (index === active) return;
    if (content.current) content.current.scrollTop = 0;
    setActive(index);
  };
  useEffect(() => {
    const pane = content.current;
    if (!pane) return;
    // Keep wheel scrolling on this pane when the detail docks after a viewport resize.
    const scrollDetails = (event: WheelEvent) => {
      if (!event.deltaY || event.ctrlKey || event.metaKey || event.shiftKey) return;
      event.preventDefault();
      pane.scrollTop += wheelPixels(event.deltaY, event.deltaMode, pane.clientHeight);
    };
    pane.addEventListener("wheel", scrollDetails, { passive: false });
    return () => pane.removeEventListener("wheel", scrollDetails);
  }, []);
  return <>
    <div className="topology-detail-tabs" role="tablist" aria-label="节点详情内容">
      {sections.map(({ label }, index) => <button key={label} id={`${id}-${index}`} type="button" role="tab"
        aria-selected={index === active} aria-controls={`${id}-content`} tabIndex={index === active ? 0 : -1}
        onClick={() => activate(index)} onKeyDown={(event) => {
          if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
          event.preventDefault();
          const next = event.key === "Home" ? 0 : event.key === "End" ? sections.length - 1 :
            (index + (event.key === "ArrowRight" ? 1 : sections.length - 1)) % sections.length;
          activate(next);
          document.getElementById(`${id}-${next}`)?.focus();
        }}>{label}</button>)}
    </div>
    <div className="topology-detail-section" role="tabpanel" tabIndex={0} ref={content}
      id={`${id}-content`} aria-labelledby={`${id}-${active}`}>{sections[active].body}</div>
  </>;
}

export function EntityDetail({ model, selected, onSelect, onSource, onClose, onExplore, semantic = false }: {
  model: SemanticModel; selected: SemanticEntity; onSelect: (id: string) => void; onSource: (id: string) => void;
  onClose?: () => void; onExplore?: () => void; semantic?: boolean;
}) {
  const [kind, setKind] = useState("");
  const lookup = semanticEntities(model);
  const relations = model.relations.filter((edge) => edge.source_id === selected.id || edge.target_id === selected.id);
  const relationTypes = [...new Set(relations.map(({ type }) => type))];
  const displayed = relations.filter((edge) => !kind || edge.type === kind);
  const target = selected.target_id ? lookup.get(selected.target_id) : undefined;
  const baseMaterial = selected.material_id ? lookup.get(selected.material_id) : undefined;
  const fields = [
    ["语义类型", conceptLabel(selected.concept)], ["工序编号", selected.number],
    ["标准术语", selected.standard_term], ["映射依据", selected.mapping_basis],
    ["输入状态", selected.input_state_ids?.map((id) => lookup.get(id)?.name).filter(Boolean).join("、")],
    ["输出状态", selected.output_state_ids?.map((id) => lookup.get(id)?.name).filter(Boolean).join("、")],
    ["工序类型", selected.operation_type], ["产品图号", selected.part_number], ["原始能力描述", selected.raw_names?.join("；")], ["设备要求", selected.equipment_requirement],
    ["能力编码", selected.code === selected.standard_term ? null : selected.code], ["映射候选", selected.candidates?.join("、")], ["物料类型", selected.material_type],
    ["要求类型", selected.constraint_type && ({ quality: "质量要求", condition: "工艺条件", numeric: "数值约束",
      process_time: "单件工时", wait_time: "等待时间", buffer_time: "缓存时间", takt_time: "生产节拍",
      quantity: "数量约束", general: "一般约束" }[selected.constraint_type] ?? selected.constraint_type)],
    ["参数编码", selected.parameter_code || selected.related_parameter_codes?.join("、")], ["比较符", selected.comparator],
    ["数值", selected.value == null ? null : String(selected.value)],
    ["上下限", selected.lower != null || selected.upper != null ? `${selected.lower ?? "—"} – ${selected.upper ?? "—"}` : null],
    ["单位", selected.unit], ["计量基准", selected.basis], ["约束说明", selected.description],
    ["约束强度", selected.strength && ({ mandatory: "强制", preferred: "偏好", unspecified: "未说明" }[selected.strength] ?? selected.strength)],
  ].filter(([, value]) => value != null && value !== "");
  const references = [
    ["制造能力", selected.capability_ids], ["工艺约束", selected.constraint_ids],
    ["输入物料", selected.input_material_ids], ["输出物料", selected.output_material_ids], ["直接前驱", selected.predecessor_ids],
  ] as const;
  const issues = model.validation.issues.filter((issue) => issue.path === selected.id);
  const overview = <dl className="fact-grid">
    {fields.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
    {selected.concept === "Operation" && references.map(([label, ids]) => <div key={label}><dt>{label}</dt>
      <dd>{ids?.length ? <ul className="handoff-facts">{ids.map((id) => {
        const fact = lookup.get(id);
        const label = [fact?.name, fact?.code].filter(Boolean).join(" · ");
        return <li key={id}>{fact ? <button type="button" className="text-action" onClick={() => onSelect(id)}>{label}</button> : id}</li>;
      })}</ul> : <span className="muted">{label === "直接前驱" && selected.precursor_declaration?.length === 0 ? "原文说明无前驱" : "未提供"}</span>}</dd>
    </div>)}
    {!!issues.length && <div><dt>校验问题</dt><dd>{issues.map((item) =>
      <p key={item.code + item.message}>{item.message}</p>)}</dd></div>}
    {target && <div><dt>约束对象</dt><dd><button className="text-action" type="button" onClick={() => onSelect(target.id)}>{entityTitle(target)}</button></dd></div>}
    {baseMaterial && <div><dt>基础物料</dt><dd><button className="text-action" type="button" onClick={() => onSelect(baseMaterial.id)}>{entityTitle(baseMaterial)}</button></dd></div>}
    {!!selected.information_flows?.length && <div><dt>工序信息流</dt><dd>{selected.information_flows.map((flow) =>
      <p key={flow.direction + flow.name}>{flow.direction === "input" ? "输入" : "输出"} · {flow.name}</p>)}</dd></div>}
  </dl>;
  const evidence = <>
    <Citations model={model} items={[...selected.evidence, ...(selected.equipment_evidence ?? [])]} onSource={onSource} />
    {selected.information_flows?.map((flow) => <section key={flow.direction + flow.name}><h4>{flow.name}</h4>
      <Citations model={model} items={flow.evidence} onSource={onSource} /></section>)}
    {!!selected.review_evidence?.length && <><h4>人工审核依据</h4><Citations model={model} items={selected.review_evidence} onSource={onSource} /></>}
  </>;
  const facts = relations.length ? <>
    {semantic && <select className="semantic-select semantic-fact-filter" aria-label="关联事实类型" value={kind} onChange={(event) => setKind(event.target.value)}>
      <option value="">全部关系</option>
      {relationTypes.map((type) => <option key={type} value={type}>{relationLabel({ type })}</option>)}
    </select>}
    <div className="relation-list">{displayed.map((edge) => {
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
    </div>
  </> : null;
  return <article className={onClose ? "entity-detail topology-entity-detail" : "entity-detail"}>
    <header>
      <div>{semantic && <small>{conceptLabel(selected.concept)}</small>}<h3>{entityTitle(selected)}</h3></div>
      <div className="result-actions">{selected.id !== "line" && <ReviewPill state={selected.status} />}{selected.review === "confirmed" && <ReviewPill state="confirmed" />}</div>
      {onClose && <button type="button" className="topology-detail-close" aria-label={semantic ? "收起实体详情" : "收起节点详情"} onClick={onClose}>
        <X size={16} aria-hidden="true" />
      </button>}
    </header>
    {onExplore && <div className="semantic-detail-actions">
      <button type="button" className="text-action" onClick={onExplore}>以此为中心</button>
    </div>}
    <DetailTabs sections={[
      { label: semantic ? "概况" : "工序概况", body: overview },
      { label: "原文证据", body: evidence },
      { label: "关联事实", body: facts || <p className="muted">当前实体没有关联事实。</p> },
    ]} />
  </article>;
}

export function RelationDetail({ model, edges, issues, onClose, onRelation, onEntity, onSource, explain }: {
  model: SemanticModel;
  edges: SemanticRelation[];
  issues: SemanticIssue[];
  onClose: () => void;
  onRelation: (edge: SemanticRelation) => void;
  onEntity: (id: string) => void;
  onSource: (id: string) => void;
  explain: (issue: SemanticIssue) => string | undefined;
}) {
  const entities = semanticEntities(model);
  const title = edges.length === 1 ? relationTitle(edges[0], entities) : `${edges.length} 条关系`;
  const close = <button type="button" className="topology-detail-close" aria-label="收起关系详情" onClick={onClose}>
    <X size={16} aria-hidden="true" />
  </button>;
  if (edges.length > 1) return <article className="entity-detail topology-entity-detail">
    <header><h3>{title}</h3><span />{close}</header>
    <div className="topology-detail-section semantic-bundle-list">
      {edges.map((edge) => <button key={edge.id} type="button" className="semantic-row" onClick={() => onRelation(edge)}>
        <span>{relationTitle(edge, entities)}</span><ReviewPill state={edge.status} />
      </button>)}
    </div>
  </article>;

  const edge = edges[0];
  const source = entities.get(edge.source_id);
  const target = entities.get(edge.target_id);
  const overview = <>
    <dl className="fact-grid">
      <div><dt>起点</dt><dd>{source ? <button type="button" className="text-action" onClick={() => onEntity(source.id)}>{entityTitle(source)}</button> : edge.source_id}</dd></div>
      <div><dt>关系类型</dt><dd>{relationLabel(edge)}</dd></div>
      <div><dt>终点</dt><dd>{target ? <button type="button" className="text-action" onClick={() => onEntity(target.id)}>{entityTitle(target)}</button> : edge.target_id}</dd></div>
      {edge.quantity && <div><dt>组成数量</dt><dd>{edge.quantity.value} {edge.quantity.unit}{edge.quantity.basis ? ` / ${edge.quantity.basis}` : ""}</dd></div>}
      {edge.condition && <div><dt>转序条件</dt><dd>{edge.condition}</dd></div>}
      {edge.max_iterations != null && <div><dt>返工上限</dt><dd>{edge.max_iterations} 次</dd></div>}
      {edge.asserted_as === "inferred" && <div><dt>关系依据</dt><dd>模型推断，待人工确认</dd></div>}
    </dl>
    {!!issues.length && <div className="semantic-issue-reasons">
      {issues.map((issue, index) => {
        const reason = explain(issue);
        return <div key={`${issue.code}-${index}`}>
          <p>{issue.message}</p>{reason && <p>{reason}</p>}
        </div>;
      })}
    </div>}
  </>;
  return <article className="entity-detail topology-entity-detail">
    <header><h3>{title}</h3><div className="result-actions"><ReviewPill state={edge.status} />
      {edge.review === "confirmed" && <ReviewPill state="confirmed" />}</div>{close}</header>
    <DetailTabs sections={[
      { label: "概况", body: overview },
      { label: "原文证据", body: <Citations model={model} items={[...edge.evidence, ...(edge.review_evidence ?? [])]} onSource={onSource} /> },
    ]} />
  </article>;
}
