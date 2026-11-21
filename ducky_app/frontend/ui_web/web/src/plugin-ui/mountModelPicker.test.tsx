// @vitest-environment jsdom
import { act } from "react";
import { expect, it, vi } from "vitest";

const seen: Array<Record<string, unknown>> = [];
vi.mock("../components/ducky/DuckyModelPicker", () => ({
  DuckyModelPicker: (props: Record<string, unknown>) => {
    seen.push(props);
    return <span>{String(props.model || props.placeholder)}</span>;
  },
}));

import { mountModelPicker } from "./mountModelPicker";

it("mounts the chat's picker with logins and the slider, and follows setModel", async () => {
  const el = document.createElement("div");
  const onChange = vi.fn();
  const onEffortChange = vi.fn();
  let mounted!: ReturnType<typeof mountModelPicker>;
  await act(async () => {
    mounted = mountModelPicker(el, { model: "", placeholder: "Auto", logins: true, onChange, onEffortChange });
  });
  const props = seen.at(-1)!;
  expect(props.apiOnly).toBeUndefined();
  expect(props.thinkingEffort).toBe("off");
  expect(el.textContent).toBe("Auto");
  await act(async () => (props.onChange as (m: string) => void)("claude_code:claude-opus-5-5"));
  expect(onChange).toHaveBeenCalledWith("claude_code:claude-opus-5-5");
  await act(async () => (seen.at(-1)!.onEffortChange as (e: string) => void)("high"));
  expect(onEffortChange).toHaveBeenCalledWith("high");
  await act(async () => mounted.setModel("openai:gpt-6"));
  expect(el.textContent).toBe("openai:gpt-6");
  await act(async () => mounted.unmount());
});

it("without logins it is API models only", async () => {
  const el = document.createElement("div");
  await act(async () => {
    mountModelPicker(el, { onChange: vi.fn() });
  });
  expect(seen.at(-1)!.apiOnly).toBe(true);
});
