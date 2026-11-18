import { useCallback, useEffect, useState } from "react";
import { api, IN_PROGRESS, type Email, type Meeting, type Step, type Task } from "../api";
import { ErrorBox, errText, fmtDate, fmtDateTime, fmtMs, Priority, StatusPill, usePolling } from "../ui";

export default function MeetingDetail({ id, onDeleted }: { id: string; onDeleted: () => void }) {
  const [m, setM] = useState<Meeting | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.meeting(id).then(setM).catch((e) => setError(errText(e)));
  }, [id]);
  useEffect(load, [load]);
  usePolling(load, 1500, Boolean(m && IN_PROGRESS.includes(m.status)));

  const act = (fn: () => Promise<unknown>) => async () => {
    setError(null);
    try {
      await fn();
      load();
    } catch (e) {
      setError(errText(e));
    }
  };

  if (!m) return error ? <ErrorBox error={error} /> : <p className="muted">Loading…</p>;

  const working = IN_PROGRESS.includes(m.status);
  return (
    <div className="stack">
      <a href="#/meetings" className="muted small">
        ← All meetings
      </a>
      <section className="card header-card">
        <div>
          <h1>{m.title}</h1>
          <div className="muted small">
            {fmtDate(m.meeting_date)} · {m.source === "webhook" ? "received by webhook" : m.filename ?? "pasted transcript"}
            {m.participants.length > 0 && ` · ${m.participants.join(", ")}`}
          </div>
        </div>
        <div className="actions">
          <StatusPill status={m.status} />
          {m.status === "failed" && (
            <button className="btn small" onClick={act(() => api.retry(id))}>
              Retry from failed step
            </button>
          )}
          {!working && (
            <button className="btn ghost small" onClick={act(() => api.reprocess(id))}>
              Run again
            </button>
          )}
          <button
            className="btn ghost small danger"
            onClick={act(async () => {
              await api.deleteMeeting(id);
              onDeleted();
            })}
          >
            Delete
          </button>
        </div>
      </section>

      <ErrorBox error={error} />
      {m.error && <div className={m.status === "failed" ? "error-box" : "warn-box"}>{m.error}</div>}
      {working && (
        <div className="info-box">
          <span className="spinner" /> The agent is working ({m.status})… this page updates by itself.
        </div>
      )}

      {m.status === "awaiting_approval" && m.email && (
        <Approval email={m.email} onDecide={(body) => act(() => api.approve(id, body))()} />
      )}

      <div className="grid-2">
        <section className="card">
          <h2>Summary</h2>
          <p>{m.summary ?? <span className="muted">Not ready yet.</span>}</p>
          <h3>Decisions</h3>
          {m.decisions.length ? (
            <ul>
              {m.decisions.map((d, i) => (
                <li key={i}>{d}</li>
              ))}
            </ul>
          ) : (
            <p className="muted">None recorded.</p>
          )}
        </section>
        <section className="card">
          <h2>Follow-up email</h2>
          {m.email ? <EmailView email={m.email} /> : <p className="muted">No email drafted.</p>}
        </section>
      </div>

      <section className="card">
        <h2>Action items</h2>
        <TaskTable tasks={m.tasks} onToggle={(t) => act(() => api.updateTask(t.id, { status: t.status === "done" ? "open" : "done" }))()} />
      </section>

      <div className="grid-2">
        <section className="card">
          <h2>Agent timeline</h2>
          <Timeline steps={m.steps} />
        </section>
        <section className="card">
          <h2>Run details</h2>
          <dl className="facts">
            <dt>Processing time</dt>
            <dd>{fmtMs(m.processing_ms)}</dd>
            <dt>LLM calls</dt>
            <dd>{m.usage.llm_calls ?? 0}</dd>
            <dt>Tokens (in / out)</dt>
            <dd>
              {(m.usage.prompt_tokens ?? 0).toLocaleString()} / {(m.usage.completion_tokens ?? 0).toLocaleString()}
            </dd>
            <dt>Cost</dt>
            <dd>${(m.usage.cost_usd ?? 0).toFixed(5)}</dd>
            <dt>Attempts · run</dt>
            <dd>
              {m.attempts} · #{m.run_no}
            </dd>
          </dl>
          <details>
            <summary>Transcript</summary>
            <pre className="transcript">{m.transcript ?? "Not transcribed yet."}</pre>
          </details>
        </section>
      </div>
    </div>
  );
}

