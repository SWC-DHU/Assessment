import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Button, Input } from "antd";
import { ArrowLeft, ArrowRight, BookOpen, Search } from "lucide-react";
import { api } from "../api/client";
import type { OntologyCatalog, SemanticGroup, SemanticIssue, SemanticModel, SemanticRelation } from "../types";
import { EntityDetail, RelationDetail, ReviewPill, statusLabels } from "./SemanticDetails";
import { SemanticGraph } from "./SemanticGraph";
import { conceptLabel, entitiesInGroup, entityTitle, groups, relationLabel, semanticEntities } from "./semanticPresentation";
import { wheelPixels } from "./useTopologyViewport";

export type EntityGroup = SemanticGroup | "all";
type Mode = "list" | "focus" | "all";
type Detail = { entity: string } | { relations: SemanticRelation[] };

interface SemanticWorkspaceProps {
  model: SemanticModel;
  group: EntityGroup;
  onGroup: (group: EntityGroup) => void;
  issues: SemanticIssue[];
  locatePath: string | null;
  onLocated: () => void;
  onOntology: () => void;
  onSource: (id: string) => void;
}

const statusOptions = ["verified", "pending", "conflict", "invalid", "unprocessed"];

export function SemanticWorkspace({ model, group, onGroup, issues, locatePath, onLocated, onOntology, onSource }: SemanticWorkspaceProps) {
  const entities = useMemo(() => semanticEntities(model), [model]);
  const relationById = useMemo(() => new Map(model.relations.map((edge) => [edge.id, edge])), [model.relations]);
  const [mode, setMode] = useState<Mode>("list");
  const [trail, setTrail] = useState<string[]>([]);
  const [entityId, setEntityId] = useState<string | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [kind, setKind] = useState("core");
  const [catalog, setCatalog] = useState<OntologyCatalog>();
  const directory = useRef<HTMLDivElement>(null);
  const detailPane = useRef<HTMLElement>(null);
  const selected = detail && "entity" in detail ? entities.get(detail.entity) : undefined;
  const activeEdges = detail && "relations" in detail ? detail.relations : [];
  const edge = activeEdges.length === 1 ? activeEdges[0] : undefined;
  const center = trail[trail.length - 1] ?? null;
  const focus = mode === "focus" ? center : null;
  const selectedEntityId = selected?.id ?? (activeEdges.length ? null : entityId);

  useEffect(() => {
    let active = true;
    api.getOntology(model.ontology_version).then((value) => { if (active) setCatalog(value); }).catch(() => {});
    return () => { active = false; };
  }, [model.ontology_version]);

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
    if (id !== center || mode !== "focus") setKind("core");
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
    if (source && signature?.domain.length && !signature.domain.includes(catalog?.instance_concepts?.[source.concept] ?? source.concept)) {
      reasons.push(`起点是「${conceptLabel(source.concept)}」，本体要求「${signature.domain.map(conceptLabel).join("」或「")}」`);
    }
    if (target && signature?.range.length && !signature.range.includes(catalog?.instance_concepts?.[target.concept] ?? target.concept)) {
      reasons.push(`终点是「${conceptLabel(target.concept)}」，本体要求「${signature.range.map(conceptLabel).join("」或「")}」`);
    }
    if (relation.source_id === relation.target_id) reasons.push("起点和终点指向同一个实体");
    return reasons.length ? `${reasons.join("。")}。` : undefined;
  };

  useEffect(() => {
    if (!locatePath) return;
    const relation = relationById.get(locatePath);
    if (relation) {
      focusOn(entities.has(relation.source_id) ? relation.source_id : relation.target_id);
      setKind(relation.type);
      setDetail({ relations: [relation] });
    } else if (entities.has(locatePath)) {
      selectEntity(locatePath, true);
    }
    onLocated();
  }, [locatePath]);

  const closeDetail = () => {
    setDetail(null);
    const viewport = detailPane.current?.parentElement?.querySelector<HTMLElement>(".semantic-viewport");
    viewport?.focus({ preventScroll: true });
  };
  const list = useMemo(() => (group === "all" ? [...entities.values()] : entitiesInGroup(model, group)).filter((entity) =>
    (!status || (entity.id !== "line" && entity.status === status)) && [
      entity.name, entity.number, entity.code, entity.standard_term, entity.id, ...(entity.raw_names ?? []),
    ].some((value) => value?.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()))), [entities, group, model, query, status]);
  const kinds = [...new Set(model.relations.filter((relation) => !focus || relation.source_id === focus || relation.target_id === focus).map((edge) => edge.type))];
  const { graphRelations, graphEntities } = useMemo(() => {
    const groupIds = new Set(group === "all" ? entities.keys() : entitiesInGroup(model, group).map(({ id }) => id));
    const core = new Set(catalog?.core_relations);
    const scoped = mode === "all" && group !== "all";
    const graphRelations = model.relations.filter((edge) =>
      (kind !== "core" || !core.size || core.has(edge.type)) &&
      (kind === "all" || kind === "core" || kind === edge.type) &&
      (!scoped || groupIds.has(edge.source_id) || groupIds.has(edge.target_id)));
    const connected = new Set(graphRelations.flatMap((edge) => [edge.source_id, edge.target_id]));
    const graphEntities = mode === "all"
      ? new Map([...entities].filter(([id]) => connected.has(id) || ((kind === "all" || scoped) && groupIds.has(id))))
      : entities;
    return { graphRelations, graphEntities };
  }, [catalog, entities, group, kind, mode, model]);
  const centerEntity = focus ? entities.get(focus) : undefined;

  return <section className="semantic-workspace" aria-label="领域语义">
    <header className="result-heading semantic-heading">
      <div><h3>领域语义</h3><p>需求实例与来源 · 本体 {model.ontology_version}</p></div>
      <div className="result-actions">
        <Button size="small" aria-pressed={mode === "list"} type={mode === "list" ? "primary" : "default"} onClick={() => {
          setMode("list");
          if (detail && "relations" in detail) setDetail(null);
        }}>实体列表</Button>
        {mode === "list" && <Button size="small" disabled={!entityId}
          icon={<ArrowRight size={13} />} onClick={() => { if (entityId) focusOn(entityId); }}>查看关系</Button>}
        <Button size="small" aria-label="全图" disabled={!entities.size && !model.relations.length} aria-pressed={mode === "all"}
          type={mode === "all" ? "primary" : "default"} onClick={() => { setKind("core"); setMode("all"); }}>全局视图</Button>
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
              {groups.map(([key, label]) => <option key={key} value={key}>{label} · {entitiesInGroup(model, key).length}</option>)}
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
              if (mode !== "list") setKind("core");
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
              onClick={() => { setKind("core"); back(); }} />}
            {mode === "all" && center && <Button size="small" icon={<ArrowLeft size={14} />} onClick={() => focusOn(center)}>返回焦点</Button>}
            <strong title={centerEntity ? entityTitle(centerEntity) : undefined}>{focus ? centerEntity ? entityTitle(centerEntity) : "未解析实体" : "全局视图"}</strong>
          </div>
          <select className="semantic-select" aria-label="单工序聚焦" value={centerEntity?.concept === "Operation" ? centerEntity.id : ""}
            onChange={(event) => { if (event.target.value) selectEntity(event.target.value, true); }}>
            <option value="">单工序聚焦</option>
            {model.operations.map((op) => <option key={op.id} value={op.id}>{entityTitle(op)}</option>)}
          </select>
          <select className="semantic-select" aria-label="关系类型" value={kind} onChange={(event) => setKind(event.target.value)}>
            <option value="core">核心关系</option><option value="all">全部关系</option>
            {kinds.map((type) => <option key={type} value={type}>{relationLabel({ type })}</option>)}
          </select>
          {mode === "all" && <select className="semantic-select" aria-label="关系图实体类型" value={group}
            onChange={(event) => onGroup(event.target.value as EntityGroup)}>
            <option value="all">全部实体类型</option>
            {groups.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
          </select>}
        </div>
        <div className="semantic-graph-legend" aria-label="线型图例">
          <span>校验通过</span><span data-state="pending">待确认</span><span data-state="conflict">来源冲突</span><span data-state="invalid">校验不通过</span>
        </div>
        <SemanticGraph entities={graphEntities} relations={graphRelations} focus={focus} kind={["core", "all"].includes(kind) ? "" : kind}
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
          onEntity={(id) => { setKind("core"); selectEntity(id, true); }} onSource={onSource} explain={explain} />}
      </aside>}
    </div>
  </section>;
}
