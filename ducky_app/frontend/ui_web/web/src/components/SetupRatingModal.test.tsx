// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SetupRatingModal } from "./SetupRatingModal";

afterEach(cleanup);

const submit = () => screen.getByRole("button", { name: "Submit" }) as HTMLButtonElement;
const star = (n: number) => screen.getByRole("radio", { name: `${n} star${n === 1 ? "" : "s"}` });

describe("SetupRatingModal", () => {
  it("needs 1 to 5 stars: Submit is off at 0 and a second click keeps the star", () => {
    const onSubmit = vi.fn();
    render(<SetupRatingModal onSubmit={onSubmit} />);
    expect(submit().disabled).toBe(true);
    expect(screen.queryByLabelText(/Any suggestions/)).toBeNull();
    fireEvent.click(submit());
    expect(onSubmit).not.toHaveBeenCalled();

    fireEvent.click(star(1));
    fireEvent.click(star(1));
    expect(submit().disabled).toBe(false);
    fireEvent.click(submit());
    expect(onSubmit).toHaveBeenCalledWith(1, "");
  });

  it("opens the optional suggestions box for every star and sends what was typed", () => {
    const onSubmit = vi.fn();
    render(<SetupRatingModal onSubmit={onSubmit} />);
    for (const n of [1, 2, 3, 4, 5]) {
      fireEvent.click(star(n));
      expect(screen.getByLabelText(/Any suggestions/)).toBeTruthy();
    }
    fireEvent.change(screen.getByLabelText(/Any suggestions/), { target: { value: "  More video tips  " } });
    fireEvent.click(submit());
    expect(onSubmit).toHaveBeenCalledWith(5, "More video tips");
  });

  it("arrow keys never go below 1 star", () => {
    const onSubmit = vi.fn();
    render(<SetupRatingModal onSubmit={onSubmit} />);
    fireEvent.keyDown(screen.getByRole("radiogroup"), { key: "ArrowLeft" });
    fireEvent.click(submit());
    expect(onSubmit).toHaveBeenCalledWith(1, "");
  });
});
