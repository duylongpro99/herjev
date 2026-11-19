import { m, type Variants } from "motion/react";
import { AnimatedIcon, useIconControls, type IconProps } from "./animated-icon";

const STROKE: Variants = {
  normal: { opacity: 1, pathLength: 1 },
  animate: { opacity: [0, 1], pathLength: [0, 1] },
};

function Strokes() {
  const controls = useIconControls();
  return (
    <>
      <m.path d="M18 6 6 18" animate={controls} variants={STROKE} />
      <m.path d="m6 6 12 12" animate={controls} variants={STROKE} transition={{ delay: 0.2 }} />
    </>
  );
}

export function CloseIcon(props: IconProps) {
  return (
    <AnimatedIcon {...props}>
      <Strokes />
    </AnimatedIcon>
  );
}
