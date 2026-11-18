import { useEffect, useState } from "react";
import { api, auth, setUnauthorizedHandler } from "./api";
import Dashboard from "./pages/Dashboard";
import Login from "./pages/Login";
import MeetingDetail from "./pages/MeetingDetail";
import Meetings from "./pages/Meetings";
import Search from "./pages/Search";
import Tasks from "./pages/Tasks";
import { go, useRoute } from "./ui";

const NAV = [
  { path: "meetings", label: "Meetings" },
  { path: "tasks", label: "Tasks" },
  { path: "search", label: "Search" },
  { path: "dashboard", label: "Dashboard" },
];

export default function App() {
  const [email, setEmail] = useState<string | null>(null);
  const [checking, setChecking] = useState(Boolean(auth.token));
  const route = useRoute();

  useEffect(() => {
    setUnauthorizedHandler(() => setEmail(null));
    if (!auth.token) return;
    api
      .me()
      .then((u) => setEmail(u.email))
      .catch(() => (auth.token = null))
      .finally(() => setChecking(false));
  }, []);

  if (checking) return <div className="center muted">Loading…</div>;
  if (!email) return <Login onLogin={setEmail} />;

  const signOut = () => {
    auth.token = null;
    setEmail(null);
  };

  const [page, id] = route;
  return (
    <div className="app">
      <header className="topbar">
        <a className="brand" href="#/meetings">
          <img src="/favicon.svg" alt="" width={26} height={26} />
          ActionPilot
        </a>
        <nav>
          {NAV.map((n) => (
            <a key={n.path} href={`#/${n.path}`} className={page === n.path ? "active" : ""}>
              {n.label}
            </a>
          ))}
        </nav>
        <div className="user">
          <span className="muted">{email}</span>
          <button className="btn ghost small" onClick={signOut}>
            Sign out
          </button>
        </div>
      </header>
      <main className="content">
        {page === "meetings" && id ? (
          <MeetingDetail id={id} onDeleted={() => go("meetings")} />
        ) : page === "tasks" ? (
          <Tasks />
        ) : page === "search" ? (
          <Search />
        ) : page === "dashboard" ? (
          <Dashboard />
        ) : (
          <Meetings />
        )}
      </main>
    </div>
  );
}
