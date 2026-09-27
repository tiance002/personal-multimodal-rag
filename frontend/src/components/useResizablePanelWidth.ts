import { useEffect, useRef, useState } from "react";

type ResizeState = {
  pointerId: number;
  startX: number;
  startWidth: number;
};

type Options = {
  open: boolean;
  initialWidth: number;
  minWidth: number;
  maxWidth?: number;
};

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

/**
 * Keeps side-panel resizing in one place. Pointer capture is intentional: the
 * preview body can contain an iframe, which otherwise steals pointer events
 * and leaves the resize state active after the pointer is released.
 */
export function useResizablePanelWidth({
  open,
  initialWidth,
  minWidth,
  maxWidth,
}: Options) {
  const [width, setWidth] = useState(initialWidth);
  const widthRef = useRef(initialWidth);
  const resizeRef = useRef<ResizeState | null>(null);

  useEffect(() => {
    widthRef.current = width;
  }, [width]);

  useEffect(() => {
    if (!open) {
      resizeRef.current = null;
      return;
    }
    const viewportMax = Math.max(240, window.innerWidth - 24);
    const boundedMax = Math.min(maxWidth ?? viewportMax, viewportMax);
    const boundedMin = Math.min(minWidth, boundedMax);
    const next = clamp(initialWidth, boundedMin, boundedMax);
    widthRef.current = next;
    setWidth(next);

    const stop = () => {
      resizeRef.current = null;
      window.document.body.style.userSelect = "";
      window.document.body.style.cursor = "";
    };
    const move = (event: PointerEvent) => {
      const resize = resizeRef.current;
      if (!resize || resize.pointerId !== event.pointerId) return;
      const availableMax = Math.min(
        maxWidth ?? Number.POSITIVE_INFINITY,
        Math.max(240, window.innerWidth - 24),
      );
      const availableMin = Math.min(minWidth, availableMax);
      const nextWidth = clamp(
        resize.startWidth + resize.startX - event.clientX,
        availableMin,
        availableMax,
      );
      widthRef.current = nextWidth;
      setWidth(nextWidth);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
    window.addEventListener("pointercancel", stop);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      window.removeEventListener("pointercancel", stop);
      stop();
    };
  }, [open]);

  const stopFromHandle = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!resizeRef.current || resizeRef.current.pointerId !== event.pointerId) {
      return;
    }
    resizeRef.current = null;
    window.document.body.style.userSelect = "";
    window.document.body.style.cursor = "";
    try {
      event.currentTarget.releasePointerCapture(event.pointerId);
    } catch {
      // The browser releases capture automatically after pointerup.
    }
  };

  const handleProps = {
    onPointerDown: (event: React.PointerEvent<HTMLDivElement>) => {
      if (event.button !== 0) return;
      event.preventDefault();
      resizeRef.current = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startWidth: widthRef.current,
      };
      window.document.body.style.userSelect = "none";
      window.document.body.style.cursor = "col-resize";
      try {
        event.currentTarget.setPointerCapture(event.pointerId);
      } catch {
        // Pointer capture is a progressive enhancement; window listeners are
        // still active as a fallback for browsers that do not support it.
      }
    },
    onPointerUp: stopFromHandle,
    onPointerCancel: stopFromHandle,
    onLostPointerCapture: stopFromHandle,
  };

  return { width, handleProps };
}
