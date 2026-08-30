# SignalForge AI Decision Studio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the local SignalForge frontend as a modern Chinese AI decision studio that shows one actionable conclusion first and exposes its real evidence, scores, risks, and uncertainty on demand.

**Architecture:** Keep the Next.js App Router and existing FastAPI contract. Small components render the decision hero, evidence fold, signal spectrum, and accessible states; pages continue to own asynchronous API calls. Use CSS variables in the existing stylesheet; do not add packages or network resources.

**Tech Stack:** Next.js 15, React 19, TypeScript, CSS, Vitest, Testing Library, Docker Compose.

## Global Constraints

- Use only existing local API functions and returned fields; do not modify backend, scoring, ETL, or imported data.
- Do not use paid fonts, assets, APIs, analytics, deployment services, or dependencies.
- Apply the approved warm-paper, ink, cinnabar, teal, and restrained-gold palette; colour cannot be the only status signal.
- Keep loading, error, empty and retry states; never invent AI claims for missing fields or empty `insights`.
- Support keyboard interaction, visible focus, `aria-expanded`, labelled drawers, reduced motion and a one-column layout at 900px or below.
- Preserve evidence, decision creation, feedback, Trace, insight filtering, and risk-source workflows.

---

## File Structure

- `frontend/src/lib/types.ts`: expand the overview opportunity type with API-returned `aspect`, `review_count`, `evidence_ids`, and `contributions`.
- `frontend/src/components/decision-hero.tsx` (new): top-priority opportunity and controlled evidence/score fold.
- `frontend/src/components/signal-spectrum.tsx` (new): interactive, accessible topic signals.
- `frontend/src/components/navigation.tsx`, `opportunity-card.tsx`, `evidence-drawer.tsx`, `risk-timeline.tsx`: accessible interactions and real metadata.
- `frontend/src/app/page.tsx`, `insights/page.tsx`, `decisions/page.tsx`, `risks/page.tsx`: compose the redesigned views while retaining current data calls.
- `frontend/src/app/globals.css`: visual tokens, layout, focus, motion and responsive rules.
- `frontend/tests/navigation.test.tsx` (new), existing page tests, and `frontend/tests/evidence-drawer.test.tsx` (new): user-visible regression coverage.

### Task 1: Establish visual foundations and responsive navigation

**Files:** Modify `frontend/src/components/navigation.tsx`, `frontend/src/app/globals.css`; create `frontend/tests/navigation.test.tsx`.

**Interfaces:** `Navigation(): JSX.Element` consumes `usePathname(): string` and produces `<nav aria-label="主导航">`; its active route link has `aria-current="page"`.

- [ ] **Step 1: Write the failing test**

```tsx
vi.mock("next/navigation", () => ({ usePathname: () => "/risks" }));
render(<Navigation />);
expect(screen.getByRole("navigation", { name: "主导航" })).toBeVisible();
expect(screen.getByRole("link", { name: "产业风险雷达" })).toHaveAttribute("aria-current", "page");
```

- [ ] **Step 2: Verify the test fails**

Run: `Set-Location frontend; npm test -- navigation.test.tsx`

Expected: FAIL because the active route is hard-coded.

- [ ] **Step 3: Implement the minimal visual base**

Add `"use client"` and `usePathname` to navigation, derive active state per route, keep ordinary anchor navigation, and add `aria-current` only to the active link. In `globals.css`, replace generic blue/grey variables with named paper, ink, cinnabar, teal, gold, line, and muted tokens. Add a 3–4px ink rule to primary regions, visible `:focus-visible`, reduced-motion override, and a 900px single-column layout. Do not import remote CSS, fonts or images.

- [ ] **Step 4: Verify passing test and lint**

Run: `Set-Location frontend; npm test -- navigation.test.tsx; npm run lint`

Expected: PASS and exit code 0.

- [ ] **Step 5: Commit**

Run: `git add frontend/src/components/navigation.tsx frontend/src/app/globals.css frontend/tests/navigation.test.tsx; git commit -m "feat: establish decision studio foundations"`

### Task 2: Build the evidence-backed decision hero

**Files:** Modify `frontend/src/lib/types.ts`, `frontend/src/components/opportunity-card.tsx`, `frontend/src/app/page.tsx`, `frontend/tests/overview.test.tsx`; create `frontend/src/components/decision-hero.tsx`.

