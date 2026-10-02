import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ErrorBoundary } from "./ui";
afterEach(cleanup);
it("contains render failures without exposing the error and offers recovery", () => {
  const spy = vi.spyOn(console, "error").mockImplementation(() => undefined);
  function Broken(): never { throw new Error("private component details"); }
  render(<ErrorBoundary><Broken /></ErrorBoundary>);
  expect(screen.getByRole("alert")).toHaveTextContent("畫面暫時無法顯示");
  expect(screen.getByRole("button", { name: "重新載入" })).toBeInTheDocument();
  expect(screen.queryByText("private component details")).toBeNull();
  spy.mockRestore();
});
