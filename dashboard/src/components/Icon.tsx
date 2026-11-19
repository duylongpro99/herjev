// Animated stroke icons; each one plays when the control it sits in is hovered or focused.
import type { ComponentType } from "react";
import { AlertIcon } from "./icons/alert";
import type { IconProps } from "./icons/animated-icon";
import { ArrowIcon } from "./icons/arrow";
import { BoardIcon } from "./icons/board";
import { ChevronDownIcon } from "./icons/chevron-down";
import { CloseIcon } from "./icons/close";
import { CollapseIcon } from "./icons/collapse";
import { ExpandIcon } from "./icons/expand";
import { FilterIcon } from "./icons/filter";
import { LinkIcon } from "./icons/link";
import { LockIcon } from "./icons/lock";
import { RefreshIcon } from "./icons/refresh";
import { SearchIcon } from "./icons/search";

const ICONS = {
  alert: AlertIcon,
  arrow: ArrowIcon,
  board: BoardIcon,
  "chevron-down": ChevronDownIcon,
  close: CloseIcon,
  collapse: CollapseIcon,
  expand: ExpandIcon,
  filter: FilterIcon,
  link: LinkIcon,
  lock: LockIcon,
  refresh: RefreshIcon,
  search: SearchIcon,
} satisfies Record<string, ComponentType<IconProps>>;

export type IconName = keyof typeof ICONS;

export function Icon({ name, ...props }: IconProps & { name: IconName }) {
  const Glyph = ICONS[name];
  return <Glyph {...props} />;
}
