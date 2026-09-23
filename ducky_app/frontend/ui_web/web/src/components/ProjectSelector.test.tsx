// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ProjectSelector } from "./ProjectSelector";

const mocks = vi.hoisted(() => ({
  flush: vi.fn(),
  api: { list_recent_projects: vi.fn(), set_project_root: vi.fn() },
}));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => mocks.api, isRemote: () => true }));
vi.mock("../contexts/EditorWorkspaceBridge", () => ({ useOptionalEditorWorkspaceFlush: () => mocks.flush }));
vi.mock("../contexts/ConfirmModalContext", () => ({ useConfirmModal: () => ({ confirm: vi.fn() }) }));
vi.mock("./TruncatedText", () => ({ TruncatedText: ({ children }: { children: React.ReactNode }) => <span>{children}</span> }));
vi.mock("./DropdownPanel", () => ({ DropdownPanel: ({ open, children }: { open: boolean; children: React.ReactNode }) => open ? <div>{children}</div> : null }));

beforeEach(() => {
  mocks.flush.mockReset().mockResolvedValue(undefined);
  mocks.api.list_recent_projects.mockReset().mockResolvedValue(["B", "C", "D"].map((name) => ({ name, path: `C:/${name}`, slug: name, active: false })));
  mocks.api.set_project_root.mockReset().mockImplementation(async (path: string) => ({ path, name: path.slice(-1), slug: path.slice(-1) }));
});
afterEach(cleanup);

it("closes the menu before waiting for the saved workspace", async () => {
  let finish!: () => void;
  mocks.flush.mockReturnValue(new Promise<void>((resolve) => { finish = resolve; }));
  render(<ProjectSelector project={{ path: "C:/A", name: "A", slug: "A" }} />);
  fireEvent.pointerDown(screen.getByRole("button", { name: "A" }));
  await act(async () => {});
  fireEvent.click(screen.getByRole("button", { name: "B" }));
  expect(screen.queryByRole("button", { name: "B" })).toBeNull();
  expect(mocks.api.set_project_root).not.toHaveBeenCalled();
  await act(async () => { finish(); });
  expect(mocks.api.set_project_root).toHaveBeenCalledWith("C:/B");
});

it("serializes rapid selections and skips superseded queued projects", async () => {
  let finish!: (info: unknown) => void;
  mocks.api.set_project_root.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  const changed = vi.fn();
  render(<ProjectSelector embedded project={{ path: "C:/A", name: "A", slug: "A" }} onProjectChanged={changed} />);
  await act(async () => {});
  fireEvent.click(screen.getByRole("button", { name: "B" }));
  await act(async () => {});
  fireEvent.click(screen.getByRole("button", { name: "C" }));
  fireEvent.click(screen.getByRole("button", { name: "D" }));
  expect(mocks.api.set_project_root).toHaveBeenCalledTimes(1);
  await act(async () => { finish({ path: "C:/B", name: "B", slug: "B" }); });
  expect(mocks.api.set_project_root.mock.calls).toEqual([["C:/B"], ["C:/D"]]);
  expect(changed).toHaveBeenCalledOnce();
});
