import React, { useEffect } from "react";
import type { Evidence } from "@/lib/types";

type EvidenceDrawerProps = {
  open: boolean;
  evidence: Evidence[];
  loading?: boolean;
  error?: string | null;
  onClose?: () => void;
  onRetry?: () => void;
};

export function EvidenceDrawer({ open, evidence, loading = false, error = null, onClose, onRetry }: EvidenceDrawerProps) {
  useEffect(() => {
    if (!open || !onClose) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <aside aria-labelledby="evidence-drawer-title" aria-modal="true" className="evidence-drawer" role="dialog">
      <header>
        <div>
          <small>可追溯原文</small>
          <h2 id="evidence-drawer-title">原始证据</h2>
        </div>
        {onClose && <button aria-label="关闭证据抽屉" onClick={onClose} type="button">关闭</button>}
      </header>
      {loading ? (
        <p aria-live="polite" className="drawer-empty">正在加载原始证据…</p>
      ) : error ? (
        <section aria-live="assertive" className="drawer-empty">
          <p>{error}</p>
          {onRetry && <button onClick={onRetry} type="button">重新加载证据</button>}
        </section>
      ) : evidence.length ? (
        evidence.map((item) => (
          <article key={item.id}>
            <div><code>{item.id}</code><span>{item.aspect ?? "未分类"} · {item.sentiment}</span></div>
            <p>{item.redacted ? "该证据已脱敏，默认不展示原文。" : item.content}</p>
          </article>
        ))
      ) : (
        <p className="drawer-empty">没有可展示的原始证据</p>
      )}
    </aside>
  );
}
