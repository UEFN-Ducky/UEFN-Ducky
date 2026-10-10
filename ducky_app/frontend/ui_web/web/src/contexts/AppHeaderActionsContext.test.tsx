// @vitest-environment jsdom
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import {
  AppHeaderActionsProvider,
  useAppHeaderActions,
  useRegisterAppHeaderActions,
} from "./AppHeaderActionsContext";

afterEach(cleanup);

function setup() {
  const seen: unknown[] = [];
  let register!: ReturnType<typeof useRegisterAppHeaderActions>;
  function Header() {
    seen.push(useAppHeaderActions());
    return null;
  }
  function Bridge() {
    register = useRegisterAppHeaderActions();
    return null;
  }
  render(
    <AppHeaderActionsProvider>
      <Header />
      <Bridge />
    </AppHeaderActionsProvider>,
  );
  return { seen, set: (patch: Parameters<typeof register.setHeaderActions>[0]) => act(() => register.setHeaderActions(patch)) };
}

it("does not re-render the header when a bridge re-sends the same slot", () => {
  const { seen, set } = setup();
  const rendersAtStart = seen.length;

  for (let i = 0; i < 10; i++) set({ terminal: null, verseWorkflow: null });
  expect(seen.length).toBe(rendersAtStart);

  const save = { dirty: false, saving: false, onSave: () => {} };
  set({ save });
  expect(seen.length).toBe(rendersAtStart + 1);
  set({ save });
  expect(seen.length).toBe(rendersAtStart + 1);
});

it("still re-renders the header when a slot changes", () => {
  const { seen, set } = setup();
  set({ save: { dirty: false, saving: false, onSave: () => {} } });
  const before = seen.length;
  set({ save: { dirty: true, saving: false, onSave: () => {} } });
  expect(seen.length).toBe(before + 1);
  expect((seen[seen.length - 1] as { save: { dirty: boolean } }).save.dirty).toBe(true);
});
