/** Discord chat + agent-tool command reference (Settings → Commands, hub cheatsheet). */

export type DiscordCommandKind = "chat" | "agent";

export type DiscordCommandCategory =
  | "Chat"
  | "Bots"
  | "Channels"
  | "Roles"
  | "Members"
  | "Messages";

export type DiscordCommandEntry = {
  id: string;
  /** What to type / call — e.g. `!ducky list` or `discord_list_channels`. */
  name: string;
  description: string;
  category: DiscordCommandCategory;
  kind: DiscordCommandKind;
};

/** Prefix chat commands users type in Discord (each bot has its own prefix). */
export function chatCommands(prefix: string): DiscordCommandEntry[] {
  const p = (prefix || "!ducky").trim() || "!ducky";
  return [
    {
      id: "chat-roster",
      name: p,
      description: "List your duckies (agent profiles) and what each is for.",
      category: "Chat",
      kind: "chat",
    },
    {
      id: "chat-list",
      name: `${p} list`,
      description: "Same as bare prefix — roster with when-to-use hints.",
      category: "Chat",
      kind: "chat",
    },
    {
      id: "chat-help",
      name: `${p} help`,
      description: "Alias for list / roster.",
      category: "Chat",
      kind: "chat",
    },
    {
      id: "chat-duckies",
      name: `${p} duckies`,
      description: "Alias for list / roster.",
      category: "Chat",
      kind: "chat",
    },
    {
      id: "chat-whoami",
      name: `${p} whoami`,
      description: "Reply with your Discord user ID (for Command access allow-list).",
      category: "Chat",
      kind: "chat",
    },
    {
      id: "chat-run",
      name: `${p} <name> <message>`,
      description: "Run that ducky; its reply posts back in the channel. One job at a time per ducky.",
      category: "Chat",
      kind: "chat",
    },
  ];
}

/** Compact rows for hub / dock cheatsheet (core chat cmds only). */
export function commandHelp(prefix: string): { cmd: string; desc: string }[] {
  const keep = new Set(["chat-roster", "chat-list", "chat-whoami", "chat-run"]);
  return chatCommands(prefix)
    .filter((c) => keep.has(c.id))
    .map((c) => ({ cmd: c.name, desc: c.description }));
}

/** Agent / MCP tools registered by the Discord plugin. */
export const AGENT_COMMANDS: DiscordCommandEntry[] = [
  {
    id: "agent-list-bots",
    name: "discord_list_bots",
    description: "List configured bots (id, label, username, prefix, guild). Optional query filter.",
    category: "Bots",
    kind: "agent",
  },
  {
    id: "agent-list-channels",
    name: "discord_list_channels",
    description: "List channels and categories of the configured server.",
    category: "Channels",
    kind: "agent",
  },
  {
    id: "agent-read-channel",
    name: "discord_read_channel",
    description: "Read recent messages from a channel (oldest-first).",
    category: "Channels",
    kind: "agent",
  },
  {
    id: "agent-send-message",
    name: "discord_send_message",
    description: "Post a message as the bot (only when the user asked).",
    category: "Messages",
    kind: "agent",
  },
  {
    id: "agent-create-channel",
    name: "discord_create_channel",
    description: "Create a text/voice/announcement channel or category.",
    category: "Channels",
    kind: "agent",
  },
  {
    id: "agent-edit-channel",
    name: "discord_edit_channel",
    description: "Rename, retopic, move, or reorder a channel/category.",
    category: "Channels",
    kind: "agent",
  },
  {
    id: "agent-delete-channel",
    name: "discord_delete_channel",
    description: "Delete a channel or category by id. Irreversible.",
    category: "Channels",
    kind: "agent",
  },
  {
    id: "agent-set-channel-permissions",
    name: "discord_set_channel_permissions",
    description: "Set a channel permission overwrite (role or member).",
    category: "Channels",
    kind: "agent",
  },
  {
    id: "agent-list-roles",
    name: "discord_list_roles",
    description: "List roles of the configured server.",
    category: "Roles",
    kind: "agent",
  },
  {
    id: "agent-create-role",
    name: "discord_create_role",
    description: "Create a role (optional permissions / color / hoist / mentionable).",
    category: "Roles",
    kind: "agent",
  },
  {
    id: "agent-edit-role",
    name: "discord_edit_role",
    description: "Edit a role — only pass fields you want to change.",
    category: "Roles",
    kind: "agent",
  },
  {
    id: "agent-delete-role",
    name: "discord_delete_role",
    description: "Delete a role by id.",
    category: "Roles",
    kind: "agent",
  },
  {
    id: "agent-list-members",
    name: "discord_list_members",
    description: "List guild members. Requires Server Members Intent.",
    category: "Members",
    kind: "agent",
  },
  {
    id: "agent-set-member-roles",
    name: "discord_set_member_roles",
    description: "Add or remove a role on a member.",
    category: "Members",
    kind: "agent",
  },
  {
    id: "agent-set-nickname",
    name: "discord_set_nickname",
    description: "Set a member's nickname (empty clears it).",
    category: "Members",
    kind: "agent",
  },
  {
    id: "agent-timeout-member",
    name: "discord_timeout_member",
    description: "Timeout a member for N minutes (0 clears). Requires Moderate Members.",
    category: "Members",
    kind: "agent",
  },
  {
    id: "agent-kick-member",
    name: "discord_kick_member",
    description: "Kick a member. Requires Kick Members.",
    category: "Members",
    kind: "agent",
  },
  {
    id: "agent-ban-member",
    name: "discord_ban_member",
    description: "Ban a member; optional recent-message purge. Requires Ban Members.",
    category: "Members",
    kind: "agent",
  },
  {
    id: "agent-edit-message",
    name: "discord_edit_message",
    description: "Edit a message (usually one the bot sent).",
    category: "Messages",
    kind: "agent",
  },
  {
    id: "agent-delete-message",
    name: "discord_delete_message",
    description: "Delete a message. Manage Messages needed for others' messages.",
    category: "Messages",
    kind: "agent",
  },
  {
    id: "agent-create-invite",
    name: "discord_create_invite",
    description: "Create an invite link for a channel.",
    category: "Messages",
    kind: "agent",
  },
];

const CATEGORY_ORDER: DiscordCommandCategory[] = [
  "Chat",
  "Bots",
  "Channels",
  "Roles",
  "Members",
  "Messages",
];

export function allDiscordCommands(prefix: string): DiscordCommandEntry[] {
  return [...chatCommands(prefix), ...AGENT_COMMANDS];
}

export function filterDiscordCommands(
  entries: DiscordCommandEntry[],
  query: string,
): DiscordCommandEntry[] {
  const q = query.trim().toLowerCase();
  if (!q) return entries;
  const tokens = q.split(/\s+/).filter(Boolean);
  return entries.filter((e) => {
    const hay = `${e.name} ${e.description} ${e.category} ${e.kind}`.toLowerCase();
    return tokens.every((t) => hay.includes(t));
  });
}

export function groupDiscordCommands(
  entries: DiscordCommandEntry[],
): { category: DiscordCommandCategory; items: DiscordCommandEntry[] }[] {
  const byCat = new Map<DiscordCommandCategory, DiscordCommandEntry[]>();
  for (const e of entries) {
    const list = byCat.get(e.category) || [];
    list.push(e);
    byCat.set(e.category, list);
  }
  return CATEGORY_ORDER.filter((c) => byCat.has(c)).map((category) => ({
    category,
    items: byCat.get(category) || [],
  }));
}
