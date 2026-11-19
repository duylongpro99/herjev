import { m } from "motion/react";
import { AnimatedIcon, useIconControls, type IconProps } from "./animated-icon";

function Panels() {
  const controls = useIconControls();
  return (
    <>
      <m.rect x="3" y="3" width="18" height="7" rx="1" animate={controls}
        variants={{
          normal: { opacity: 1, y: 0 },
          animate: { opacity: [0, 1], y: [-5, 0], transition: { opacity: { duration: 0.5, times: [0.2, 1] }, duration: 0.5 } },
        }} />
      <m.rect x="3" y="14" width="7" height="7" rx="1" animate={controls}
        variants={{
          normal: { opacity: 1, x: 0 },
          animate: { opacity: [0, 1], x: [-10, 0], transition: { opacity: { duration: 0.7, times: [0.5, 1] }, x: { delay: 0.3 }, duration: 0.5 } },
        }} />
      <m.rect x="14" y="14" width="7" height="7" rx="1" animate={controls}
        variants={{
          normal: { opacity: 1, x: 0 },
          animate: { opacity: [0, 1], x: [10, 0], transition: { opacity: { duration: 0.8, times: [0.5, 1] }, x: { delay: 0.4 }, duration: 0.5 } },
        }} />
    </>
  );
}

/** Lucide's layout-panel-top. */
export function BoardIcon(props: IconProps) {
  return (
    <AnimatedIcon {...props}>
      <Panels />
    </AnimatedIcon>
  );
}
