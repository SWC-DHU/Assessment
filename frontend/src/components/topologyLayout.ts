import type { SemanticModel, SemanticRelation } from "../types";
import type { Point } from "./useTopologyViewport";

export const topologyCard = { width: 166, height: 48 };
const rowSpacing = 94;
const columnSpacing = 200;

const legacyKinds = new Map([
  ["hasPredecessor", "declared_predecessor"], ["directlyPrecedes", "direct_transition"],
  ["branchesTo", "branch"], ["mergesInto", "merge"], ["conditionallyPrecedes", "conditional"],
  ["reworksTo", "rework"], ["optionalNext", "optional"], ["precedes", "precedence"], ["dependsOn", "dependency"],
]);

const operationCategories = new Map([
  ["主线工序", "main"], ["主线", "main"], ["main", "main"],
  ["汇合工序", "merge"], ["汇合", "merge"], ["merge", "merge"],
  ["旁路工序", "bypass"], ["旁路", "bypass"], ["bypass", "bypass"],
  ["可选工序", "optional"], ["可选", "optional"], ["optional", "optional"],
]);

export interface TopologyEdge {
  id: string;
  source: string;
  target: string;
  kind: string;
  status: SemanticRelation["status"];
  condition?: string | null;
}

function legacyEdge(relation: SemanticRelation): TopologyEdge[] {
  const kind = legacyKinds.get(relation.type);
  if (!kind) return [];
  const reversed = kind === "declared_predecessor" || kind === "dependency";
  return [{ id: relation.id, kind, status: relation.status, condition: relation.condition,
    source: reversed ? relation.target_id : relation.source_id,
    target: reversed ? relation.source_id : relation.target_id }];
}

function orderOperations(ids: string[], outgoing: Map<string, string[]>) {
  const inDegree = new Map(ids.map((id) => [id, 0]));
  const remaining = new Set(ids);
  outgoing.forEach((targets) => targets.forEach((target) => inDegree.set(target, inDegree.get(target)! + 1)));
  const order: string[] = [];
  while (remaining.size) {
    // A malformed forward cycle still needs a stable drawing. Keep its edges for display.
    const id = ids.find((candidate) => remaining.has(candidate) && inDegree.get(candidate) === 0)
      ?? remaining.values().next().value!;
    remaining.delete(id);
    order.push(id);
    outgoing.get(id)!.forEach((target) => inDegree.set(target, inDegree.get(target)! - 1));
  }
  return order;
}

function mainColumn(order: string[], incoming: Map<string, string[]>, categories: Map<string, string>) {
  const isMain = (id: string) => categories.get(id) === "main" || categories.get(id) === "merge";
  const main = new Set(order.filter(isMain));
  const paths = new Map<string, { length: number; declared: number; previous?: string }>();
  let best: string | undefined;
  const better = (a: { length: number; declared: number }, b: { length: number; declared: number }) =>
    a.declared > b.declared || (a.declared === b.declared && a.length > b.length);

  for (const id of order) {
    if (categories.get(id) === "bypass" || categories.get(id) === "optional") continue;
    const own = Number(isMain(id));
    let path: { length: number; declared: number; previous?: string } = { length: 1, declared: own };
    for (const predecessor of incoming.get(id)!) {
      const previous = paths.get(predecessor);
      if (!previous) continue;
      const candidate = { length: previous.length + 1, declared: previous.declared + own, previous: predecessor };
      if (better(candidate, path)) path = candidate;
    }
    paths.set(id, path);
    if (!best || better(path, paths.get(best)!)) best = id;
  }
  if (!main.size && (!best || paths.get(best)!.length === 1)) {
    return new Set(paths.size ? paths.keys() : order);
  }
  while (best) {
    main.add(best);
    best = paths.get(best)?.previous;
  }
  return main;
}

