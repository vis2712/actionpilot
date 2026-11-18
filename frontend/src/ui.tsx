// Small shared building blocks: routing, polling, status pills, formatting.

import { useEffect, useRef, useState } from "react";
import type { Status } from "./api";

/** Hash routing (#/meetings/123) — no router library needed, and it works on any static host. */
export function useRoute(): string[] {
  const read = () => (window.location.hash.replace(/^#\/?/, "") || "meetings").split("/");
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const onChange = () => setRoute(read());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

export const go = (path: string) => {
  window.location.hash = `#/${path}`;
};

/** Runs `fn` now and then every `ms` while `active` is true. */
export function usePolling(fn: () => void, ms: number, active: boolean) {
  const saved = useRef(fn);
  saved.current = fn;
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => saved.current(), ms);
    return () => window.clearInterval(id);
  }, [ms, active]);
}

const STATUS_LABEL: Record<Status, string> = {
  queued: "Queued",
  transcribing: "Transcribing",
  analyzing: "Analyzing",
  retrying: "Retrying",
  awaiting_approval: "Needs approval",
  sending: "Sending",
  finishing: "Finishing",
  completed: "Completed",
  failed: "Failed",
};

export function StatusPill({ status }: { status: Status }) {
  return <span className={`pill pill-${status}`}>{STATUS_LABEL[status] ?? status}</span>;
}

export function Priority({ value }: { value: string }) {
  return <span className={`prio prio-${value}`}>{value}</span>;
}

export function ErrorBox({ error }: { error: string | null }) {
  return error ? <div className="error-box">{error}</div> : null;
}

export const fmtDate = (iso: string | null | undefined) =>
  iso ? new Date(iso.length === 10 ? `${iso}T00:00:00` : iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "—";

export const fmtDateTime = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "—";

export const fmtMs = (ms: number | null | undefined) =>
  ms == null ? "—" : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;

export const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));