function Approval({ email, onDecide }: { email: Email; onDecide: (b: Parameters<typeof api.approve>[1]) => void }) {
  const [to, setTo] = useState(email.recipients.join(", "));
  const [subject, setSubject] = useState(email.subject);
  const [body, setBody] = useState(email.body);
  const [busy, setBusy] = useState(false);
  const decide = (action: "approve" | "reject") => {
    setBusy(true);
    onDecide(
      action === "approve"
        ? { action, to: to.split(/[,;\s]+/).filter(Boolean), subject, body }
        : { action },
    );
  };
  return (
    <section className="card approval">
      <h2>✋ Your approval is needed</h2>
      <p className="muted small">
        The agent drafted this follow-up. Nothing is sent until you approve. Edit anything you like first.
      </p>
      {email.blocked_recipients.length > 0 && (
        <div className="warn-box">
          Guardrail: removed {email.blocked_recipients.join(", ")} — not in this meeting's participant list.
        </div>
      )}
      <label>
        To
        <input value={to} onChange={(e) => setTo(e.target.value)} />
      </label>
      <label>
        Subject
        <input value={subject} onChange={(e) => setSubject(e.target.value)} />
      </label>
      <label>
        Body
        <textarea rows={12} value={body} onChange={(e) => setBody(e.target.value)} />
      </label>
      <div className="row">
        <button className="btn primary" disabled={busy} onClick={() => decide("approve")}>
          Approve & send
        </button>
        <button className="btn ghost" disabled={busy} onClick={() => decide("reject")}>
          Reject
        </button>
      </div>
    </section>
  );
}

function EmailView({ email }: { email: Email }) {
  return (
    <div className="email-view">
      <div className="small">
        <span className={`pill pill-email-${email.status}`}>{email.status}</span>
        {email.sent_at && <span className="muted"> · sent {fmtDateTime(email.sent_at)}</span>}
      </div>
      <div className="small">
        <strong>To:</strong> {email.recipients.join(", ")}
      </div>
      <div className="small">
        <strong>Subject:</strong> {email.subject}
      </div>
      <pre className="email-body">{email.body}</pre>
      {email.error && <div className="error-box">{email.error}</div>}
    </div>
  );
}

export function TaskTable({ tasks, onToggle, showMeeting }: { tasks: Task[]; onToggle: (t: Task) => void; showMeeting?: boolean }) {
  if (!tasks.length) return <p className="muted">No action items.</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th />
            <th>Task</th>
            <th>Owner</th>
            <th>Due</th>
            <th>Priority</th>
            <th>Reminder</th>
            {showMeeting && <th>Meeting</th>}
          </tr>
        </thead>
        <tbody>
          {tasks.map((t) => (
            <tr key={t.id} className={t.status === "done" ? "done" : ""}>
              <td>
                <input type="checkbox" checked={t.status === "done"} onChange={() => onToggle(t)} aria-label="Done" />
              </td>
              <td>{t.title}</td>
              <td>{t.assignee ?? <span className="muted">—</span>}</td>
              <td className={t.overdue ? "overdue" : ""}>{fmtDate(t.due_date)}</td>
              <td>
                <Priority value={t.priority} />
              </td>
              <td className="small">
                {t.reminder_status ? `${t.reminder_status} · ${fmtDateTime(t.remind_at)}` : <span className="muted">—</span>}
              </td>
              {showMeeting && (
                <td className="small">
                  {t.meeting_id ? <a href={`#/meetings/${t.meeting_id}`}>{t.meeting_title}</a> : <span className="muted">manual</span>}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const STEP_LABEL: Record<string, string> = {
  transcribe: "Transcribed the recording",
  extract: "Extracted notes",
  index: "Indexed for search",
  agent: "Agent decided",
  send_email: "Sent the email",
  finish: "Finished",
};

function Timeline({ steps }: { steps: Step[] }) {
  if (!steps.length) return <p className="muted">Nothing yet.</p>;
  return (
    <ol className="timeline">
      {steps.map((s, i) => {
        const tool = s.step.startsWith("tool:") ? s.step.slice(5) : null;
        const calls = (s.detail.tool_calls as string[] | undefined) ?? [];
        return (
          <li key={i} className={tool ? "tool" : ""}>
            <div className="tl-head">
              <span>
                {tool ? (
                  <>
                    🛠 <code>{tool}</code>
                  </>
                ) : (
                  STEP_LABEL[s.step] ?? s.step
                )}
                {s.step === "agent" && (
                  <span className="muted small"> → {calls.length ? calls.join(", ") : "done"}</span>
                )}
              </span>
              <span className="muted small">{fmtMs(s.ms)}</span>
            </div>
            <details>
              <summary className="small muted">details</summary>
              <pre>{JSON.stringify(s.detail, null, 2)}</pre>
            </details>
          </li>
        );
      })}
    </ol>
  );
}
