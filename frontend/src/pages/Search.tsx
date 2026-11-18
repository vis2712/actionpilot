import { useState, type FormEvent } from "react";
import { api, type SearchHit } from "../api";
import { ErrorBox, errText, fmtDate } from "../ui";

export default function Search() {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setHits(await api.search(q));
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <h2>Search past meetings</h2>
      <p className="muted small">Hybrid search (meaning + keywords) across every summary and transcript you own.</p>
      <form className="row" onSubmit={run}>
        <input
          className="grow"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="What did we decide about pricing?"
          minLength={2}
          required
        />
        <button className="btn primary" disabled={busy}>
          Search
        </button>
      </form>
      <ErrorBox error={error} />
      {hits && hits.length === 0 && <p className="muted">No matches.</p>}
      <ul className="hits">
        {hits?.map((h, i) => (
          <li key={i}>
            <a href={`#/meetings/${h.meeting_id}`}>
              <strong>{h.title}</strong>
            </a>
            <span className="muted small">
              {" "}
              · {fmtDate(h.meeting_date)} · {h.kind}
            </span>
            <pre className="excerpt">{h.content}</pre>
          </li>
        ))}
      </ul>
    </section>
  );
}
