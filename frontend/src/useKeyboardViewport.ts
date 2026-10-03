import { useEffect, useState, type CSSProperties } from "react";

type ViewportState = { keyboardOpen: boolean; style: CSSProperties };

/** Safari's keyboard can resize and pan the visual viewport without resizing the page. */
export function useKeyboardViewport(): ViewportState {
  const [state, setState] = useState<ViewportState>({ keyboardOpen: false, style: {} });
  useEffect(() => {
    document.body.classList.add("app-active");
    const viewport = window.visualViewport;
    let baseline = window.innerHeight;
    let layoutWidth = window.innerWidth;
    const update = () => {
      if (!viewport || viewport.scale !== 1) return;
      const focused = document.activeElement;
      const editing = focused instanceof HTMLTextAreaElement || focused instanceof HTMLInputElement;
      // Rotation changes the layout width; discard the old portrait baseline.
      if (window.innerWidth !== layoutWidth) {
        layoutWidth = window.innerWidth;
        baseline = Math.max(window.innerHeight, viewport.height);
      } else {
        baseline = Math.max(baseline, window.innerHeight, viewport.height);
      }
      const keyboardOpen = viewport.width < 800 && editing && baseline - viewport.height > 120;
      setState({
        keyboardOpen,
        style: {
          "--viewport-height": `${viewport.height}px`,
          "--viewport-top": `${viewport.offsetTop}px`,
        } as CSSProperties,
      });
    };
    update();
    viewport?.addEventListener("resize", update);
    viewport?.addEventListener("scroll", update);
    window.addEventListener("resize", update);
    document.addEventListener("focusin", update);
    document.addEventListener("focusout", update);
    return () => {
      document.body.classList.remove("app-active");
      viewport?.removeEventListener("resize", update);
      viewport?.removeEventListener("scroll", update);
      window.removeEventListener("resize", update);
      document.removeEventListener("focusin", update);
      document.removeEventListener("focusout", update);
    };
  }, []);
  return state;
}
