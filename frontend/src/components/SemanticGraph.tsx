import { useEffect, useId, useMemo, useState } from "react";
import { Empty, Select } from "antd";
import { Minus, Plus, RotateCcw } from "lucide-react";
import type { SemanticEntity, SemanticModel, SemanticRelation } from "../types";

export const groups = [
  ["products", "产品"], ["operations", "工序"], ["materials", "物料"],
  ["material_states", "物料状态"], ["capabilities", "制造能力"], ["constraints", "约束"],
] as const;

const flowKinds = new Set(["hasPredecessor", "branchesTo", "mergesInto", "reworksTo", "conditionallyPrecedes"]);
const labels: Record<string, string> = {
  hasPredecessor: "直接依赖", branchesTo: "分支", mergesInto: "汇合", reworksTo: "返工",
  conditionallyPrecedes: "条件", hasComponent: "组成", consumes: "消耗", produces: "产出",
  requiresCapability: "需要能力", appliesTo: "作用于", hasInputState: "输入状态", hasOutputState: "输出状态", stateOf: "归属物料",
  precedes: "先后", dependsOn: "依赖", directlyPrecedes: "直接转序", optionalNext: "可选转序",
  declared_predecessor: "直接前驱", direct_transition: "直接转序", precedence: "先后", dependency: "依赖",
  branch: "分支", merge: "汇合", conditional: "条件", rework: "返工", optional: "可选转序",
};

export function relationLabel(edge: SemanticRelation) {
  return labels[edge.type] ?? edge.type;
}

interface GraphEdge { edge: SemanticRelation; from: string; to: string }
interface Point { x: number; y: number }
const NODE_WIDTH = 148;
const NODE_HEIGHT = 54;
const ROW_SPACE = 88;

function layout(nodes: SemanticEntity[], edges: GraphEdge[], flow: boolean) {
  const points = new Map<string, Point>();
  const buckets = new Map<number, SemanticEntity[]>();
  const depths = new Map(nodes.map((node) => [node.id, 0]));
  if (flow) {
    for (let pass = 0; pass < nodes.length; pass += 1) {
      let changed = false;
      for (const { edge, from, to } of edges) {
        if (["reworksTo", "rework", "dependency"].includes(edge.type) || edge.status !== "verified") continue;
        const current = depths.get(to) ?? 0;
        const level = (depths.get(from) ?? 0) + 1;
        if (current < level && level < nodes.length) {
          depths.set(to, level);
          changed = true;
        }
      }
      if (!changed) break;
    }
  }
  const kinds = ["Product", "Operation", "Material", "MaterialState", "Capability", "Constraint", "ProductionLine"];
  nodes.forEach((node) => {
    const column = flow ? (depths.get(node.id) ?? 0) : kinds.indexOf(node.concept);
    const bucket = buckets.get(column) ?? [];
    bucket.push(node);
    buckets.set(column, bucket);
  });
  const columns = [...buckets.keys()].sort((a, b) => a - b);
  const largest = Math.max(1, ...[...buckets.values()].map((bucket) => bucket.length));
  columns.forEach((column, index) => {
    const bucket = buckets.get(column) ?? [];
    bucket.forEach((node, row) => {
      points.set(node.id, { x: 24 + index * 205, y: 32 + (row + (largest - bucket.length) / 2) * ROW_SPACE });
    });
  });
  return { points, width: 48 + columns.length * 205, height: 60 + largest * ROW_SPACE };
}

