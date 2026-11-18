import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, IN_PROGRESS, type MeetingRow } from "../api";
import { ErrorBox, errText, fmtDate, fmtMs, go, StatusPill, usePolling } from "../ui";

const SAMPLE = `Priya: Thanks everyone. Agenda today: the mobile app beta and the pricing page.
Rahul: The payment bug on Android is still open. It's blocking the beta, so it's urgent.
Rahul: I'll fix the Android payment bug by Wednesday.
Ananya: We agreed to launch the beta to 200 users on the 25th, if the bug is fixed.
Priya: Good. Ananya, can you own the beta invite list?
Ananya: Yes, I'll prepare the beta invite list by Friday.
Karan: For pricing, we decided to keep the free plan but limit it to 3 projects.
Karan: I'll update the pricing page copy by next Monday.
Priya: I'll send the investor update after the beta goes live.
Priya: Great, thanks all. Same time next week.`;

const today = () => new Date().toLocaleDateString("en-CA"); // YYYY-MM-DD in local time

export default function Meetings() {
  const [rows, setRows] = useState<MeetingRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.meetings().then(setRows).catch((e) => setError(errText(e)));
  }, []);
  useEffect(load, [load]);
  usePolling(load, 2500, Boolean(rows?.some((r) => IN_PROGRESS.includes(r.status))));

  return (
    <div className="grid-2">
      <NewMeeting onCreated={(id) => go(`meetings/${id}`)} />
      <section className="card">
        <h2>Your meetings</h2>
        <ErrorBox error={error} />
        {rows === null ? (
          <p className="muted">Loading…</p>
        ) : rows.length === 0 ? (
          <p className="muted">No meetings yet. Paste the sample transcript on the left to see the agent work.</p>
        ) : (
          <ul className="meeting-list">
            {rows.map((m) => (
              <li key={m.id}>
                <a href={`#/meetings/${m.id}`}>
                  <div>
                    <strong>{m.title}</strong>
                    <div className="muted small">
                      {fmtDate(m.meeting_date)} · {m.task_count} tasks
                      {m.source === "webhook" && " · via webhook"}
                      {m.processing_ms != null && ` · ${fmtMs(m.processing_ms)}`}
                    </div>
                  </div>
                  <StatusPill status={m.status} />
                </a>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function NewMeeting({ onCreated }: { onCreated: (id: string) => void }) {
  const [mode, setMode] = useState<"paste" | "file">("paste");
  const [title, setTitle] = useState("");
  const [date, setDate] = useState(today());
  const [participants, setParticipants] = useState("");
  const [transcript, setTranscript] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const form = new FormData();
    if (title) form.set("title", title);
    form.set("meeting_date", date);
    if (participants) form.set("participants", participants);
    if (mode === "file" && file) form.set("file", file);
    else form.set("transcript", transcript);
    try {
      const m = await api.createMeeting(form);
      onCreated(m.id);
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card" onSubmit={submit}>
      <h2>New meeting</h2>
      <p className="muted small">
        The agent extracts a summary, decisions and action items, creates tasks, schedules reminders and drafts a
        follow-up email for you to approve.
      </p>
      <label>
        Title <span className="muted">(optional)</span>
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Weekly product sync" />
      </label>
      <div className="row">
        <label>
          Meeting date
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)} required />
        </label>
        <label className="grow">
          Participants' emails <span className="muted">(who may get the follow-up)</span>
          <input
            value={participants}
            onChange={(e) => setParticipants(e.target.value)}
            placeholder="priya@company.com, rahul@company.com"
          />
        </label>
      </div>
      <div className="tabs">
        <button type="button" className={mode === "paste" ? "active" : ""} onClick={() => setMode("paste")}>
          Paste transcript
        </button>
        <button type="button" className={mode === "file" ? "active" : ""} onClick={() => setMode("file")}>
          Upload file
        </button>
      </div>
      {mode === "paste" ? (
        <>
          <textarea
            rows={10}
            value={transcript}
            onChange={(e) => setTranscript(e.target.value)}
            placeholder={"Speaker: what they said\nAnother speaker: …"}
            required
          />
          <button
            type="button"
            className="btn ghost small"
            onClick={() => {
              setTranscript(SAMPLE);
              setTitle((t) => t || "Mobile beta & pricing sync");
            }}
          >
            Use a sample transcript
          </button>
        </>
      ) : (
        <label className="dropzone">
          <input
            type="file"
            accept=".txt,.md,.vtt,.srt,.mp3,.m4a,.wav,.webm,.ogg,.mp4"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            required
          />
          <span>{file ? file.name : "Choose a transcript (.txt .vtt .srt) or a recording (.mp3 .m4a .wav …, max 25 MB)"}</span>
        </label>
      )}
      <ErrorBox error={error} />
      <button className="btn primary" disabled={busy}>
        {busy ? "Uploading…" : "Run the agent"}
      </button>
    </form>
  );
}
