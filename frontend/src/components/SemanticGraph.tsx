import { useId, useLayoutEffect, useMemo, useState } from "react";
import { LocateFixed, Maximize, Minus, Plus } from "lucide-react";
import type { SemanticEntity, SemanticRelation } from "../types";
import { buildSemanticGraph, relationGeometry, semanticCard } from "./semanticGraphLayout";
import { conceptLabel, entityTitle, relationLabel, relationTitle } from "./semanticPresentation";
import { statusLabels } from "./SemanticDetails";
import { useTopologyViewport } from "./useTopologyViewport";
import type { Point } from "./useTopologyViewport";

const { width, height } = semanticCard;
const emptyPositions: ReadonlyMap<string, Point> = new Map();

interface SemanticGraphProps {
  entities: ReadonlyMap<string, SemanticEntity>;
  relations: SemanticRelation[];
  focus: string | null;
  kind: string;
  selectedEntity: string | null;
  selectedRelations: string[];
  detailOpen: boolean;
  onSelectEntity: (id: string) => void;
  onSelectRelations: (edges: SemanticRelation[]) => void;
}

export function SemanticGraph({ entities, relations, focus, kind, selectedEntity, selectedRelations,
  detailOpen, onSelectEntity, onSelectRelations }: SemanticGraphProps) {
  const [dimensions, setDimensions] = useState({ width: 0, height: 0 });
  const compact = dimensions.width < 650;
  const graph = useMemo(() => buildSemanticGraph(entities, relations, focus, kind, compact), [entities, relations, focus, kind, compact]);
  const layoutKey = JSON.stringify([focus, compact]);
  const [positions, setPositions] = useState(() => new Map<string, ReadonlyMap<string, Point>>());
  const viewport = useTopologyViewport(true, (id, point) => setPositions((previous) => {
    const changes = new Map(previous.get(layoutKey));
    changes.set(id, point);
    return new Map(previous).set(layoutKey, changes);
  }));
  const { viewportRef, frameNode, fit, camera, movingNode } = viewport;
  const marker = useId();
  const saved = positions.get(layoutKey) ?? emptyPositions;
  const points = useMemo(() => {
    const displayed = new Map(graph.points);
    saved.forEach((point, id) => { if (displayed.has(id)) displayed.set(id, point); });
    if (movingNode && displayed.has(movingNode.id)) displayed.set(movingNode.id, movingNode.point);
    return displayed;
  }, [graph.points, saved, movingNode]);

  useLayoutEffect(() => {
    const element = viewportRef.current;
    if (!element) return;
    const measure = () => {
      const { clientWidth, clientHeight } = element;
      setDimensions((current) => current.width === clientWidth && current.height === clientHeight ? current :
        { width: clientWidth, height: clientHeight });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [viewportRef]);

  const displayedSelection = selectedEntity ? points.get(selectedEntity) : undefined;
  const activePath = selectedRelations.length ? graph.edges.find((item) => item.relations.some(({ id }) => id === selectedRelations[0])) : undefined;
  const geometry = (source: string, target: string, offset: number) =>
    relationGeometry(points.get(source)!, points.get(target)!, source, target, offset, compact && Boolean(focus));
  const activePoint = activePath ? geometry(activePath.source, activePath.target, activePath.offset).label : undefined;

  useLayoutEffect(() => {
    if (!dimensions.width || !dimensions.height) return;
    const target = focus ? graph.points.get(focus) : undefined;
    if (!target) fit(graph.points, width, height);
    else if (compact && dimensions.height < 345) {
      const neighbor = [...graph.points].filter(([id]) => id !== focus).sort(([, a], [, b]) =>
        Math.hypot(a.x - target.x, a.y - target.y) - Math.hypot(b.x - target.x, b.y - target.y))[0];
      fit(new Map([[focus!, target], ...(neighbor ? [neighbor] : [])]), width, height);
    }
    else frameNode(target, width, height, "locate");
  }, [focus, graph, compact, dimensions.width > 0, dimensions.height > 0, frameNode, fit]);

  // Revealing a selected item on resize keeps the user's camera position whenever it still fits.
  useLayoutEffect(() => {
    if (!detailOpen || !dimensions.width || !dimensions.height) return;
    if (compact && dimensions.height < 345 && activePath) {
      fit(new Map([[activePath.source, points.get(activePath.source)!], [activePath.target, points.get(activePath.target)!]]), width, height);
    }
    else if (points.size <= 3 && dimensions.height < 320 && focus) fit(points, width, height);
    else if (activePoint) frameNode(activePoint, 0, 0, "reveal");
    else if (displayedSelection) frameNode(displayedSelection, width, height, "reveal");
  }, [detailOpen, dimensions, compact, selectedEntity, activePath?.key, frameNode, fit]);

  const focusPoint = focus ? points.get(focus) : undefined;
  const frame = () => {
    if (focusPoint && !(points.size <= 3 && dimensions.height < 320)) frameNode(focusPoint, width, height, "locate");
    else fit(points, width, height);
  };
  const grid = Math.max(10, 20 * camera.zoom);
  return <div className="semantic-board">
    <div className="semantic-viewport topology-viewport" role="region" aria-label="语义关系图" tabIndex={0}
      ref={viewportRef} data-dragging={viewport.dragging}
      aria-description="拖动空白平移，拖动节点调整位置。滚轮纵向浏览，Shift 加滚轮横向浏览。"
      style={{ backgroundPosition: `${camera.x}px ${camera.y}px`, backgroundSize: `${grid}px ${grid}px` }}
      onPointerDown={(event) => viewport.begin(event)} onPointerMove={viewport.move}
      onPointerUp={(event) => viewport.finish(event)} onPointerCancel={(event) => viewport.finish(event, true)}
      onLostPointerCapture={(event) => viewport.finish(event, true)} onKeyDown={viewport.panWithKeys}>
      <div className="topology-world semantic-world" style={{ transform: `translate(${camera.x}px, ${camera.y}px) scale(${camera.zoom})` }}>
        <svg width="1" height="1" className="semantic-edges">
          <defs><marker id={marker} markerWidth="7" markerHeight="7" refX="5.5" refY="3.5" orient="auto-start-reverse">
            <path d="M 1 1 L 5.5 3.5 L 1 6" fill="none" stroke="context-stroke" strokeWidth="1.15" />
          </marker></defs>
          {graph.edges.map(({ key, source, target, relations: edges, offset }) => {
            const edge = edges[0];
            const selected = edges.some((item) => selectedRelations.includes(item.id));
            const bundle = edges.length > 1;
            const states = new Set(edges.map((item) => item.status));
            const status = states.size === 1 ? edge.status : "mixed";
            const problem = states.has("invalid") ? "invalid" : states.has("conflict") ? "conflict" : undefined;
            const badge = bundle ? (states.has("invalid") ? "含无效关系" : states.has("conflict") ? "含冲突关系" :
              states.size > 1 ? "状态不一" : "") : status === "verified" ? "" : statusLabels[status];
            const label = bundle ? `${edges.length} 条关系` : relationLabel(edge);
            const title = bundle ? `${label} · ${edges.map((item) => relationTitle(item, entities)).join("、")}` :
              `${relationTitle(edge, entities)} · ${statusLabels[edge.status]}`;
            const { path, label: point } = geometry(source, target, offset);
            const select = () => onSelectRelations(edges);
            const reverse = bundle && source !== target && edges.some((item) => item.source_id === target);
            return <g key={key} role="button" tabIndex={0} aria-label={title} aria-pressed={selected}
              data-status={status} data-problem={problem} data-relation={bundle ? undefined : edge.id} data-bundle={bundle}
              className="semantic-edge" onPointerDown={(event) => event.stopPropagation()} onClick={select}
              onFocus={(event) => { if (event.currentTarget.matches(":focus-visible")) frameNode(point, 0, 0, "reveal"); }}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") { event.preventDefault(); select(); }
              }}>
              <title>{title}</title>
              <path className="semantic-edge-hit" d={path} />
              <path className="semantic-edge-stroke" d={path} markerEnd={`url(#${marker})`}
                markerStart={reverse ? `url(#${marker})` : undefined} />
              <text className="semantic-edge-label" data-visible={Boolean(focus) || selected || Boolean(problem)}
                x={point.x} y={point.y - 3} textAnchor="middle">
                {label}{badge && <tspan x={point.x} dy={12}>{badge}</tspan>}
              </text>
            </g>;
          })}
        </svg>
        {[...points].map(([id, point]) => {
          const entity = entities.get(id);
          const position = { left: point.x, top: point.y, width, height };
          if (!entity) return <div className="semantic-node semantic-unresolved" key={id} style={position}>
            <small>未解析实体</small><span title={id}>{id}</span>
          </div>;
          return <button type="button" key={id} className="semantic-node" style={position} data-entity={id}
            data-concept={entity.concept} data-center={id === focus} aria-pressed={id === selectedEntity} title={entityTitle(entity)}
            onPointerDown={(event) => viewport.begin(event, { id, point })}
            onFocus={(event) => { if (event.currentTarget.matches(":focus-visible")) frameNode(point, width, height, "reveal"); }}
            onClick={(event) => {
              if (event.detail === 0 || !viewport.ignorePointerClick.current) onSelectEntity(id);
            }}>
            <small>{conceptLabel(entity.concept)}</small><span>{entityTitle(entity)}</span>
          </button>;
        })}
      </div>
    </div>
    {!graph.edges.length && <output className="semantic-graph-empty">{kind ? "没有符合筛选条件的关系" : "当前范围没有直接关系"}</output>}
    <div className="topology-controls" role="group" aria-label="语义画布工具">
      <button type="button" aria-label="放大" title="放大" onClick={() => viewport.zoomBy(0.2)}><Plus size={16} /></button>
      <output>{Math.max(1, Math.round(camera.zoom * 100))}%</output>
      <button type="button" aria-label="缩小" title="缩小" onClick={() => viewport.zoomBy(-0.2)}><Minus size={16} /></button>
      {focus && <button type="button" aria-label="回到焦点" title="回到焦点" onClick={frame}><LocateFixed size={15} /></button>}
      <button type="button" title="适应画布" aria-label="适应画布" onClick={() => fit(points, width, height)}><Maximize size={15} /></button>
    </div>
  </div>;
}
