import { useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Empty, Select } from "antd";
import { ArrowUpToLine, Maximize, Minus, Plus, RotateCcw } from "lucide-react";
import type { SemanticModel } from "../types";
import { relationLabel } from "./SemanticGraph";
import { layoutTopology, topologyCard, topologyPath } from "./topologyLayout";
import { useTopologyViewport } from "./useTopologyViewport";
import type { Point } from "./useTopologyViewport";

const solidKinds = new Set(["declared_predecessor", "direct_transition", "merge", "branch"]);
const { width: cardWidth, height: cardHeight } = topologyCard;

interface ProcessTopologyProps {
  model: SemanticModel;
  selected: string | null;
  positions: ReadonlyMap<string, Point>;
  onSelect: (id: string | null) => void;
  onMove: (id: string, point: Point) => void;
  renderDetail?: (close: () => void) => ReactNode;
}

export function ProcessTopology({ model, selected, positions, onSelect, onMove, renderDetail }: ProcessTopologyProps) {
  const graph = useMemo(() => layoutTopology(model), [model]);
  const [located, setLocated] = useState<string>();
  const [returnToStart, setReturnToStart] = useState(false);
  const marker = useId();
  const viewport = useTopologyViewport(graph.nodes.length > 0, onMove);
  const { camera, viewportRef, frameNode, movingNode } = viewport;
  const viewportReady = useRef(false);
  const hasDetail = Boolean(renderDetail);
  const initialPoint = graph.start ? positions.get(graph.start) ?? graph.points.get(graph.start) : undefined;
  const points = useMemo(() => {
    const displayed = new Map(graph.points);
    positions.forEach((point, id) => { if (displayed.has(id)) displayed.set(id, point); });
    if (movingNode && displayed.has(movingNode.id)) displayed.set(movingNode.id, movingNode.point);
    return displayed;
  }, [graph.points, positions, movingNode]);

  useLayoutEffect(() => {
    const element = viewportRef.current;
    if (!element || !initialPoint || viewportReady.current) return;
    const initialize = () => {
      if (!element.clientWidth || !element.clientHeight) return false;
      viewportReady.current = true;
      frameNode(initialPoint, cardWidth, cardHeight, "start");
      return true;
    };
    if (initialize()) return;
    const observer = new ResizeObserver(() => { if (initialize()) observer.disconnect(); });
    observer.observe(element);
    return () => observer.disconnect();
  }, [frameNode, initialPoint, viewportRef]);

  const selectedPoint = selected ? positions.get(selected) ?? graph.points.get(selected) : undefined;
  const lastSelectedPoint = useRef(selectedPoint);
  useLayoutEffect(() => {
    lastSelectedPoint.current = selectedPoint;
  }, [selectedPoint]);
  useLayoutEffect(() => {
    const element = viewportRef.current;
    if (!element || !hasDetail || !selected) return;
    const keepSelectedVisible = () => {
      const point = lastSelectedPoint.current;
      if (point) frameNode(point, cardWidth, cardHeight, "reveal");
    };
    keepSelectedVisible();
    const observer = new ResizeObserver(keepSelectedVisible);
    observer.observe(element);
    return () => observer.disconnect();
  }, [frameNode, hasDetail, selected, viewportRef]);

  const goToStart = () => {
    setLocated(undefined);
    onSelect(null);
    setReturnToStart(true);
  };

  useLayoutEffect(() => {
    if (!returnToStart) return;
    const point = graph.start && points.get(graph.start);
    if (point) frameNode(point, cardWidth, cardHeight, "start");
    setReturnToStart(false);
  }, [frameNode, graph.start, points, returnToStart]);

  const locate = (id: string | undefined) => {
    setLocated(id);
    const point = id && points.get(id);
    if (id && point) {
      if (hasDetail && selected !== id) onSelect(id);
      frameNode(point, cardWidth, cardHeight, "locate");
    }
  };

  const close = () => {
    viewportRef.current?.focus({ preventScroll: true });
    onSelect(null);
  };

  const gridSize = Math.max(10, 20 * camera.zoom);
  const highlighted = located ?? selected;

  return <section className="topology-graph" aria-label="工艺拓扑">
    <div className="topology-toolbar">
      <Select aria-label="搜索工序" size="small" showSearch allowClear optionFilterProp="label"
        placeholder="按编号或名称搜索工序" value={located} onChange={locate}
        disabled={!graph.nodes.length} options={graph.nodes.map((node) => ({
          value: node.id, label: [node.number, node.name].filter(Boolean).join(" · "),
        }))} />
      <button type="button" className="topology-control" disabled={!graph.start} onClick={goToStart}>
        <ArrowUpToLine size={14} aria-hidden="true" />回到起点
      </button>
    </div>
    {graph.nodes.length ? <>
      <div className="graph-legend"><span>直接、分支及汇合</span><span>条件、返工及其他关系</span></div>
      <div className="topology-workspace" data-detail={hasDetail}>
        <div className="topology-board">
          <div className="topology-viewport" ref={viewportRef} data-dragging={viewport.dragging} tabIndex={0}
            role="region" aria-label="工艺拓扑画布"
            aria-description="滚轮或方向键浏览，拖动空白处平移，拖动工序卡片调整位置。"
            style={{ backgroundPosition: `${camera.x}px ${camera.y}px`, backgroundSize: `${gridSize}px ${gridSize}px` }}
            onPointerDown={(event) => viewport.begin(event)} onPointerMove={viewport.move}
            onPointerUp={(event) => viewport.finish(event)} onPointerCancel={(event) => viewport.finish(event, true)}
            onLostPointerCapture={(event) => viewport.finish(event, true)} onKeyDown={viewport.panWithKeys}>
            <div className="topology-world" style={{ transform: `translate(${camera.x}px, ${camera.y}px) scale(${camera.zoom})` }}>
              <svg width="1" height="1" aria-hidden="true">
                <defs><marker id={marker} markerWidth="7" markerHeight="7" refX="5.5" refY="3.5" orient="auto">
                  <path d="M 1 1 L 5.5 3.5 L 1 6" fill="none" stroke="context-stroke" strokeWidth="1.15" />
                </marker></defs>
                {graph.edges.map((edge) => <path key={edge.id} data-source={edge.source} data-target={edge.target}
                  d={topologyPath(points.get(edge.source)!, points.get(edge.target)!, edge.kind, graph.returnRail)}
                  fill="none" markerEnd={`url(#${marker})`}
                  className="topology-edge" data-status={edge.status}
                  data-highlighted={highlighted === edge.source || highlighted === edge.target}
                  strokeDasharray={edge.status === "verified" && solidKinds.has(edge.kind) ? undefined : "5 4"}>
                  <title>{[relationLabel({ type: edge.kind }), edge.condition].filter(Boolean).join(" · ")}</title>
                </path>)}
              </svg>
              {graph.nodes.map((node) => {
                const { id } = node;
                const point = points.get(id)!;
                return <button type="button" key={id} className="topology-node" data-operation={id}
                  data-category={graph.categories.get(id)} data-located={located === id} aria-pressed={selected === id}
                  aria-label={[node.number, node.operation_type, node.name].filter(Boolean).join(" · ")}
                  style={{ left: point.x, top: point.y, width: cardWidth, height: cardHeight }} title={node.name}
                  onPointerDown={(event) => viewport.begin(event, { id, point })}
                  onFocus={(event) => {
                    if (event.currentTarget.matches(":focus-visible")) frameNode(point, cardWidth, cardHeight, "reveal");
                  }}
                  onClick={(event) => {
                    if (event.detail !== 0 && viewport.ignorePointerClick.current) return;
                    setLocated(undefined);
                    onSelect(id);
                  }}>
                  {node.number && <small>{node.number}</small>}<span>{node.name}</span>
                </button>;
              })}
            </div>
          </div>
          <div className="topology-controls" role="group" aria-label="画布缩放">
            <button type="button" aria-label="放大" title="放大" onClick={() => viewport.zoomBy(0.2)}><Plus size={16} /></button>
            <output>{Math.max(1, Math.round(camera.zoom * 100))}%</output>
            <button type="button" aria-label="缩小" title="缩小" onClick={() => viewport.zoomBy(-0.2)}><Minus size={16} /></button>
            <button type="button" aria-label="重置缩放" title="重置缩放" onClick={viewport.resetZoom}><RotateCcw size={14} /></button>
            <button type="button" className="topology-fit" aria-label="适应画布" title="适应画布" onClick={() => viewport.fit(points, cardWidth, cardHeight)}>
              <Maximize size={15} aria-hidden="true" />
            </button>
          </div>
        </div>
        {renderDetail && <aside className="topology-detail" aria-label="拓扑节点详情">{renderDetail(close)}</aside>}
      </div>
    </> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前版本没有可显示的节点" />}
  </section>;
}
