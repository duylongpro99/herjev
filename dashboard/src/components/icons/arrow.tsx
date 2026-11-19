import { m } from "motion/react";
import { AnimatedIcon, useIconControls, type IconProps } from "./animated-icon";

function Arrow() {
  const controls = useIconControls();
  return (
    <>
      <m.path d="M5 12h14" animate={controls}
        variants={{ normal: { d: "M5 12h14" }, animate: { d: ["M5 12h14", "M5 12h9", "M5 12h14"], transition: { duration: 0.4 } } }} />
      <m.path d="m12 5 7 7-7 7" animate={controls}
        variants={{ normal: { x: 0 }, animate: { x: [0, -3, 0], transition: { duration: 0.4 } } }} />
    </>
  );
}

/** Lucide's arrow-right. */
export function ArrowIcon(props: IconProps) {
  return (
    <AnimatedIcon {...props}>
      <Arrow />
    </AnimatedIcon>
  );
}
