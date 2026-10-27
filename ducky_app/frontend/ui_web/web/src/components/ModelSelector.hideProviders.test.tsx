// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { CatalogModelRow } from "../hooks/modelsCatalogCache";
import { ModelSelector } from "./ModelSelector";

afterEach(cleanup);

const row = (providerKey: string, id: string, name: string): CatalogModelRow => ({
  provider: providerKey,
  providerKey,
  id,
  name,
  supportsVision: true,
  supportsTools: true,
  supportsWebSearch: false,
  contextLimit: 0,
  priceIn: null,
  priceOut: null,
  isLocal: false,
});

/** A plugin's picker can leave a gateway's own models out (Play Tester hides Ducky AI's chat model). */
describe("ModelSelector hideProviders", () => {
  it("leaves the hidden gateway's models out and keeps the rest", () => {
    render(
      <ModelSelector
        selectedModel=""
        setSelectedModel={() => undefined}
        catalogRows={[row("uefn_ducky", "ducky-ai", "Ducky AI chat"), row("openai", "gpt-6-luna", "GPT-6 Luna")]}
        hideProviders={["uefn-ducky"]}
      />,
    );
    fireEvent.click(screen.getByRole("button"));
    expect(screen.queryByText("Ducky AI chat")).toBeNull();
    expect(screen.getByText("GPT-6 Luna")).toBeTruthy();
  });
});
