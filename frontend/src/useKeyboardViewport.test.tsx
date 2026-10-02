import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useKeyboardViewport } from "./useKeyboardViewport";
function Harness() {
  const viewport = useKeyboardViewport();
  return <div data-testid="shell" style={viewport.style} data-keyboard={viewport.keyboardOpen}><textarea aria-label="問題" /></div>;
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
it("tracks keyboard height and Safari pan without changing the layout viewport, then restores", () => {
  const viewport = Object.assign(new EventTarget(), { height: 740, width: 390, offsetTop: 0, scale: 1 });
  vi.stubGlobal("visualViewport", viewport);
  render(<Harness />);
  screen.getByRole("textbox").focus();
  act(() => { viewport.height = 360; viewport.offsetTop = 85; viewport.dispatchEvent(new Event("resize")); });
  const shell = screen.getByTestId("shell");
  expect(shell.dataset.keyboard).toBe("true");
  expect(shell.style.getPropertyValue("--viewport-height")).toBe("360px");
  expect(shell.style.getPropertyValue("--viewport-top")).toBe("85px");
  act(() => { viewport.height = 740; viewport.offsetTop = 0; viewport.dispatchEvent(new Event("resize")); });
  fireEvent.blur(screen.getByRole("textbox"));
  expect(shell.dataset.keyboard).toBe("false");
});
it("preserves the existing viewport geometry during pinch zoom and cleans up scroll locking", () => {
  const viewport = Object.assign(new EventTarget(), { height: 740, width: 390, offsetTop: 0, scale: 1 });
  vi.stubGlobal("visualViewport", viewport);
  const { unmount } = render(<Harness />);
  act(() => { viewport.scale = 2; viewport.height = 200; viewport.dispatchEvent(new Event("resize")); });
  expect(screen.getByTestId("shell").style.getPropertyValue("--viewport-height")).toBe("740px");
  unmount();
  expect(document.body.classList.contains("app-active")).toBe(false);
});

it("does not mistake a landscape rotation for an open keyboard", () => {
  vi.stubGlobal("innerWidth", 390);
  vi.stubGlobal("innerHeight", 740);
  const viewport = Object.assign(new EventTarget(), { height: 740, width: 390, offsetTop: 0, scale: 1 });
  vi.stubGlobal("visualViewport", viewport);
  render(<Harness />);
  screen.getByRole("textbox").focus();
  act(() => {
    vi.stubGlobal("innerWidth", 740);
    vi.stubGlobal("innerHeight", 390);
    viewport.width = 740;
    viewport.height = 390;
    window.dispatchEvent(new Event("resize"));
  });
  expect(screen.getByTestId("shell").dataset.keyboard).toBe("false");
  expect(screen.getByTestId("shell").style.getPropertyValue("--viewport-height")).toBe("390px");
});
