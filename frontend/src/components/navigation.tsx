"use client";

import { usePathname } from "next/navigation";
import React from "react";

const items = [
  ["决策总览", "/"],
  ["用户洞察", "/insights"],
  ["决策实验室", "/decisions"],
  ["产业风险雷达", "/risks"],
] as const;

export function Navigation() {
  const pathname = usePathname();

  return (
    <aside className="navigation">
      <div>
        <div className="brand"><b>S</b>SignalForge</div>
        <nav aria-label="决策中枢主导航">
          {items.map(([item, href]) => {
            const isActive = pathname === href;
            return <a aria-current={isActive ? "page" : undefined} className={`nav-item${isActive ? " active" : ""}`} href={href} key={item}><i aria-hidden="true" />{item}</a>;
          })}
          <a aria-current={pathname === "/embodied" ? "page" : undefined} className={`nav-item${pathname === "/embodied" ? " active" : ""}`} href="/embodied"><i aria-hidden="true" />EmbodiedOps</a>
        </nav>
      </div>
      <div className="dataset-badge"><small>当前数据快照</small><strong>ASAP Demo · v1</strong><small>使用版本化证据数据</small></div>
    </aside>
  );
}
