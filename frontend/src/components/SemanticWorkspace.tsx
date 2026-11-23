import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Button, Empty, Input, Modal } from "antd";
import { ArrowLeft, ArrowRight, BookOpen, Search } from "lucide-react";
import { api } from "../api/client";
import type { OntologyCatalog, SemanticGroup, SemanticIssue, SemanticModel, SemanticRelation } from "../types";
import { EntityDetail, RelationDetail, ReviewPill, statusLabels } from "./SemanticDetails";
import { SemanticGraph } from "./SemanticGraph";
import { conceptLabel, entityTitle, groups, relationLabel, relationTitle, semanticEntities } from "./semanticPresentation";
import { wheelPixels } from "./useTopologyViewport";

export type EntityGroup = SemanticGroup | "all";
type Mode = "list" | "focus" | "all";
type Detail = { entity: string } | { relations: SemanticRelation[] };

interface SemanticWorkspaceProps {
  model: SemanticModel;
  group: EntityGroup;
  onGroup: (group: EntityGroup) => void;
  issues: SemanticIssue[];
  issuesOpen: boolean;
  onCloseIssues: () => void;
  onOntology: () => void;
  onSource: (id: string) => void;
}

const issuePriority: Record<SemanticIssue["status"], number> = { invalid: 0, conflict: 1, pending: 2, unprocessed: 3, verified: 4 };
const statusOptions = ["verified", "pending", "conflict", "invalid", "unprocessed"];

