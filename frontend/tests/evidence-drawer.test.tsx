import "@testing-library/jest-dom/vitest";
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { EvidenceDrawer } from "../src/components/evidence-drawer";

describe("EvidenceDrawer", () => {
  it("provides an accessible error state that can retry the same request", () => {
    const onClose = vi.fn();
    const onRetry = vi.fn();
    render(<EvidenceDrawer open evidence={[]} loading={false} error="加载失败" onClose={onClose} onRetry={onRetry} />);

    expect(screen.getByRole("dialog", { name: "原始证据" })).toHaveAttribute("aria-modal", "true");
    fireEvent.click(screen.getByRole("button", { name: "重新加载证据" }));
    fireEvent.click(screen.getByRole("button", { name: "关闭证据抽屉" }));

    expect(onRetry).toHaveBeenCalledOnce();
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("separates loading and empty evidence states", () => {
    const { rerender } = render(<EvidenceDrawer open evidence={[]} loading onClose={vi.fn()} />);
    expect(screen.getByText("正在加载原始证据…")).toBeVisible();

    rerender(<EvidenceDrawer open evidence={[]} loading={false} onClose={vi.fn()} />);
    expect(screen.getByText("没有可展示的原始证据")).toBeVisible();
  });
});
