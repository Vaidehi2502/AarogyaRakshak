import { useEffect, useRef, useState } from "react";
import "./SeverOverlay.css";

/**
 * The one animated flourish: when a decision arrives with a failing check
 * that carries a `source` citation, draw a line from that check's ledger
 * row to the highlighted span in the context pane, pulse the span, then
 * snap the line in half. Draw 300ms + pulse 200ms + snap 180ms = 680ms,
 * timed to land while the ledger's own stamp-in (Step 1, always running)
 * is still finishing its later rows - it reads as one event, not two.
 *
 * Geometry is computed once per trigger via getBoundingClientRect() on
 * the two DOM nodes the caller marks with data-failing-check and
 * data-injected-span - no layout is read on every frame, only at the
 * start of the sequence.
 */
export default function SeverOverlay({ ledgerRef, contextRef, decision }) {
  const [geom, setGeom] = useState(null);
  const [phase, setPhase] = useState("idle"); // idle | draw-in | draw | snap
  const timers = useRef([]);

  useEffect(() => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
    setPhase("idle");
    setGeom(null);

    if (!decision) return;
    const failing = decision.checks?.find((c) => !c.passed && c.source);
    if (!failing) return;

    // The DOM is already committed by the time an effect runs, so the
    // geometry read doesn't need to wait a frame - only the dashoffset
    // flip (below) does, to give the browser a paint with the "hidden"
    // value before transitioning. setTimeout, not requestAnimationFrame,
    // does that flip: rAF is fully suspended by Chrome while the tab is
    // backgrounded/hidden, which would silently freeze the whole
    // sequence any time the viewer's tab loses visibility.
    const rowEl = ledgerRef.current?.querySelector("[data-failing-check]");
    const spanEl = contextRef.current?.querySelector("[data-injected-span]");
    if (!rowEl || !spanEl) return;

    const a = rowEl.getBoundingClientRect();
    const b = spanEl.getBoundingClientRect();
    const x1 = a.right;
    const y1 = a.top + a.height / 2;
    const x2 = b.left;
    const y2 = b.top + b.height / 2;
    const length = Math.hypot(x2 - x1, y2 - y1);

    setGeom({ x1, y1, x2, y2, mx: (x1 + x2) / 2, my: (y1 + y2) / 2, length });
    setPhase("draw-in"); // one tick with dashoffset = length (line hidden)

    timers.current.push(
      setTimeout(() => {
        setPhase("draw"); // next tick: transition to 0
        timers.current.push(
          setTimeout(() => {
            spanEl.classList.add("pulse");
            timers.current.push(
              setTimeout(() => {
                spanEl.classList.remove("pulse");
                setPhase("snap");
                timers.current.push(setTimeout(() => setPhase("idle"), 180));
              }, 200)
            );
          }, 300)
        );
      }, 0)
    );

    return () => {
      timers.current.forEach((t) => clearTimeout(t));
    };
  }, [decision?._seq]);

  if (!geom || phase === "idle") return null;

  const { x1, y1, x2, y2, mx, my, length } = geom;

  if (phase === "draw-in" || phase === "draw") {
    return (
      <svg className="sever-overlay">
        <path
          className="sever-line"
          d={`M ${x1} ${y1} L ${x2} ${y2}`}
          style={{
            strokeDasharray: length,
            strokeDashoffset: phase === "draw-in" ? length : 0,
          }}
        />
      </svg>
    );
  }

  const dirA = { x: x1 - mx, y: y1 - my };
  const magA = Math.hypot(dirA.x, dirA.y) || 1;
  const dirB = { x: x2 - mx, y: y2 - my };
  const magB = Math.hypot(dirB.x, dirB.y) || 1;

  return (
    <svg className="sever-overlay">
      <path
        className="sever-half snap"
        d={`M ${x1} ${y1} L ${mx} ${my}`}
        style={{ "--dx": `${(dirA.x / magA) * 12}px`, "--dy": `${(dirA.y / magA) * 12}px` }}
      />
      <path
        className="sever-half snap"
        d={`M ${mx} ${my} L ${x2} ${y2}`}
        style={{ "--dx": `${(dirB.x / magB) * 12}px`, "--dy": `${(dirB.y / magB) * 12}px` }}
      />
    </svg>
  );
}