**Interfaces:** `DecisionHero({ opportunity, datasetVersionId, onShowEvidence }): JSX.Element` consumes `OverviewResponse["opportunities"][number]` plus `onShowEvidence(ids: string[]): Promise<void> | void`. The opportunity has `id`, `title`, `aspect`, `review_count`, `negative_rate`, `score`, `evidence_count`, `evidence_ids`, and `contributions`.

- [ ] **Step 1: Write the failing interaction test**

```tsx
render(<DecisionHero opportunity={opportunity} datasetVersionId="v1" onShowEvidence={onShowEvidence} />);
await user.click(screen.getByRole("button", { name: "展开依据" }));
expect(screen.getByText("负向反馈 75.0% · 4 条样本 · 4 条证据")).toBeVisible();
expect(screen.getByText("数据版本 v1")).toBeVisible();
await user.click(screen.getByRole("button", { name: "查看原始证据" }));
expect(onShowEvidence).toHaveBeenCalledWith(["review-1", "review-2"]);
```

- [ ] **Step 2: Verify the test fails**

Run: `Set-Location frontend; npm test -- overview.test.tsx`

Expected: FAIL because `DecisionHero` is absent.

- [ ] **Step 3: Implement the real-data fold**

Expose existing API fields in the type, without changing endpoint URLs. The hero’s “展开依据” button controls a same-page fold with `aria-expanded`. Show exact data version, score contributions, and only the API-provided evidence IDs. Use “不足以判断” when a value is null. The homepage uses its first opportunity as the hero, remaining opportunities as the queue, and loads `getEvidence(data.active_dataset.id, ids)` before opening `EvidenceDrawer`. Retain abort, loading, error, retry, and no-opportunity handling. Make queue-card expansion keyboard-operable.

- [ ] **Step 4: Verify tests and lint**

Run: `Set-Location frontend; npm test -- overview.test.tsx; npm run lint`

Expected: PASS; no claim or evidence ID is fabricated.

- [ ] **Step 5: Commit**

Run: `git add frontend/src/lib/types.ts frontend/src/components/decision-hero.tsx frontend/src/components/opportunity-card.tsx frontend/src/app/page.tsx frontend/tests/overview.test.tsx; git commit -m "feat: add evidence-backed decision hero"`

### Task 3: Add an honest, interactive insight signal spectrum

**Files:** Create `frontend/src/components/signal-spectrum.tsx`, `frontend/tests/evidence-drawer.test.tsx`; modify `frontend/src/app/insights/page.tsx`, `frontend/src/components/evidence-drawer.tsx`, `frontend/tests/insights.test.tsx`.

**Interfaces:** `SignalSpectrum({ metrics, selectedAspect, onSelectAspect }): JSX.Element` consumes `InsightsResponse["metrics"]` and calls `onSelectAspect(aspect: string)`. `EvidenceDrawer({ open, evidence, loading, error, onClose }): JSX.Element` consumes `Evidence[]`.

- [ ] **Step 1: Write failing signal and empty-state tests**

```tsx
render(<SignalSpectrum metrics={[metric]} selectedAspect="" onSelectAspect={onSelect} />);
await user.click(screen.getByRole("button", { name: /服务.*75.0% 负向/ }));
expect(onSelect).toHaveBeenCalledWith("service");
render(<InsightsBody metrics={[metric]} insights={[]} />);
expect(screen.getByText("尚未形成可验证洞察")).toBeVisible();
expect(screen.getByText(/先审阅主题信号与原始证据/)).toBeVisible();
```

- [ ] **Step 2: Verify tests fail**

Run: `Set-Location frontend; npm test -- insights.test.tsx evidence-drawer.test.tsx`

Expected: FAIL because no spectrum or explicit no-insight state exists.

- [ ] **Step 3: Implement signal and evidence states**

Replace `TopicChart` usage with `SignalSpectrum`. Each signal button visibly and accessibly names aspect, sample count, negative rate, severity and scoreability; it uses the current aspect setter, keeping existing query-string behavior. If insights are empty, keep the real spectrum and display “尚未形成可验证洞察” plus “先审阅主题信号与原始证据”; do not add synthetic AI copy. Otherwise retain confidence, claim, recommendation, unknowns and evidence action.

Give `EvidenceDrawer` `role="dialog"`, `aria-modal="true"`, a labelled close button, and separate loading, failure, retry and empty content. The evidence fetch sets loading before requesting and retains selected IDs so retry executes the exact same request.

