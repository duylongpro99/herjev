import { AnimatedIcon, type IconProps } from "./animated-icon";
import { Corners } from "./corners";

export function CollapseIcon(props: IconProps) {
  return (
    <AnimatedIcon {...props}>
      <Corners
        parts={[
          ["M9 4.2V9m0 0H4.2M9 9 3 3", 1, 1],
          ["M15 4.2V9m0 0h4.8M15 9l6-6", -1, 1],
          ["M9 19.8V15m0 0H4.2M9 15l-6 6", 1, -1],
          ["m15 15 6 6m-6-6v4.8m0-4.8h4.8", -1, -1],
        ]}
      />
    </AnimatedIcon>
  );
}
