// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { render, screen } from "@testing-library/react";
import { lazy, Suspense } from "react";
import { expect, it, vi } from "vitest";

const loads = vi.hoisted(() => ({ remote: 0 }));
vi.mock("./components/RemoteWindowView", () => {
  loads.remote += 1;
  return {
    RemoteWindowOverlay: ({ hwnd }: { hwnd: string }) => <div>overlay {hwnd}</div>,
    RemoteWindowLaunching: () => null,
    RemoteTypeToggle: () => null,
    RemoteWindowSelect: () => null,
    UefnStickPad: () => null,
    StickBtn: () => null,
  };
});

const source = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf8");

it("the app shell and header import the remote view only lazily", () => {
  for (const file of ["./App.tsx", "./components/Header.tsx"]) {
    const text = source(file);
    expect(text).not.toMatch(/^import [^;]*RemoteWindowView";/m);
    expect(text).toMatch(/import\("\.\/(components\/)?RemoteWindowView"\)/);
  }
  // The trackpad pulls the remote view in through RemoteController, so it is lazy too.
  expect(source("./App.tsx")).not.toMatch(/^import [^;]*RemoteTrackpad";/m);
});

it("the remote view module loads only when the overlay first shows", async () => {
  expect(loads.remote).toBe(0);

  const Overlay = lazy(() => import("./components/RemoteWindowView").then((m) => ({ default: m.RemoteWindowOverlay })));
  render(<Suspense fallback={null}><Overlay hwnd="monitor:1" /></Suspense>);
  expect(await screen.findByText("overlay monitor:1")).toBeTruthy();
  expect(loads.remote).toBe(1);
});
