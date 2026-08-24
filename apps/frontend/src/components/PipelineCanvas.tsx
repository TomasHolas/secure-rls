/**
 * PipelineCanvas - the query pipeline as a flowchart, and the chat's empty state (owner request,
 * pattern from [beautifului.dev](https://www.beautifului.dev) Flowchart, MIT). Six cards on a
 * dotted canvas say what happens to every SQL statement the model writes before a row comes back:
 * the model's statement, layers 2, 2.5, 3 and 4, and the rows that survive them. The copy is one
 * line per layer from the README's security table, so the screen and the docs cannot drift.
 *
 * The six steps themselves live in `pipelineSteps.ts`, which `chat/PipelineStrip` reads too: this
 * canvas is the MAP of the path and the strip is the JOURNEY one statement took down it, and two
 * drawings of one enforcement path may not keep two copies of its labels.
 *
 * It is the *static* pipeline, and that is why it is allowed to look like a plan: the order is
 * fixed in `db.py` and `security.py` and holds for every question, unlike an agent's run, whose
 * next tool is decided by what the last one returned (issue #91 rejected step rings for exactly
 * that reason - see `docs/ui-pattern-review.md`). Nothing here is fed by a turn.
 *
 * The connectors are **measured, never assumed**: each card reports its own box through a
 * `ResizeObserver`, the anchors come off those boxes, and each edge is one cubic bezier whose
 * control points are a fraction of the vertical span it crosses. So a card that wraps to a
 * second line, a collapsed conversation rail, or a 900px viewport moves the curves with it
 * rather than leaving them pointing at where the card used to be.
 *
 * Every visual metric - node width, row gap, canvas padding, dot spacing and colour - is a
 * custom property in `app.css`. What stays here are the three numbers that are pointer and
 * path math rather than style: the curvature of a connector, the pointer slop that separates a
 * drag from a click, and the inset a dragged card keeps from the canvas edge.
 *
 * A card being dragged has to be over the ones it passes, which is `.pipeline-node:hover` in the
 * stylesheet rather than a z-index this brick computes: the pointer that drags a card is on it by
 * definition, and the reference's own version of this reads a ref during render.
 *
 * Selection and dragging are one gesture apart, which the reference solves and this ports: a
 * pointer that moved past the slop marks the drag, and the click it ends with is swallowed, so
 * moving a card never also toggles it. One correction to the reference, found on a live click:
 * the pointer is captured by the card rather than by the node around it, because a capture
 * retargets the compatibility mouse events too - captured a level up, every click landed on the
 * node and the button under the pointer was never told it had been pressed. Cards are buttons carrying `aria-pressed`, so Enter and
 * Space toggle a selection without a key handler of our own, and the selected card lights the
 * connectors on both of its sides.
 */

import { useCallback, useLayoutEffect, useRef, useState } from "react";
import type { CSSProperties, PointerEvent as ReactPointerEvent } from "react";

import { Icon } from "./Icon";
import { PIPELINE_STEPS } from "./pipelineSteps";
import type { PipelineStep } from "./pipelineSteps";
import { Pill } from "./Pill";

/** How far a connector's control points reach along the gap it crosses. */
const CURVE_RATIO = 0.55;
/** Floor and ceiling on that reach: a short hop still bows, a long one does not loop. */
const CURVE_MIN = 24;
const CURVE_MAX = 84;
/** Pointer travel that turns a press into a drag, in px, below which it is still a click. */
const DRAG_SLOP = 3;
/** The gap a dragged card keeps from the canvas edge, in px. */
const DRAG_INSET = 8;

/** The pipeline is a chain, so the edges are the consecutive pairs of it. */
const EDGES = PIPELINE_STEPS.slice(1).map((step, index) => ({
  from: PIPELINE_STEPS[index].id,
  to: step.id,
}));

const LEAD =
  "Every question that reaches the data runs down this path, in this order, server-side. Select a step to light what it connects to - or drag a card.";

/** One card's own layout box, measured: the node's, plus where the card inside it sits. */
type Box = { left: number; top: number; width: number; height: number; card: number; cardH: number };

const within = (value: number, min: number, max: number) =>
  Math.min(Math.max(value, min), max);