- [ ] **Step 4: Verify tests and lint**

Run: `Set-Location frontend; npm test -- insights.test.tsx evidence-drawer.test.tsx; npm run lint`

Expected: PASS; empty insights stay honest and selection uses the current filter flow.

- [ ] **Step 5: Commit**

Run: `git add frontend/src/components/signal-spectrum.tsx frontend/src/app/insights/page.tsx frontend/src/components/evidence-drawer.tsx frontend/tests/insights.test.tsx frontend/tests/evidence-drawer.test.tsx; git commit -m "feat: add auditable insight signal spectrum"`

### Task 4: Complete decision/risk workspaces and verify locally

**Files:** Modify `frontend/src/app/decisions/page.tsx`, `frontend/src/app/risks/page.tsx`, `frontend/src/components/risk-timeline.tsx`, `frontend/src/app/globals.css`, `frontend/tests/risks.test.tsx`, `frontend/tests/decisions.test.tsx`.

**Interfaces:** `RiskTimeline({ risks }): JSX.Element` receives `OverviewResponse["risks"]` and renders a descending-score copy. A testable `DecisionResult({ card, onOpenTrace }): JSX.Element`, if extraction is necessary, receives `DecisionCard` and retains feedback and Trace behavior.

- [ ] **Step 1: Write failing risk-order and Trace tests**

```tsx
render(<RiskTimeline risks={[lowRisk, highRisk]} />);
expect(screen.getAllByRole("article")[0]).toHaveTextContent("高分事件");
expect(screen.getByRole("link", { name: "打开权威来源" })).toHaveAttribute("href", highRisk.source_url);
render(<DecisionResult card={card} onOpenTrace={onOpenTrace} />);
await user.click(screen.getByRole("button", { name: "查看 Trace" }));
expect(onOpenTrace).toHaveBeenCalledOnce();
```

- [ ] **Step 2: Verify tests fail**

Run: `Set-Location frontend; npm test -- risks.test.tsx decisions.test.tsx`

Expected: FAIL because risks keep API order and the decision result is not isolated.

- [ ] **Step 3: Implement risks, decision result, and final polish**

Render risks from `[...risks].sort((a, b) => b.score - a.score)` without mutating props. Preserve date, event type, risk score, evidence quality, source, owner, status, mitigation and feedback; put score contribution fields in native `<details><summary>查看评分依据</summary>…</details>`. Extract the decision result only when needed for the test, retaining exact dataset version, problem, hypothesis, score breakdown, feedback and Trace trigger.

Finish CSS across dashboard, spectrum, form, drawer and timeline with the approved palette, short opacity/transform transitions, broad focus rings and responsive drawer sizing. Use no third-party dependency or remote asset.

- [ ] **Step 4: Run all automated and Docker checks**

Run: `Set-Location frontend; npm test; npm run lint; npm run build; Set-Location ..; docker compose up --build -d; docker compose ps; powershell -ExecutionPolicy Bypass -File .\scripts\verify_local.ps1`

Expected: frontend checks exit 0, API is healthy, web maps port 3000, and local verification passes.

- [ ] **Step 5: Browser acceptance check and commit**

Open `http://localhost:3000`, `/insights`, `/decisions`, and `/risks` at desktop and 900px-or-narrower. Confirm navigation, fold, evidence drawer, empty insight state, decision creation, feedback, Trace and source links are reachable. Then run: `git add frontend/src/app/decisions/page.tsx frontend/src/app/risks/page.tsx frontend/src/components/risk-timeline.tsx frontend/src/app/globals.css frontend/tests/risks.test.tsx frontend/tests/decisions.test.tsx; git commit -m "feat: complete AI decision studio workspaces"`.

## Plan Self-Review

- **Spec coverage:** Tasks 1–4 cover visual system, accessibility, responsive motion, conclusion-first evidence chain, real signal exploration, truthful empty states, decision/Trace/feedback continuity, risk source and detail, and local verification.
- **Placeholder scan:** 没有未完成标记、延后实现项或未定义的异常处理任务。每项任务都有明确文件、接口、测试代码、命令和预期结果。
- **Type consistency:** `DecisionHero` receives an overview opportunity and evidence-ID callback; `SignalSpectrum` receives metrics and returns an aspect; `EvidenceDrawer` retains `Evidence`; decision and risk types stay the existing source of truth.
