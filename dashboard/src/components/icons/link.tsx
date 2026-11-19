import { m, type Variants } from "motion/react";
import { AnimatedIcon, useIconControls, type IconProps } from "./animated-icon";

const LINK: Variants = {
  normal: { pathLength: 1, pathOffset: 0, rotate: 0 },
  animate: {
    pathLength: [1, 0.97, 1, 0.97, 1],
    pathOffset: [0, 0.05, 0, 0.05, 0],
    rotate: [0, -5, 0],
    transition: { rotate: { duration: 0.5 }, duration: 1, times: [0, 0.2, 0.4, 0.6, 1], ease: "easeInOut" },
  },
};

function Links() {
  const controls = useIconControls();
  return (
    <>
      <m.path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" animate={controls} variants={LINK} />
      <m.path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" animate={controls} variants={LINK} />
    </>
  );
}

export function LinkIcon(props: IconProps) {
  return (
    <AnimatedIcon {...props}>
      <Links />
    </AnimatedIcon>
  );
}
