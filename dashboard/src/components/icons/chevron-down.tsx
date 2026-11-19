import { m } from "motion/react";
import { AnimatedIcon, useIconControls, type IconProps } from "./animated-icon";

function Chevron() {
  const controls = useIconControls();
  return (
    <m.path
      d="m6 9 6 6 6-6"
      animate={controls}
      transition={{ times: [0, 0.4, 1], duration: 0.5 }}
      variants={{ normal: { y: 0 }, animate: { y: [0, 2, 0] } }}
    />
  );
}

export function ChevronDownIcon(props: IconProps) {
  return (
    <AnimatedIcon {...props}>
      <Chevron />
    </AnimatedIcon>
  );
}
