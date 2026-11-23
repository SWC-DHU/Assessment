import type { SemanticEntity, SemanticRelation } from "../types";
import type { Point } from "./useTopologyViewport";

export const semanticCard = { width: 146, height: 52 };

export interface DisplayRelation {
  key: string;
  source: string;
  target: string;
  offset: number;
  relations: SemanticRelation[];
}

const orderedPair = (source: string, target: string) => source <= target ? [source, target] : [target, source];

export function buildSemanticGraph(entities: ReadonlyMap<string, SemanticEntity>, relations: SemanticRelation[],
  focus: string | null, kind: string, compact: boolean) {
  const edges = relations.filter((edge) =>
    (!kind || edge.type === kind) && (!focus || edge.source_id === focus || edge.target_id === focus));
  const ids = new Set(focus ? [focus] : entities.keys());
  const incoming = new Set<string>();
  for (const edge of edges) {
    ids.add(edge.source_id);
    ids.add(edge.target_id);
    if (edge.target_id === focus) incoming.add(edge.source_id);
  }

  const buckets = new Map<string, string[]>();
  for (const id of ids) {
    if (id === focus) continue;
    const group = focus ? (incoming.has(id) ? "incoming" : "outgoing") : (entities.get(id)?.concept ?? "missing");
    const bucket = buckets.get(group) ?? [];
    bucket.push(id);
    buckets.set(group, bucket);
  }
  const points = new Map<string, Point>();
  if (focus) {
    points.set(focus, { x: 0, y: 0 });
    for (const [direction, bucket] of buckets) {
      const side = direction === "incoming" ? -1 : 1;
      bucket.forEach((id, index) => {
        points.set(id, compact ? { x: 0, y: side * (index + 1) * 118 } :
          { x: side * 232, y: (index - (bucket.length - 1) / 2) * 88 });
      });
    }
  } else {
    [...buckets.values()].forEach((bucket, column) => {
      bucket.forEach((id, row) => points.set(id, { x: column * 225, y: row * 87 }));
    });
  }

  const pairs = new Map<string, SemanticRelation[]>();
  for (const edge of edges) {
    const pair = JSON.stringify(orderedPair(edge.source_id, edge.target_id));
    const bucket = pairs.get(pair) ?? [];
    bucket.push(edge);
    pairs.set(pair, bucket);
  }
  const display = Array.from(pairs.values()).flatMap((bucket): DisplayRelation[] => {
    const first = bucket[0];
    if (bucket.length > 3) {
      return [{ key: first.id, source: first.source_id, target: first.target_id, offset: 0, relations: bucket }];
    }
    return bucket.map((edge, index) => ({
      key: edge.id, source: edge.source_id, target: edge.target_id,
      offset: (index - (bucket.length - 1) / 2) * 25, relations: [edge],
    }));
  });
  return { points, edges: display };
}

export function relationGeometry(from: Point, to: Point, source: string, target: string, offset: number, vertical = false) {
  const { width, height } = semanticCard;
  const a = { x: from.x + width / 2, y: from.y + height / 2 };
  const b = { x: to.x + width / 2, y: to.y + height / 2 };
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  if (!dx && !dy) {
    const rise = 44 + Math.abs(offset);
    const x = a.x + offset;
    return { path: `M ${x - 28} ${from.y} C ${x - 36} ${from.y - rise}, ${x + 36} ${from.y - rise}, ${x + 28} ${from.y - 3}`,
      label: { x, y: from.y - rise * 0.75 } };
  }
  if (vertical && Math.abs(dy) > 150 && Math.abs(dx) < width / 2) {
    const band = Math.round(Math.abs(dy) / 118);
    const side = band % 2 ? -1 : 1;
    const rail = (side === 1 ? Math.max(a.x, b.x) : Math.min(a.x, b.x)) +
      side * (width / 2 + Math.min(40, 16 + band * 4)) + offset / 3;
    const yNearPeer = Math.abs(from.y) > Math.abs(to.y) ? a.y : b.y;
    const start = a.x + side * width / 2;
    const end = b.x + side * (width / 2 + 3);
    return { path: `M ${start} ${a.y} H ${rail} V ${b.y} H ${end}`,
      label: { x: rail, y: yNearPeer + Math.sign((a.y + b.y) / 2 - yNearPeer) * 34 + offset / 2 } };
  }
  const distance = Math.hypot(dx, dy);
  const scale = Math.min(width / 2 / Math.abs(dx), height / 2 / Math.abs(dy), 0.44);
  const direction = source <= target ? 1 : -1;
  const normal = { x: -dy / distance * direction, y: dx / distance * direction };
  const shift = offset * 0.26;
  const start = { x: a.x + dx * scale + normal.x * shift, y: a.y + dy * scale + normal.y * shift };
  const end = { x: b.x - dx * (scale + 3 / distance) + normal.x * shift, y: b.y - dy * (scale + 3 / distance) + normal.y * shift };
  const center = { x: (start.x + end.x) / 2, y: (start.y + end.y) / 2 };
  const control = { x: center.x + normal.x * offset * 1.48, y: center.y + normal.y * offset * 1.48 };
  return { path: `M ${start.x} ${start.y} Q ${control.x} ${control.y} ${end.x} ${end.y}`,
    label: { x: (center.x + control.x) / 2, y: (center.y + control.y) / 2 } };
}
