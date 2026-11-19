import { m, type Transition } from "motion/react";
import { useIconControls } from "./animated-icon";

const SPRING: Transition = { type: "spring", stiffness: 250, damping: 25 };

/** Path pieces that each slide by their own offset when the icon animates. */
export function Corners({ parts }: { parts: [d: string, x: number, y: number][] }) {
  const controls = useIconControls();
  return parts.map(([d, x, y]) => (
    <m.path key={d} d={d} animate={controls} transition={SPRING} variants={{ normal: { x: 0, y: 0 }, animate: { x, y } }} />
  ));
}
