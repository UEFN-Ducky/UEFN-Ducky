/**
 * Mount the chat's own model picker (DuckyModelPicker) into a DOM node for shell.boot plugins,
 * e.g. a gateway drawing its page in the LLMs `ducky:llm-slot` mount.
 */
import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { DuckyModelPicker } from "../components/ducky/DuckyModelPicker";

export type MountModelPickerOpts = {
  /** Qualified "gateway:model" (or "agent:model" for a login), "" = the plugin's own default. */
  model?: string;
  onChange: (model: string) => void;
  placeholder?: string;
  /** Offer logins (Claude Code, Codex…) and the Faster / Smarter slider; else API models only. */
  logins?: boolean;
  effort?: string;
  onEffortChange?: (effort: string) => void;
  hideProviders?: string[];
};

export type MountedModelPicker = {
  setModel: (model: string) => void;
  setEffort: (effort: string) => void;
  unmount: () => void;
};

type Setters = { model?: (v: string) => void; effort?: (v: string) => void };

function PickerMount({ opts, setters }: { opts: MountModelPickerOpts; setters: Setters }) {
  const [model, setModel] = useState(opts.model || "");
  const [effort, setEffort] = useState(opts.effort || "off");
  useEffect(() => {
    setters.model = setModel;
    setters.effort = setEffort;
    return () => {
      delete setters.model;
      delete setters.effort;
    };
  }, [setters]);
  const loginProps = opts.logins
    ? {
        thinkingEffort: effort,
        onEffortChange: (next: string) => {
          setEffort(next);
          opts.onEffortChange?.(next);
        },
      }
    : { apiOnly: true };
  return (
    <DuckyModelPicker
      model={model}
      onChange={(next) => {
        setModel(next);
        opts.onChange(next);
      }}
      label=""
      hint=""
      placeholder={opts.placeholder || "Default model"}
      menuPlacement="bottom"
      {...loginProps}
      hideProviders={opts.hideProviders}
      labeled
    />
  );
}

/** Imperative mount for shell.boot — returns setters + unmount. */
export function mountModelPicker(container: HTMLElement, opts: MountModelPickerOpts): MountedModelPicker {
  const root = createRoot(container);
  const setters: Setters = {};
  root.render(<PickerMount opts={opts} setters={setters} />);
  return {
    setModel: (model) => setters.model?.(model),
    setEffort: (effort) => setters.effort?.(effort),
    unmount: () => root.unmount(),
  };
}
