import { useCallback, useEffect, useRef, useState } from "react";
import type { KeyboardEvent, PointerEvent } from "react";

export interface Point { x: number; y: number }
interface Camera extends Point { zoom: number }
interface NodePosition { id: string; point: Point }
interface Drag {
  pointerId: number;
  start: Point;
  camera: Camera;
  node?: NodePosition;
  moved: boolean;
}

const initialCamera: Camera = { x: 0, y: 0, zoom: 1 };
const dragThreshold = 4;
const keyboardPan: Partial<Record<string, Point>> = {
  ArrowLeft: { x: 48, y: 0 }, ArrowRight: { x: -48, y: 0 },
  ArrowUp: { x: 0, y: 48 }, ArrowDown: { x: 0, y: -48 },
};

export function useTopologyViewport(enabled: boolean, onMove?: (id: string, point: Point) => void) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const drag = useRef<Drag | null>(null);
  const ignorePointerClick = useRef(false);
  const [camera, setCamera] = useState(initialCamera);
  const [movingNode, setMovingNode] = useState<NodePosition | null>(null);
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!enabled || !viewport) return;

    const onWheel = (event: WheelEvent) => {
      if (event.ctrlKey || event.metaKey) return;
      event.preventDefault();
      if (drag.current) return;
      const horizontal = event.deltaX || (event.shiftKey ? event.deltaY : 0);
      const vertical = event.shiftKey ? 0 : event.deltaY;
      const xUnit = event.deltaMode === WheelEvent.DOM_DELTA_PAGE ? viewport.clientWidth :
        event.deltaMode === WheelEvent.DOM_DELTA_LINE ? 16 : 1;
      const yUnit = event.deltaMode === WheelEvent.DOM_DELTA_PAGE ? viewport.clientHeight : xUnit;
      setCamera((view) => ({ ...view, x: view.x - horizontal * xUnit, y: view.y - vertical * yUnit }));
    };

    viewport.addEventListener("wheel", onWheel, { passive: false });
    return () => viewport.removeEventListener("wheel", onWheel);
  }, [enabled]);

  const fit = useCallback((points: ReadonlyMap<string, Point>, nodeWidth: number, nodeHeight: number) => {
    const viewport = viewportRef.current;
    if (!viewport || !points.size || !viewport.clientWidth || !viewport.clientHeight) return;
    let left = Infinity;
    let top = Infinity;
    let right = -Infinity;
    let bottom = -Infinity;
    for (const point of points.values()) {
      left = Math.min(left, point.x);
      top = Math.min(top, point.y);
      right = Math.max(right, point.x + nodeWidth);
      bottom = Math.max(bottom, point.y + nodeHeight);
    }
    const { clientWidth: width, clientHeight: height } = viewport;
    const padding = Math.min(40, width / 4, height / 4);
    const zoom = Math.min(1, (width - 2 * padding) / (right - left), (height - 2 * padding) / (bottom - top));
    setCamera({ x: (width - (left + right) * zoom) / 2, y: (height - (top + bottom) * zoom) / 2, zoom });
  }, []);

  const changeZoom = (getZoom: (zoom: number) => number) => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const anchor = { x: viewport.clientWidth / 2, y: viewport.clientHeight / 2 };
    setCamera((view) => {
      const zoom = getZoom(view.zoom);
      const ratio = zoom / view.zoom;
      return { x: anchor.x + (view.x - anchor.x) * ratio, y: anchor.y + (view.y - anchor.y) * ratio, zoom };
    });
  };

  const zoomBy = (step: number) => changeZoom((zoom) => {
    const next = zoom < 0.4 ? zoom * (1 + step) : zoom + step;
    return Math.min(2, Math.max(Math.min(0.1, zoom), next));
  });
  const resetZoom = () => changeZoom(() => 1);

  const begin = (event: PointerEvent<HTMLElement>, node?: NodePosition) => {
    if (event.button !== 0 || !event.isPrimary || drag.current) return;
    event.stopPropagation();
    ignorePointerClick.current = false;
    if (!node) event.currentTarget.focus({ preventScroll: true });
    drag.current = {
      pointerId: event.pointerId, start: { x: event.clientX, y: event.clientY }, camera, node, moved: false,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const move = (event: PointerEvent<HTMLElement>) => {
    const current = drag.current;
    if (!current || current.pointerId !== event.pointerId) return;
    const dx = event.clientX - current.start.x;
    const dy = event.clientY - current.start.y;
    if (!current.moved) {
      if (Math.hypot(dx, dy) < dragThreshold) return;
      current.moved = true;
      setDragging(true);
    }
    if (current.node) {
      const { id, point } = current.node;
      setMovingNode({ id, point: { x: point.x + dx / current.camera.zoom, y: point.y + dy / current.camera.zoom } });
    } else {
      setCamera({ ...current.camera, x: current.camera.x + dx, y: current.camera.y + dy });
    }
  };

  const finish = (event: PointerEvent<HTMLElement>, cancelled = false) => {
    const current = drag.current;
    if (!current || current.pointerId !== event.pointerId) return;
    drag.current = null;
    ignorePointerClick.current = current.moved;
    setDragging(false);
    setMovingNode(null);
    if (!cancelled && current.moved && current.node) {
      const { id, point } = current.node;
      onMove?.(id, {
        x: point.x + (event.clientX - current.start.x) / current.camera.zoom,
        y: point.y + (event.clientY - current.start.y) / current.camera.zoom,
      });
    }
  };

  const panWithKeys = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.target !== event.currentTarget) return;
    const delta = keyboardPan[event.key];
    if (!delta) return;
    event.preventDefault();
    if (!drag.current) setCamera((view) => ({ ...view, x: view.x + delta.x, y: view.y + delta.y }));
  };

  const reveal = (point: Point, nodeWidth: number, nodeHeight: number) => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const { clientWidth: width, clientHeight: height } = viewport;
    setCamera((view) => {
      const x = view.x + point.x * view.zoom;
      const y = view.y + point.y * view.zoom;
      const right = x + nodeWidth * view.zoom;
      const bottom = y + nodeHeight * view.zoom;
      if (x >= 0 && y >= 0 && right <= width && bottom <= height) return view;
      return {
        ...view,
        x: (width - (2 * point.x + nodeWidth) * view.zoom) / 2,
        y: (height - (2 * point.y + nodeHeight) * view.zoom) / 2,
      };
    });
  };

  return { viewportRef, camera, movingNode, dragging, ignorePointerClick, fit, zoomBy, resetZoom, begin, move, finish, panWithKeys, reveal };
}
