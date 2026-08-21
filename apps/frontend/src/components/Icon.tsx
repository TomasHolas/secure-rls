// Material Symbols (Outlined) — the only icon source (no other libs, no hand-rolled SVGs); self-hosted subset in public/fonts. Map a `name` below to add an icon; see components/README.md to regenerate the subset.

import type { CSSProperties } from "react";

export const MATERIAL_SYMBOLS: Record<string, string> = {
  search: "search",
  plus: "add",
  sparkles: "auto_awesome",
  "file-text": "description",
  "git-branch": "account_tree",
  "message-circle": "chat_bubble",
  users: "group",
  play: "play_arrow",
  video: "videocam",
  lightbulb: "lightbulb",
  package: "inventory_2",
  folder: "folder",
  link: "link",
  "external-link": "open_in_new",
  "arrow-up-right": "arrow_outward",
  edit: "edit",
  clock: "schedule",
  x: "close",
  home: "home",
  compass: "explore",
  "chevron-right": "chevron_right",
  "chevron-down": "expand_more",
  send: "send",
  loader: "progress_activity",
  check: "check",
  "arrow-left": "arrow_back",
  "arrow-right": "arrow_forward",
  hash: "tag",
  "corner-down-left": "keyboard_return",
  command: "keyboard_command_key",
  calendar: "calendar_today",
  user: "person",
  filter: "filter_alt",
  trash: "delete",
  share: "share",
  inbox: "inbox",
  "square-terminal": "terminal",
  bot: "smart_toy",
  workflow: "schema",
  "plug-zap": "bolt",
  cpu: "memory",
  database: "database",
  wrench: "build",
  "book-open": "menu_book",
  bookmark: "bookmark",
  download: "download",
  activity: "monitoring",
  "bar-chart": "bar_chart",
  coins: "payments",
  timer: "timer",
  settings: "settings",
  "refresh-cw": "refresh",
  layers: "layers",
};

export interface IconProps {
  name: string;
  size?: number;
  style?: CSSProperties;
  className?: string;
}

export function Icon({ name, size = 18, style, className }: IconProps) {
  const glyph = MATERIAL_SYMBOLS[name] ?? "";
  return (
    <span
      className={`material-symbols-outlined${className ? ` ${className}` : ""}`}
      style={{ fontSize: size, ...style }}
      aria-hidden="true"
    >
      {glyph}
    </span>
  );
}