export function SemanticWorkspace({ model, group, onGroup, issues, issuesOpen, onCloseIssues, onOntology, onSource }: SemanticWorkspaceProps) {
  const entities = useMemo(() => semanticEntities(model), [model]);
  const relationById = useMemo(() => new Map(model.relations.map((edge) => [edge.id, edge])), [model.relations]);
  const [mode, setMode] = useState<Mode>("list");
  const [trail, setTrail] = useState<string[]>([]);
  const [entityId, setEntityId] = useState<string | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [kind, setKind] = useState("");
  const [catalog, setCatalog] = useState<OntologyCatalog>();
  const directory = useRef<HTMLDivElement>(null);
  const detailPane = useRef<HTMLElement>(null);
  const selected = detail && "entity" in detail ? entities.get(detail.entity) : undefined;
  const activeEdges = detail && "relations" in detail ? detail.relations : [];
  const edge = activeEdges.length === 1 ? activeEdges[0] : undefined;
  const needsCatalog = issuesOpen || (edge?.status === "invalid" && issues.some((issue) => issue.path === edge.id));
  const center = trail[trail.length - 1] ?? null;
  const focus = mode === "focus" ? center : null;
  const selectedEntityId = selected?.id ?? (activeEdges.length ? null : entityId);

  useEffect(() => {
    if (!needsCatalog || catalog) return;
    let active = true;
    api.getOntology(model.ontology_version).then((value) => { if (active) setCatalog(value); }).catch(() => {});
    return () => { active = false; };
  }, [catalog, model.ontology_version, needsCatalog]);

  useEffect(() => {
    const pane = directory.current;
    if (!pane) return;
    const wheel = (event: WheelEvent) => {
      if (!event.deltaY || event.shiftKey || event.ctrlKey || event.metaKey) return;
      event.preventDefault();
      pane.scrollTop += wheelPixels(event.deltaY, event.deltaMode, pane.clientHeight);
    };
    pane.addEventListener("wheel", wheel, { passive: false });
    return () => pane.removeEventListener("wheel", wheel);
  }, []);

  useLayoutEffect(() => {
    if (entityId && directory.current?.clientHeight) {
      const item = Array.from(directory.current.querySelectorAll<HTMLButtonElement>("button")).find((button) => button.dataset.entity === entityId);
      if (!item) return;
      const parent = directory.current.getBoundingClientRect();
      const itemBounds = item.getBoundingClientRect();
      if (itemBounds.top < parent.top || itemBounds.bottom > parent.bottom) {
        directory.current.scrollTop += itemBounds.top - parent.top;
      }
    }
  }, [entityId, mode]);

  const focusOn = (id: string) => {
    setMode("focus");
    if (id !== center || mode !== "focus") setKind("");
    setEntityId(entities.has(id) ? id : null);
    setTrail((previous) => previous[previous.length - 1] === id ? previous : [...previous, id]);
  };

  const selectEntity = (id: string, recenter = false) => {
    setEntityId(id);
    setDetail({ entity: id });
    if (recenter) focusOn(id);
  };

  const back = () => {
    const previous = trail[trail.length - 2];
    setTrail(trail.slice(0, -1));
    setEntityId(entities.has(previous) ? previous : null);
    setDetail(null);
    setMode("focus");
  };

  const explain = (issue: SemanticIssue) => {
    const relation = relationById.get(issue.path);
    if (!relation || issue.code !== "relation_reference") return;
    const source = entities.get(relation.source_id);
    const target = entities.get(relation.target_id);
    const signature = catalog?.object_properties.find(({ id }) => id === relation.type);
    const reasons: string[] = [];
    if (!source) reasons.push("起点实体在当前版本中缺失");
    if (!target) reasons.push("终点实体在当前版本中缺失");
    if (source && signature?.domain.length && !signature.domain.includes(source.concept)) {
      reasons.push(`起点是「${conceptLabel(source.concept)}」，本体要求「${signature.domain.map(conceptLabel).join("」或「")}」`);
    }
    if (target && signature?.range.length && !signature.range.includes(target.concept)) {
      reasons.push(`终点是「${conceptLabel(target.concept)}」，本体要求「${signature.range.map(conceptLabel).join("」或「")}」`);
    }
    if (relation.source_id === relation.target_id) reasons.push("起点和终点指向同一个实体");
    return reasons.length ? `${reasons.join("。")}。` : undefined;
  };

  const locateIssue = (issue: SemanticIssue) => {
    const relation = relationById.get(issue.path);
    onCloseIssues();
    setKind("");
    if (relation) {
      focusOn(entities.has(relation.source_id) || !entities.has(relation.target_id) ? relation.source_id : relation.target_id);
      setDetail({ relations: [relation] });
    } else selectEntity(issue.path, true);
  };

  const closeDetail = () => {
    setDetail(null);
    const viewport = detailPane.current?.parentElement?.querySelector<HTMLElement>(".semantic-viewport");
    viewport?.focus({ preventScroll: true });
  };
  const list = (group === "all" ? [...entities.values()] : model[group]).filter((entity) =>
    (!status || (entity.id !== "line" && entity.status === status)) && [
      entity.name, entity.number, entity.code, entity.standard_term, entity.id, ...(entity.raw_names ?? []),
    ].some((value) => value?.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())));
  const kinds = [...new Set(model.relations.filter((relation) => !focus || relation.source_id === focus || relation.target_id === focus).map((edge) => edge.type))];
  const centerEntity = focus ? entities.get(focus) : undefined;

  return <section className="semantic-workspace" aria-label="领域语义">
    <header className="result-heading semantic-heading">
      <h3>领域语义</h3>
      <div className="result-actions">
        <Button size="small" aria-pressed={mode === "list"} type={mode === "list" ? "primary" : "default"} onClick={() => {
          setMode("list");
          if (detail && "relations" in detail) setDetail(null);
        }}>实体列表</Button>
        {mode === "list" && <Button size="small" disabled={!entityId}
          icon={<ArrowRight size={13} />} onClick={() => { if (entityId) focusOn(entityId); }}>查看关系</Button>}
        <Button size="small" aria-label="全图" disabled={!entities.size && !model.relations.length} aria-pressed={mode === "all"}
          type={mode === "all" ? "primary" : "default"} onClick={() => { setKind(""); setMode("all"); }}>全图</Button>
        <Button size="small" icon={<BookOpen size={14} />} onClick={onOntology}>浏览本体</Button>
      </div>
    </header>
    <div className="semantic-layout" data-mode={mode} data-detail={Boolean(detail)}>
      <aside className="semantic-directory" aria-label="语义实体列表">
        <div className="semantic-directory-tools">
          <Input size="small" allowClear prefix={<Search size={13} aria-hidden="true" />} aria-label="搜索实体"
            placeholder="搜索名称、编号或编码" value={query} onChange={(event) => setQuery(event.target.value)} />
          <div className="semantic-filter-row">
            <select className="semantic-select" aria-label="实体类型" value={group} onChange={(event) => onGroup(event.target.value as EntityGroup)}>
              <option value="all">全部实体</option>
              {groups.map(([key, label]) => <option key={key} value={key}>{label} · {model[key].length}</option>)}
            </select>
            <select className="semantic-select" aria-label="实体校验状态" value={status} onChange={(event) => setStatus(event.target.value)}>
              <option value="">全部状态</option>
              {statusOptions.map((value) => <option key={value} value={value}>{statusLabels[value]}</option>)}
            </select>
          </div>
        </div>
        <div className="semantic-directory-results" ref={directory} tabIndex={0} aria-label="实体搜索结果">
          {list.length ? list.map((entity) => <button type="button" key={entity.id} className="semantic-row"
            data-entity={entity.id} aria-pressed={entity.id === entityId} onClick={() => {
              if (mode !== "list") setKind("");
              selectEntity(entity.id, mode !== "list");
            }}>
            <span><strong>{entityTitle(entity)}</strong><small>{[conceptLabel(entity.concept), entity.code].filter(Boolean).join(" · ")}</small></span>
            {entity.id !== "line" && <ReviewPill state={entity.status} />}
          </button>) : <p className="muted">没有符合条件的实体。</p>}
        </div>
      </aside>
      {mode !== "list" && <div className="semantic-graph-panel">
        <div className="semantic-graph-tools">
          <div className="semantic-focus">
            {mode === "focus" && trail.length > 1 && <Button size="small" icon={<ArrowLeft size={14} />} aria-label="返回上一个焦点" title="返回上一个焦点"
              onClick={() => { setKind(""); back(); }} />}
            {mode === "all" && center && <Button size="small" icon={<ArrowLeft size={14} />} onClick={() => focusOn(center)}>返回焦点</Button>}
            <strong title={centerEntity ? entityTitle(centerEntity) : undefined}>{focus ? centerEntity ? entityTitle(centerEntity) : "未解析实体" : "全图"}</strong>
          </div>
          <select className="semantic-select" aria-label="关系类型" value={kind} onChange={(event) => setKind(event.target.value)}>
            <option value="">全部关系</option>
            {kinds.map((type) => <option key={type} value={type}>{relationLabel({ type })}</option>)}
          </select>
        </div>
        <div className="semantic-graph-legend" aria-label="线型图例">
          <span>校验通过</span><span data-state="pending">待确认／未处理</span><span data-state="invalid">校验不通过</span>
        </div>
        <SemanticGraph entities={entities} relations={model.relations} focus={focus} kind={kind}
          selectedEntity={selectedEntityId} selectedRelations={activeEdges.map(({ id }) => id)} detailOpen={Boolean(detail)}
          onSelectEntity={selectEntity} onSelectRelations={(relations) => setDetail({ relations })} />
      </div>}
      {detail && <aside className="topology-detail semantic-detail" aria-label="语义详情" ref={detailPane}>
        {selected && <EntityDetail key={selected.id} model={model} selected={selected} onSource={onSource} semantic
          onSelect={(id) => selectEntity(id, mode !== "list")} onClose={closeDetail}
          onExplore={mode !== "list" && selected.id !== focus ? () => focusOn(selected.id) : undefined} />}
        {!!activeEdges.length && <RelationDetail key={activeEdges.map(({ id }) => id).join("|")} model={model}
          edges={activeEdges} issues={edge ? issues.filter((issue) => issue.path === edge.id) : []}
          onClose={closeDetail} onRelation={(relation) => setDetail({ relations: [relation] })}
          onEntity={(id) => { setKind(""); selectEntity(id, true); }} onSource={onSource} explain={explain} />}
      </aside>}
    </div>
    {issuesOpen && <Modal title="待确认与诊断" open onCancel={onCloseIssues} footer={null} width={600}>
      {issues.length ? <div className="semantic-issue-list">
        {[...issues].sort((a, b) => issuePriority[a.status] - issuePriority[b.status]).map((issue, index) => {
          const relation = relationById.get(issue.path);
          const entity = entities.get(issue.path);
          const reason = explain(issue);
          return <article key={`${issue.path}-${index}`}>
            <div className="semantic-issue-heading"><ReviewPill state={issue.status} />
              {(relation || entity) && <Button size="small" onClick={() => locateIssue(issue)}>{relation ? "定位关系" : "定位实体"}</Button>}</div>
            {(relation || entity) && <strong>{relation ? relationTitle(relation, entities) : entityTitle(entity!)}</strong>}
            <p>{issue.message}</p>{reason && <p>{reason}</p>}
          </article>;
        })}
      </div> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前版本没有校验问题" />}
    </Modal>}
  </section>;
}
