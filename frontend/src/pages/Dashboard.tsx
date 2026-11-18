import { useEffect, useState } from "react";
import { api, type Stats } from "../api";
import { ErrorBox, errText, fmtMs } from "../ui";

function Stat({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <div className="stat">
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
      {hint && <div className="muted small">{hint}</div>}
    </div>
  );
}

export default function Dashboard() {
  const [s, setS] = useState<Stats | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.stats().then(setS).catch((e) => setError(errText(e)));
  }, []);

  if (error) return <ErrorBox error={error} />;
  if (!s) return <p className="muted">Loading…</p>;
  return (
    <div className="stack">
      <section className="card">
        <h2>Meetings</h2>
        <div className="stats">
          <Stat label="Meetings" value={s.meetings.meetings} />
          <Stat label="Completed" value={s.meetings.completed} />
          <Stat label="Waiting for approval" value={s.meetings.awaiting_approval} />
          <Stat label="Failed" value={s.meetings.failed} />
        </div>
      </section>
      <section className="card">
        <h2>Work created</h2>
        <div className="stats">
          <Stat label="Open tasks" value={s.tasks.open} hint={`${s.tasks.overdue} overdue`} />
          <Stat label="Done tasks" value={s.tasks.done} />
          <Stat label="Reminders" value={s.reminders.scheduled} hint={`${s.reminders.sent} sent`} />
          <Stat label="Emails sent" value={s.emails.sent} hint={`${s.emails.rejected} rejected by you`} />
          <Stat label="Guardrail blocks" value={s.emails.with_blocked_recipients} hint="drafts with removed recipients" />
        </div>
      </section>
      <section className="card">
        <h2>Speed and cost</h2>
        <div className="stats">
          <Stat label="Avg processing" value={fmtMs(s.meetings.avg_processing_ms)} hint="transcript → draft ready" />
          <Stat label="p95 processing" value={fmtMs(s.meetings.p95_processing_ms)} />
          <Stat
            label="Tokens"
            value={(s.meetings.prompt_tokens + s.meetings.completion_tokens).toLocaleString()}
            hint={`${s.meetings.prompt_tokens.toLocaleString()} in / ${s.meetings.completion_tokens.toLocaleString()} out`}
          />
          <Stat label="Total cost" value={`$${s.meetings.cost_usd.toFixed(4)}`} />
        </div>
      </section>
    </div>
  );
}
