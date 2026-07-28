import { useEffect, useMemo, useState } from "react";

import {
  allDiscordCommands,
  filterDiscordCommands,
  groupDiscordCommands,
} from "../../components/groupchat/discordCommands";
import { Icons } from "../../icons/Icons";
import { getApi } from "../../hooks/usePanelApi";
import { onApiReady } from "../../hooks/onApiReady";

/** Settings → Discord → Commands: searchable chat + agent-tool reference. */
export function DiscordCommandsCatalog() {
  const [query, setQuery] = useState("");
  const [prefix, setPrefix] = useState("!ducky");

  useEffect(
    () =>
      onApiReady(() => {
        const api = getApi();
        if (!api?.discord_list_bots) return;
        void api.discord_list_bots().then((res) => {
          const first = (res.bots || []).find((b) => b.enabled) || (res.bots || [])[0];
          if (first?.prefix?.trim()) setPrefix(first.prefix.trim());
        });
      }),
    [],
  );

  const groups = useMemo(() => {
    const filtered = filterDiscordCommands(allDiscordCommands(prefix), query);
    return groupDiscordCommands(filtered);
  }, [prefix, query]);

  const total = groups.reduce((n, g) => n + g.items.length, 0);

  return (
    <div className="discord-commands-catalog">
      <div className="catalog-slide-header">
        <div className="catalog-slide-header-titles">
          <h2 className="catalog-slide-title">Commands</h2>
          <p className="general-tab-section-desc">
            Chat commands use each bot’s prefix (example below: <code>{prefix}</code>). Agent tools
            are available to duckies when Discord tools are enabled.
          </p>
        </div>
      </div>

      <label className="catalog-slide-search">
        <span className="catalog-slide-search-icon" aria-hidden>
          <Icons.Search />
        </span>
        <input
          className="catalog-slide-search-input"
          type="search"
          value={query}
          placeholder="Search commands…"
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search Discord commands"
        />
      </label>

      {total === 0 ? (
        <p className="catalog-slide-empty">No commands match “{query.trim()}”.</p>
      ) : (
        <div className="discord-commands-groups">
          {groups.map((g) => (
            <section key={g.category} className="discord-commands-group">
              <h3 className="discord-commands-group-title">
                {g.category}
                <span className="discord-commands-group-count">{g.items.length}</span>
              </h3>
              <ul className="discord-commands-list">
                {g.items.map((cmd) => (
                  <li key={cmd.id} className="discord-commands-row">
                    <div className="discord-commands-row-main">
                      <code className="discord-commands-name">{cmd.name}</code>
                      <span
                        className={`discord-commands-kind discord-commands-kind--${cmd.kind}`}
                      >
                        {cmd.kind === "chat" ? "Chat" : "Agent"}
                      </span>
                    </div>
                    <p className="discord-commands-desc">{cmd.description}</p>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
