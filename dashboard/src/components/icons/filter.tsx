import { m, type Transition } from "motion/react";
import { AnimatedIcon, useIconControls, type IconProps } from "./animated-icon";

const SPRING: Transition = { type: "spring", stiffness: 100, damping: 12, mass: 0.4 };

// [x1, y1, x2, y2, and the animated end values]; the knobs slide along their rails.
const LINES: [number, number, number, number, Record<string, [number, number]>][] = [
  [21, 4, 14, 4, { x2: [14, 10] }],
  [10, 4, 3, 4, { x1: [10, 5] }],
  [21, 12, 12, 12, { x2: [12, 18] }],
  [8, 12, 3, 12, { x1: [8, 13] }],
  [3, 20, 12, 20, { x2: [12, 4] }],
  [16, 20, 21, 20, { x1: [16, 8] }],
  [14, 2, 14, 6, { x1: [14, 9], x2: [14, 9] }],
  [8, 10, 8, 14, { x1: [8, 14], x2: [8, 14] }],
  [16, 18, 16, 22, { x1: [16, 8], x2: [16, 8] }],
];

function Sliders() {
  const controls = useIconControls();
  return LINES.map(([x1, y1, x2, y2, moves], i) => {
    const pick = (at: 0 | 1) => Object.fromEntries(Object.entries(moves).map(([k, v]) => [k, v[at]]));
    return (
      <m.line key={i} x1={x1} y1={y1} x2={x2} y2={y2} animate={controls} transition={SPRING}
        variants={{ normal: pick(0), animate: pick(1) }} />
    );
  });
}

/** Lucide's sliders-horizontal. */
export function FilterIcon(props: IconProps) {
  return (
    <AnimatedIcon {...props}>
      <Sliders />
    </AnimatedIcon>
  );
}
