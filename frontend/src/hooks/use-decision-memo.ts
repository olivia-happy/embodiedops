"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getDecisionMemo, getDecisionMemoJob, getModelHealth, startDecisionMemo } from "@/lib/api";
import type { DecisionMemo, MemoGenerationJob, ModelHealth } from "@/lib/types";

const safeMessage = "决策备忘录暂不可用，请检查本地服务状态后重试。";

type WorkflowState = {
  datasetVersionId: string | undefined;
  memo: DecisionMemo | null;
  job: MemoGenerationJob | null;
  health: ModelHealth | null;
  loading: boolean;
  error: string | null;
};

function emptyState(datasetVersionId: string | undefined): WorkflowState {
  return {
    datasetVersionId,
    memo: null,
    job: null,
    health: null,
    loading: Boolean(datasetVersionId),
    error: null,
  };
}

function isAbortError(cause: unknown): boolean {
  return cause instanceof Error && cause.name === "AbortError";
}

export function useDecisionMemo(datasetVersionId: string | undefined) {
  const [state, setState] = useState<WorkflowState>(() => emptyState(datasetVersionId));
  const versionRef = useRef(datasetVersionId);
  const refreshControllerRef = useRef<AbortController | null>(null);
  const pollControllerRef = useRef<AbortController | null>(null);
  const refreshEpochRef = useRef(0);
  const generateEpochRef = useRef(0);

  // Updating the ref during render makes stale async continuations fail their guard
  // before the dataset-change effect gets a chance to abort their requests.
  versionRef.current = datasetVersionId;

  const refresh = useCallback(async () => {
    const version = datasetVersionId;
    if (!version) return;

    refreshControllerRef.current?.abort();
    const controller = new AbortController();
    refreshControllerRef.current = controller;
    const epoch = ++refreshEpochRef.current;
    const isCurrent = () => (
      !controller.signal.aborted
      && versionRef.current === version
      && refreshEpochRef.current === epoch
    );

    setState((current) => current.datasetVersionId === version
      ? { ...current, loading: true, error: null }
      : emptyState(version));

    try {
      const [nextMemo, nextHealth] = await Promise.all([
        getDecisionMemo(version, controller.signal),
        getModelHealth(controller.signal),
      ]);
      if (!isCurrent()) return;
      if (nextMemo && nextMemo.dataset_version_id !== version) {
        throw new Error("memo dataset version mismatch");
      }
      setState((current) => current.datasetVersionId === version
        ? { ...current, memo: nextMemo, health: nextHealth, error: null }
        : current);
    } catch (cause) {
      if (isAbortError(cause) || !isCurrent()) return;
      setState((current) => current.datasetVersionId === version
        ? { ...current, error: safeMessage }
        : current);
    } finally {
      if (isCurrent()) {
        setState((current) => current.datasetVersionId === version
          ? { ...current, loading: false }
          : current);
      }
      if (refreshControllerRef.current === controller) {
        refreshControllerRef.current = null;
      }
    }
  }, [datasetVersionId]);

  useEffect(() => {
    refreshControllerRef.current?.abort();
    pollControllerRef.current?.abort();
    refreshEpochRef.current += 1;
    generateEpochRef.current += 1;
    setState(emptyState(datasetVersionId));
    if (datasetVersionId) void refresh();

    return () => {
      refreshControllerRef.current?.abort();
      pollControllerRef.current?.abort();
      refreshEpochRef.current += 1;
      generateEpochRef.current += 1;
    };
  }, [datasetVersionId, refresh]);

  const visibleState = state.datasetVersionId === datasetVersionId
    ? state
    : emptyState(datasetVersionId);
  const jobIdToPoll = visibleState.job?.id;

  useEffect(() => {
    const version = datasetVersionId;
    if (!version || !jobIdToPoll) return;

    const jobId = jobIdToPoll;
    let active = true;
    let timer: number | undefined;

    const schedule = () => {
      timer = window.setTimeout(() => { void poll(); }, 800);
    };

    const poll = async () => {
      if (!active || versionRef.current !== version) return;
      const controller = new AbortController();
      pollControllerRef.current = controller;
      let shouldContinue = true;
      const isCurrent = () => (
        active
        && !controller.signal.aborted
        && versionRef.current === version
      );

      try {
        const nextJob = await getDecisionMemoJob(jobId, controller.signal);
        if (!isCurrent()) return;
        if (nextJob.id !== jobId || nextJob.dataset_version_id !== version) {
          throw new Error("memo job dataset version mismatch");
        }

        setState((current) => current.datasetVersionId === version
          ? { ...current, job: nextJob, error: null }
          : current);

        if (nextJob.status === "completed") {
          shouldContinue = false;
          const nextMemo = await getDecisionMemo(version, controller.signal);
          if (!isCurrent()) return;
          if (nextMemo && nextMemo.dataset_version_id !== version) {
            throw new Error("completed memo dataset version mismatch");
          }
          setState((current) => current.datasetVersionId === version
            ? { ...current, memo: nextMemo, error: null }
            : current);
        } else if (nextJob.status === "failed") {
          shouldContinue = false;
        }
      } catch (cause) {
        if (isAbortError(cause) || !isCurrent()) return;
        setState((current) => current.datasetVersionId === version
          ? { ...current, error: safeMessage }
          : current);
      } finally {
        if (pollControllerRef.current === controller) {
          pollControllerRef.current = null;
        }
        if (isCurrent() && shouldContinue) schedule();
      }
    };

    schedule();
    return () => {
      active = false;
      if (timer !== undefined) window.clearTimeout(timer);
      pollControllerRef.current?.abort();
    };
  }, [datasetVersionId, jobIdToPoll]);

  const generate = useCallback(async () => {
    const version = datasetVersionId;
    if (!version) return;
    const epoch = ++generateEpochRef.current;
    setState((current) => current.datasetVersionId === version
      ? { ...current, error: null }
      : emptyState(version));

    try {
      const nextJob = await startDecisionMemo(version);
      if (versionRef.current !== version || generateEpochRef.current !== epoch) return;
      if (nextJob.dataset_version_id !== version) throw new Error("generated job dataset version mismatch");
      setState((current) => current.datasetVersionId === version
        ? { ...current, job: nextJob, error: null }
        : current);
    } catch {
      if (versionRef.current !== version || generateEpochRef.current !== epoch) return;
      setState((current) => current.datasetVersionId === version
        ? { ...current, error: safeMessage }
        : current);
    }
  }, [datasetVersionId]);

  return {
    memo: visibleState.memo,
    job: visibleState.job,
    health: visibleState.health,
    loading: visibleState.loading,
    error: visibleState.error,
    generate,
    retry: generate,
    refresh,
  };
}
