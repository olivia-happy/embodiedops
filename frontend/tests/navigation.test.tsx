import "@testing-library/jest-dom/vitest";
import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Navigation } from "../src/components/navigation";

vi.mock("next/navigation", () => ({ usePathname: () => "/risks" }));

describe("Navigation", () => {
  it("marks the current route for assistive technology", () => {
    render(<Navigation />);
    expect(screen.getByRole("navigation", { name: "决策中枢主导航" })).toBeVisible();
    expect(screen.getByRole("link", { name: "产业风险雷达" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "决策总览" })).not.toHaveAttribute("aria-current");
  });

  it("exposes each Chinese navigation destination as a link", () => {
    render(<Navigation />);
    expect(screen.getByRole("link", { name: "决策总览" })).toHaveAttribute("href", "/");
    expect(screen.getByRole("link", { name: "用户洞察" })).toHaveAttribute("href", "/insights");
    expect(screen.getByRole("link", { name: "决策实验室" })).toHaveAttribute("href", "/decisions");
    expect(screen.getByRole("link", { name: "产业风险雷达" })).toHaveAttribute("href", "/risks");
  });
});