export function PipelineCanvas() {
  const canvas = useRef<HTMLDivElement>(null);
  const nodes = useRef(new Map<string, HTMLElement>());
  const cards = useRef(new Map<string, HTMLElement>());
  const [boxes, setBoxes] = useState<Record<string, Box>>({});
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [selected, setSelected] = useState<string | null>(null);
  const [offsets, setOffsets] = useState<Record<string, { dx: number; dy: number }>>({});
  const drag = useRef<{
    id: string;
    startX: number;
    startY: number;
    baseDx: number;
    baseDy: number;
    moved: boolean;
  } | null>(null);

  const measure = useCallback(() => {
    const box = canvas.current;
    if (!box) return;
    setSize({ width: box.clientWidth, height: box.clientHeight });
    setBoxes((previous) => {
      const next: Record<string, Box> = {};
      nodes.current.forEach((node, id) => {
        const card = cards.current.get(id);
        next[id] = {
          left: node.offsetLeft,
          top: node.offsetTop,
          width: node.offsetWidth,
          height: node.offsetHeight,
          card: node.offsetTop + (card?.offsetTop ?? 0),
          cardH: card?.offsetHeight ?? node.offsetHeight,
        };
      });
      return same(previous, next) ? previous : next;
    });
  }, []);

  useLayoutEffect(() => {
    const box = canvas.current;
    if (!box) return;
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(box);
    nodes.current.forEach((node) => observer.observe(node));
    return () => observer.disconnect();
  }, [measure]);

  /**
   * A card's connector anchors. The card is centred on its 0-1 x by a -50% translate, so the
   * node's layout left *is* its centre line, and a drag offset moves both anchors together.
   */
  function anchors(id: string) {
    const box = boxes[id];
    if (!box) return null;
    const offset = offsets[id];
    const x = box.left + (offset?.dx ?? 0);
    const top = box.card + (offset?.dy ?? 0);
    return { top: { x, y: top }, bottom: { x, y: top + box.cardH } };
  }

  function bezier(edge: { from: string; to: string }): string | null {
    const from = anchors(edge.from)?.bottom;
    const to = anchors(edge.to)?.top;
    if (!from || !to) return null;
    const reach = within(Math.abs(to.y - from.y) * CURVE_RATIO, CURVE_MIN, CURVE_MAX);
    return `M ${from.x} ${from.y} C ${from.x} ${from.y + reach}, ${to.x} ${to.y - reach}, ${to.x} ${to.y}`;
  }

  const onPointerDown = (step: PipelineStep) => (event: ReactPointerEvent<HTMLDivElement>) => {
    const offset = offsets[step.id];
    drag.current = {
      id: step.id,
      startX: event.clientX,
      startY: event.clientY,
      baseDx: offset?.dx ?? 0,
      baseDy: offset?.dy ?? 0,
      moved: false,
    };
    // Captured on the CARD, not on this node: a capture retargets the click that ends the
    // gesture to whatever holds it, and a click landing on the node would never reach the button.
    cards.current.get(step.id)?.setPointerCapture(event.pointerId);
  };

  const onPointerMove = (step: PipelineStep) => (event: ReactPointerEvent<HTMLDivElement>) => {
    const held = drag.current;
    const box = boxes[step.id];
    if (!held || held.id !== step.id || !box) return;
    const dx = held.baseDx + event.clientX - held.startX;
    const dy = held.baseDy + event.clientY - held.startY;
    if (!held.moved && Math.hypot(dx - held.baseDx, dy - held.baseDy) < DRAG_SLOP) return;
    held.moved = true;
    // Clamped as the card's own edges, so no part of it can leave the canvas.
    const baseLeft = box.left - box.width / 2;
    const left = within(baseLeft + dx, DRAG_INSET, size.width - box.width - DRAG_INSET);
    const top = within(box.top + dy, DRAG_INSET, size.height - box.height - DRAG_INSET);
    setOffsets((current) => ({
      ...current,
      [step.id]: { dx: left - baseLeft, dy: top - box.top },
    }));
  };

  const onPointerUp = (step: PipelineStep) => () => {
    const held = drag.current;
    if (held?.id !== step.id) return;
    // A drag ends in a click, and that click must not toggle: cleared after it has been swallowed.
    if (held.moved) setTimeout(() => (drag.current = null), 0);
    else drag.current = null;
  };

  return (
    <div className="pipeline">
      <p className="pipeline-lead">{LEAD}</p>
      <div ref={canvas} className="pipeline-canvas">
        <svg className="pipeline-edges" aria-hidden="true">
          {EDGES.map((edge) => {
            const path = bezier(edge);
            const lit = selected === edge.from || selected === edge.to;
            return path === null ? null : (
              <path
                key={`${edge.from}-${edge.to}`}
                className={`pipeline-edge${lit ? " lit" : ""}`}
                d={path}
              />
            );
          })}
        </svg>
        {PIPELINE_STEPS.map((step) => {
          const offset = offsets[step.id];
          const active = selected === step.id;
          return (
            <div
              key={step.id}
              ref={(el) => {
                if (el) nodes.current.set(step.id, el);
                else nodes.current.delete(step.id);
              }}
              className="pipeline-node"
              style={
                {
                  "--pipe-x": step.x,
                  "--pipe-dx": `${offset?.dx ?? 0}px`,
                  "--pipe-dy": `${offset?.dy ?? 0}px`,
                } as CSSProperties
              }
              onPointerDown={onPointerDown(step)}
              onPointerMove={onPointerMove(step)}
              onPointerUp={onPointerUp(step)}
              onPointerCancel={onPointerUp(step)}
            >
              <Pill tone="neutral">{step.kind}</Pill>
              <button
                type="button"
                ref={(el) => {
                  if (el) cards.current.set(step.id, el);
                  else cards.current.delete(step.id);
                }}
                className={`pipeline-card${active ? " selected" : ""}`}
                aria-pressed={active}
                onClick={() => {
                  if (drag.current?.moved) return;
                  setSelected(active ? null : step.id);
                }}
                style={{ "--pipe-hue": step.hue } as CSSProperties}
              >
                <span className="pipeline-glyph">
                  <Icon name={step.icon} size={18} />
                </span>
                <span className="pipeline-copy">
                  <span className="pipeline-title">{step.title}</span>
                  <span className="pipeline-mech">{step.mechanism}</span>
                </span>
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** Measured boxes are compared before they are stored, so a resize that changed nothing re-renders nothing. */
function same(a: Record<string, Box>, b: Record<string, Box>): boolean {
  const ids = Object.keys(b);
  if (ids.length !== Object.keys(a).length) return false;
  return ids.every((id) => {
    const one = a[id];
    const two = b[id];
    return (
      one !== undefined &&
      one.left === two.left &&
      one.top === two.top &&
      one.width === two.width &&
      one.height === two.height &&
      one.card === two.card &&
      one.cardH === two.cardH
    );
  });
}
