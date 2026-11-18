import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, type Task } from "../api";
import { ErrorBox, errText } from "../ui";
import { TaskTable } from "./MeetingDetail";

export default function Tasks() {
  const [filter, setFilter] = useState<"open" | "done" | "all">("open");
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [assignee, setAssignee] = useState("");
  const [due, setDue] = useState("");

  const load = useCallback(() => {
    api.tasks(filter).then(setTasks).catch((e) => setError(errText(e)));
  }, [filter]);
  useEffect(load, [load]);

  async function add(e: FormEvent) {
    e.preventDefault();
    try {
      await api.createTask({ title, assignee: assignee || undefined, due_date: due || undefined });
      setTitle("");
      setAssignee("");
      setDue("");
      load();
    } catch (err) {
      setError(errText(err));
    }
  }

  const toggle = async (t: Task) => {
    try {
      await api.updateTask(t.id, { status: t.status === "done" ? "open" : "done" });
      load();
    } catch (err) {
      setError(errText(err));
    }
  };

  return (
    <section className="card">
      <div className="card-head">
        <h2>Tasks</h2>
        <div className="tabs">
          {(["open", "done", "all"] as const).map((f) => (
            <button key={f} className={filter === f ? "active" : ""} onClick={() => setFilter(f)}>
              {f[0].toUpperCase() + f.slice(1)}
            </button>
          ))}
        </div>
      </div>
      <form className="row add-task" onSubmit={add}>
        <input className="grow" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Add a task…" required />
        <input value={assignee} onChange={(e) => setAssignee(e.target.value)} placeholder="Owner" />
        <input type="date" value={due} onChange={(e) => setDue(e.target.value)} />
        <button className="btn">Add</button>
      </form>
      <ErrorBox error={error} />
      {tasks === null ? <p className="muted">Loading…</p> : <TaskTable tasks={tasks} onToggle={toggle} showMeeting />}
    </section>
  );
}
