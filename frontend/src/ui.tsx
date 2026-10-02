import { Component, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode } from "react";

export function Button(props: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button type="button" {...props} />;
}
export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} />;
}
export function ErrorMessage({ children, className = "inline-error" }: { children: ReactNode; className?: string }) {
  return <p className={className} role="alert">{children}</p>;
}
export function StatusMessage({ children }: { children: ReactNode }) {
  return <p className="stream-status" role="status" aria-live="polite">{children}</p>;
}
export class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    if (this.state.failed) return (
      <main className="loading-page">
        <section role="alert">
          <h1>畫面暫時無法顯示</h1>
          <p>請重新載入頁面，恢復已完成的對話。</p>
          <Button className="primary-button" onClick={() => window.location.reload()}>重新載入</Button>
        </section>
      </main>
    );
    return this.props.children;
  }
}