export function SemanticGraph({ model, flow, selected, onSelect }: {
  model: SemanticModel;
  flow: boolean;
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const [zoom, setZoom] = useState(1);
  const [focus, setFocus] = useState("");
  const [kind, setKind] = useState("");
  const marker = useId();
  useEffect(() => { setFocus(""); setZoom(1); setKind(""); }, [model.run_id, flow]);
  const entities = useMemo(() => groups.flatMap(([key]) => model[key]), [model]);
  const graph = useMemo(() => {
    const oldLinks = model.relations.filter((edge) => (flow ? flowKinds.has(edge.type) : !kind || edge.type === kind))
      .map((edge) => flow && edge.type === "hasPredecessor"
        ? { edge, from: edge.target_id, to: edge.source_id }
        : { edge, from: edge.source_id, to: edge.target_id });
    const links = flow && model.process_graph ? model.process_graph.edges.map((edge) => ({
      from: edge.source, to: edge.target, edge: { ...edge, type: edge.kind, source_id: edge.source, target_id: edge.target },
    })) : oldLinks;
    const neighborhood = new Set([focus]);
    links.filter(({ from, to }) => from === focus || to === focus).forEach(({ from, to }) => {
      neighborhood.add(from);
      neighborhood.add(to);
    });
    const all = flow ? model.operations : entities;
    const filtered = all.filter((node) => !focus || neighborhood.has(node.id));
    if (!flow && neighborhood.has("line")) {
      filtered.push({ id: "line", name: "整线", concept: "ProductionLine", status: "verified", evidence: [] });
    }
    if (!flow && !focus && links.some(({ to }) => to === "line")) {
      filtered.push({ id: "line", name: "整线", concept: "ProductionLine", status: "verified", evidence: [] });
    }
    const ids = new Set(filtered.map((node) => node.id));
    const edges = links.filter(({ from, to }) => ids.has(from) && ids.has(to));
    return { nodes: filtered, edges, ...layout(filtered, edges, flow) };
  }, [entities, flow, focus, kind, model]);

  return (
    <section className="graph-section" aria-label={flow ? "工艺拓扑" : "语义关系图"}>
      <div className="graph-toolbar">
        <Select aria-label="筛选工序或实体" size="small" allowClear showSearch optionFilterProp="label"
          placeholder={flow ? "筛选工序" : "筛选实体"} value={focus || undefined}
          onChange={(value) => setFocus(value ?? "")}
          options={(flow ? model.operations : entities).map((node) => ({
            value: node.id, label: [node.number, node.name].filter(Boolean).join(" · "),
          }))} />
        {!flow && <Select aria-label="关系类型" size="small" allowClear placeholder="关系类型"
          value={kind || undefined} onChange={(value) => setKind(value ?? "")}
          options={Object.entries(labels).map(([value, label]) => ({ value, label }))} />}
        <div className="graph-zoom">
          <button type="button" aria-label="缩小" onClick={() => setZoom((z) => Math.max(0.4, z - 0.2))}><Minus size={15} /></button>
          <output>{Math.round(zoom * 100)}%</output>
          <button type="button" aria-label="放大" onClick={() => setZoom((z) => Math.min(2, z + 0.2))}><Plus size={15} /></button>
          <button type="button" aria-label="重置缩放" onClick={() => setZoom(1)}><RotateCcw size={13} /></button>
        </div>
      </div>
      {flow && model.process_graph && <div className="graph-legend">
        <span>直接或汇合</span><span>条件、返工、先后及待确认</span>
      </div>}
      {graph.nodes.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前版本没有可显示的节点" /> :
        <div className="graph-scroll" tabIndex={0} aria-label="可滚动的关系图">
          <div style={{ width: graph.width * zoom, height: graph.height * zoom }}>
            <div className="graph-canvas" style={{ width: graph.width, height: graph.height, transform: `scale(${zoom})` }}>
              <svg width={graph.width} height={graph.height} aria-hidden="true">
                <defs>
                  <marker id={marker} markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
                    <path d="M 1 1 L 7 4 L 1 7" fill="none" stroke="context-stroke" strokeWidth="1.3" />
                  </marker>
                </defs>
                {graph.edges.map(({ edge, from, to }) => {
                  const a = graph.points.get(from)!;
                  const b = graph.points.get(to)!;
                  const highlighted = selected === from || selected === to;
                  const direct = b.x > a.x;
                  const x1 = a.x + (direct ? NODE_WIDTH : 0);
                  const x2 = b.x + (direct ? -4 : NODE_WIDTH + 4);
                  const y1 = a.y + NODE_HEIGHT / 2;
                  const y2 = b.y + NODE_HEIGHT / 2;
                  const offset = direct ? 24 : -32;
                  const path = a.x === b.x
                    ? `M ${a.x} ${y1} C ${a.x - 24} ${y1}, ${a.x - 24} ${y2}, ${b.x - 4} ${y2}`
                    : `M ${x1} ${y1} C ${x1 + offset} ${y1}, ${x2 - offset} ${y2}, ${x2} ${y2}`;
                  return <path key={edge.id} d={path} fill="none" markerEnd={`url(#${marker})`}
                    stroke={edge.status === "invalid" ? "#a75447" : highlighted ? "#426a51" : "#94a99c"}
                    strokeWidth={highlighted ? 2.4 : 1.3}
                    strokeDasharray={["hasPredecessor", "declared_predecessor", "direct_transition", "merge"].includes(edge.type) && edge.status === "verified" ? undefined : "4 3"}>
                    <title>{[relationLabel(edge), edge.condition].filter(Boolean).join(" · ")}</title>
                  </path>;
                })}
              </svg>
              {graph.nodes.map((node) => {
                const point = graph.points.get(node.id)!;
                return <button type="button" key={node.id}
                  className="graph-node" data-concept={node.concept} aria-pressed={node.id === selected}
                  style={{ left: point.x, top: point.y, width: NODE_WIDTH, height: NODE_HEIGHT }}
                  title={node.name} onClick={() => onSelect(node.id)}>
                  <small>{[node.number, node.operation_type].filter(Boolean).join(" · ") || groups.find(([key]) => model[key].includes(node))?.[1] || "产线"}</small>
                  <span>{node.name}</span>
                </button>;
              })}
            </div>
          </div>
        </div>}
    </section>
  );
}