export function layoutTopology(model: SemanticModel) {
  const nodes = model.operations;
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const graphCategories = new Map(model.process_graph?.nodes.map(({ id, category }) => [id, category]));
  const categories = new Map(nodes.map((node) => [node.id,
    graphCategories.get(node.id) ?? operationCategories.get(node.operation_type?.trim().toLowerCase() ?? "") ?? "unspecified"]));
  const ids = nodes.map(({ id }) => id);
  const edges: TopologyEdge[] = (model.process_graph?.edges ?? model.relations.flatMap(legacyEdge))
    .filter(({ source, target }) => categories.has(source) && categories.has(target));
  const outgoing = new Map(ids.map((id) => [id, [] as string[]]));
  edges.forEach(({ source, target, kind, status }) => {
    if (status === "verified" && kind !== "rework" && kind !== "dependency") outgoing.get(source)!.push(target);
  });
  const order = orderOperations(ids, outgoing);
  const orderIndex = new Map(order.map((id, index) => [id, index]));
  const incoming = new Map(ids.map((id) => [id, [] as string[]]));
  outgoing.forEach((targets, source) => {
    const forward = targets.filter((target) => orderIndex.get(source)! < orderIndex.get(target)!);
    outgoing.set(source, forward);
    forward.forEach((target) => incoming.get(target)!.push(source));
  });

  const main = mainColumn(order, incoming, categories);
  const backbone = order.filter((id) => main.has(id));
  const rows = new Map(backbone.map((id, index) => [id, index]));
  for (const id of [...order].reverse()) {
    if (main.has(id)) continue;
    const targets = outgoing.get(id)!.flatMap((target) => rows.has(target) ? [rows.get(target)!] : []);
    if (targets.length) rows.set(id, Math.min(...targets) - 1);
  }
  for (const id of order) {
    if (rows.has(id)) continue;
    const sources = incoming.get(id)!.map((source) => rows.get(source)!);
    rows.set(id, sources.length ? Math.max(...sources) + 1 : 0);
  }

  const lanes = new Map<number, number>();
  const points = new Map(order.map((id) => {
    const row = rows.get(id)!;
    if (main.has(id)) return [id, { x: 0, y: row * rowSpacing }] as const;
    const index = lanes.get(row) ?? 0;
    lanes.set(row, index + 1);
    const lane = (index % 2 === 0 ? -1 : 1) * (Math.floor(index / 2) + 1);
    return [id, { x: lane * columnSpacing, y: row * rowSpacing }] as const;
  }));

  const returnRail = Math.max(0, ...Array.from(points.values(), (point) => point.x)) + topologyCard.width + 22;
  return { nodes: order.map((id) => byId.get(id)!), points, edges, categories, start: backbone[0], returnRail };
}

export function topologyPath(from: Point, to: Point, kind: string, returnRail: number) {
  const { width, height } = topologyCard;
  const x1 = from.x + width / 2;
  const x2 = to.x + width / 2;
  const y1 = from.y + height;
  const y2 = to.y - 3;
  if (kind === "rework" || to.y < from.y) {
    const right = Math.max(returnRail, from.x + width + 22, to.x + width + 22);
    return `M ${x1} ${y1} V ${y1 + 12} H ${right} V ${y2 - 12} H ${x2} V ${y2}`;
  }
  if (to.y - from.y < height + 8 && (Math.abs(to.x - from.x) >= width || to.y === from.y)) {
    const direction = to.x > from.x ? 1 : -1;
    const start = from.x + (direction === 1 ? width : 0);
    const end = to.x + (direction === 1 ? -3 : width + 3);
    const middle = (start + end) / 2;
    const sourceY = from.y + height / 2;
    const targetY = to.y + height / 2;
    return `M ${start} ${sourceY} C ${middle} ${sourceY}, ${middle} ${targetY}, ${end} ${targetY}`;
  }
  if (to.y - from.y > rowSpacing + height) {
    const rail = to.x + (from.x < to.x ? -14 : width + 14);
    return `M ${x1} ${y1} V ${y1 + 12} H ${rail} V ${y2 - 12} H ${x2} V ${y2}`;
  }
  if (x1 === x2) return `M ${x1} ${y1} V ${y2}`;
  const middle = (y1 + y2) / 2;
  return `M ${x1} ${y1} C ${x1} ${middle}, ${x2} ${middle}, ${x2} ${y2}`;
}
