import React, { useState, useEffect, useRef } from "react";
import { createRoot } from "react-dom/client";
import {
  BookOpen,
  Compass,
  Headphones,
  Activity,
  Settings,
  ArrowUpRight,
  ArrowRight,
  Plus,
  Play,
  Pause,
  Mic,
  Square,
  ChevronLeft,
  ChevronRight,
  RefreshCw,
  Check,
  X,
  Volume2,
  Upload,
  Radio,
  CircleHelp,
  Bookmark,
  Clock,
  LogOut,
  FileText,
  RotateCcw,
  ExternalLink,
} from "lucide-react";
import {
  api,
  post,
  Row,
  fileUrl,
  date,
  minutes,
  pendingRecording,
} from "./api";
import "./style.css";

type Page = "library" | "discover" | "review" | "jobs" | "settings" | "learn";
const tabs: [Page, string, typeof BookOpen][] = [
  ["library", "My library", BookOpen],
  ["discover", "Find a paper", Compass],
  ["review", "Practice again", RotateCcw],
  ["jobs", "In the making", Activity],
  ["settings", "Settings", Settings],
];
function Badge({
  children,
  state = "",
}: {
  children: React.ReactNode;
  state?: string;
}) {
  return <span className={`badge ${state}`}>{children}</span>;
}
function App() {
  const [signed, setSigned] = useState(false),
    [checking, setChecking] = useState(true);
  const [page, setPage] = useState<Page>("library"),
    [active, setActive] = useState<string | null>(null);
  const [lessons, setLessons] = useState<Row[]>([]),
    [papers, setPapers] = useState<Row[]>([]),
    [jobs, setJobs] = useState<Row[]>([]),
    [recs, setRecs] = useState<Row[]>([]),
    [reviews, setReviews] = useState<Row[]>([]);
  const [status, setStatus] = useState<any>(null),
    [version, setVersion] = useState(0),
    [error, setError] = useState(""),
    [adding, setAdding] = useState(false);
  const [evidence, setEvidence] = useState<Row | null>(null);
  const refresh = () => setVersion((v) => v + 1);
  const act = async (fn: () => Promise<any>) => {
    try {
      await fn();
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  useEffect(() => {
    api("/status")
      .then((x) => {
        setStatus(x);
        setSigned(true);
      })
      .catch(() => {})
      .finally(() => setChecking(false));
    const out = () => setSigned(false);
    window.addEventListener("signed-out", out);
    return () => window.removeEventListener("signed-out", out);
  }, []);
  useEffect(() => {
    if (!signed) return;
    Promise.all([
      api<Row[]>("/lessons"),
      api<Row[]>("/papers"),
      api<Row[]>("/jobs"),
      api<Row[]>("/recommendations"),
      api<Row[]>("/reviews"),
    ])
      .then(([l, p, j, r, v]) => {
        setLessons(l);
        setPapers(p);
        setJobs(j);
        setRecs(r);
        setReviews(v);
      })
      .catch((e) => setError(e.message));
  }, [signed, version]);
  useEffect(() => {
    if (!signed) return;
    const events = new EventSource("/api/events");
    let timer: ReturnType<typeof setTimeout> | undefined;
    events.addEventListener("update", () => {
      if (!timer)
        timer = setTimeout(() => {
          refresh();
          timer = undefined;
        }, 1500);
    });
    const poll = setInterval(() => {
      api("/status")
        .then(setStatus)
        .catch(() => {});
      refresh();
    }, 15000);
    return () => {
      events.close();
      clearTimeout(timer);
      clearInterval(poll);
    };
  }, [signed]);
  const [recordBusy, setRecordBusy] = useState(false),
    [reviewTarget, setReviewTarget] = useState<Row | null>(null);
  const navigate = (p: Page) => {
    if (recordBusy) {
      setError("Finish your recording first.");
      return;
    }
    setPage(p);
  };
  const openLesson = (id: string, target: Row | null = null) => {
    setReviewTarget(target);
    setActive(id);
    navigate("learn");
  };
  const busy = jobs.filter((j) => ["queued", "running"].includes(j.state));
  if (checking)
    return (
      <div className="loading">
        <Radio size={32} />
        <p>Opening your studio…</p>
      </div>
    );
  if (!signed) return <Login onDone={() => setSigned(true)} />;
  return (
    <div className="shell">
      <aside className="sidebar">
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            navigate("library");
          }}
        >
          <span className="brand-symbol">
            <Radio size={23} />
          </span>
          <span>
            PaperSpeak<small>THE LEARNING STUDIO</small>
          </span>
        </a>
        <div className="nav-label">A LITTLE DEEPER, EVERY DAY</div>
        <nav>
          {tabs.map(([key, label, Icon]) => (
            <button
              key={key}
              className={
                page === key || (key === "library" && page === "learn")
                  ? "selected"
                  : ""
              }
              onClick={() => navigate(key)}
            >
              <Icon size={18} />
              {label}
              {key === "jobs" && busy.length > 0 && <b>{busy.length}</b>}
              {key === "review" && reviews.length > 0 && (
                <b>{reviews.length}</b>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-note">
          <div className="tiny-wave">
            <i />
            <i />
            <i />
            <i />
            <i />
            <i />
            <i />
          </div>
          <p>
            Big ideas.
            <br />
            Simple words.
            <br />
            Your own voice.
          </p>
        </div>
        <div className="local-status">
          <span
            className={`dot ${status?.worker && Date.now() / 1000 - status.worker.heartbeat < 90 ? "" : "off"}`}
          />
          <div>
            {status?.password_required === false ? "Open on this LAN" : "Private & local"}
            <small>
              {status?.gpu?.name?.replace("NVIDIA GeForce ", "") ||
                "Your Linux studio"}
            </small>
          </div>
        </div>
        {status?.password_required !== false && <button
          className="logout"
          disabled={recordBusy}
          onClick={() =>
            act(async () => {
              await post("/logout");
              setSigned(false);
            })
          }
        >
          <LogOut size={14} /> Sign out
        </button>}
      </aside>
      <main>
        <header className="topbar">
          <span>Your space to understand.</span>
          <span>
            {new Date().toLocaleDateString("en-US", {
              weekday: "long",
              month: "long",
              day: "numeric",
            })}
          </span>
        </header>
        {error && (
          <div className="notice error" role="alert">
            {error}
            <button aria-label="Dismiss message" onClick={() => setError("")}>
              <X size={16} />
            </button>
          </div>
        )}
        {page === "library" && (
          <>
            <section className="hero">
              <div>
                <div className="eyebrow">
                  <span />
                  YOUR PERSONAL PAPER RADIO
                </div>
                <h1>
                  A good paper.
                  <br />A clearer idea.
                </h1>
                <p>
                  Listen to the story behind the science.
                  <br />
                  Then find the words to tell it yourself.
                </p>
                <div className="actions">
                  <button className="primary" onClick={() => setAdding(true)}>
                    <Plus size={17} /> Add a paper
                  </button>
                  <button
                    className="text-button"
                    onClick={() => setPage("discover")}
                  >
                    Find something new <ArrowRight size={17} />
                  </button>
                </div>
              </div>
              <div className="hero-art" aria-hidden="true">
                <div className="orbit orbit-one" />
                <div className="orbit orbit-two" />
                <div className="paper-shape">
                  <div className="paper-tag">AN IDEA WORTH HEARING</div>
                  <div className="paper-lines">
                    <i />
                    <i />
                    <i />
                  </div>
                  <div className="sound-bars">
                    {[18, 34, 51, 29, 72, 92, 61, 37, 67, 45, 23, 40, 16].map(
                      (h, i) => (
                        <i key={i} style={{ height: h }} />
                      ),
                    )}
                  </div>
                  <div className="paper-bottom">
                    <span>01 / LISTEN & LEARN</span>
                    <Headphones size={21} />
                  </div>
                </div>
                <span className="art-stamp">
                  Made for
                  <br />
                  <em>curious minds.</em>
                </span>
              </div>
            </section>
            <div className="section-heading">
              <div>
                <div className="eyebrow">ONE IDEA AT A TIME</div>
                <h2>
                  Your library <span>{lessons.length}</span>
                </h2>
              </div>
              <span className="subtle">Pick up where you left off</span>
            </div>
            {lessons.length ? (
              <div className="lesson-grid">
                {lessons.map((l) => (
                  <button
                    className="lesson-card"
                    key={l.id}
                    onClick={() => openLesson(l.id)}
                  >
                    <div className="card-meta">
                      <Badge state={l.state === "ready" ? "ready" : l.job_state || l.state}>
                        {l.state === "ready"
                          ? "Ready to explore"
                          : `${l.ready_chapters} chapters ready${l.job_state === "failed" ? " · Needs attention" : l.job_state === "paused" ? " · Paused" : l.job_state === "cancelled" ? " · Stopped" : ""}`}
                      </Badge>
                      <span>{date(l.created)}</span>
                    </div>
                    <h3>{l.data.title}</h3>
                    <div className="card-bottom">
                      <span>
                        <Headphones size={16} />
                        {l.chapter_count || "—"} chapters
                      </span>
                      <span className="circle-arrow">
                        <ArrowUpRight size={20} />
                      </span>
                    </div>
                    <div className="card-progress">
                      <i
                        style={{
                          width: `${(100 * l.ready_chapters) / Math.max(1, l.chapter_count)}%`,
                        }}
                      />
                    </div>
                  </button>
                ))}
              </div>
            ) : (
              <div className="empty-library">
                <div className="empty-icon">
                  <BookOpen size={26} />
                </div>
                <h3>What would you like to understand?</h3>
                <p>
                  Add an arXiv paper or a PDF. We will turn it into a
                  conversation.
                </p>
                <div className="example-links">
                  {[
                    ["Attention Is All You Need", "1706.03762"],
                    ["How diffusion models work", "2006.11239"],
                    ["A closer look at LoRA", "2106.09685"],
                  ].map(([title, id]) => (
                    <button
                      key={id}
                      onClick={() =>
                        act(() => post("/papers/import", { reference: id }))
                      }
                    >
                      {title}
                      <ArrowUpRight size={15} />
                    </button>
                  ))}
                </div>
              </div>
            )}
            {papers.filter((p) => !p.lesson_count).length > 0 && (
              <section>
                <h2>Saved papers</h2>
                <div className="list">
                  {papers
                    .filter((p) => !p.lesson_count)
                    .slice(0, 15)
                    .map((p) => (
                      <div className="list-row" key={p.id}>
                        <FileText />
                        <div>
                          <strong>{p.title}</strong>
                          <small>{p.data.categories?.join(" · ")}</small>
                        </div>
                        <button
                          className="secondary"
                          onClick={() =>
                            act(() => post(`/papers/${p.id}/lessons`))
                          }
                        >
                          Make a lesson
                        </button>
                      </div>
                    ))}
                </div>
              </section>
            )}
          </>
        )}
        {page === "discover" && (
          <>
            <PageHeading
              eyebrow="FOLLOW YOUR CURIOSITY"
              title="Find your next idea."
              description="Papers from across AI, chosen for what they can teach you."
            />
            <div className="discovery-banner">
              <Compass size={24} />
              <p>
                We read the paper before we recommend it.
                <small>
                  New papers become lessons automatically. You can guide what
                  comes next.
                </small>
              </p>
              <button
                className="primary"
                onClick={() => act(() => post("/discover"))}
              >
                <RefreshCw size={16} /> Look for papers
              </button>
            </div>
            {recs.length ? (
              <div className="recommendations">
                {recs.map((r) => (
                  <article className="recommendation" key={r.id}>
                    <div className="card-meta">
                      <Badge state={r.state}>
                        {r.state.replaceAll("_", " ")}
                      </Badge>
                      <span>{r.day}</span>
                    </div>
                    <h3>{r.title}</h3>
                    <p>{r.data.why}</p>
                    <div className="learning-box">
                      <strong>You will learn</strong>
                      <p>{r.data.learn}</p>
                    </div>
                    <details>
                      <summary>Read the evidence ({r.data.source_ids?.length || 0})</summary>
                      <div className="actions">
                      {r.data.source_ids?.map((sid: string, i: number) => (
                        <button
                          className="source-chip"
                          key={sid}
                          onClick={() =>
                            api<Row>(`/sources/${encodeURIComponent(sid)}`)
                              .then(setEvidence)
                              .catch((e) => setError(e.message))
                          }
                        >
                          <FileText size={12} /> Evidence {i + 1}
                        </button>
                      ))}
                      </div>
                    </details>
                    {r.data.cautions?.length > 0 && (
                      <details>
                        <summary>Things to keep in mind</summary>
                        <ul>
                          {r.data.cautions.map((c: string, i: number) => (
                            <li key={i}>{c}</li>
                          ))}
                        </ul>
                      </details>
                    )}
                    <div className="actions">
                      <button
                        className="primary"
                        onClick={() =>
                          act(() => post(`/papers/${r.paper_id}/lessons`))
                        }
                      >
                        Make a lesson <ArrowRight size={16} />
                      </button>
                      <button
                        className={`secondary ${r.feedback === "interested" ? "active" : ""}`}
                        onClick={() =>
                          act(() =>
                            post(`/recommendations/${r.id}/feedback`, {
                              value: "interested",
                            }),
                          )
                        }
                      >
                        <Bookmark size={16} /> More like this
                      </button>
                      <button
                        className="text-button"
                        onClick={() =>
                          act(() =>
                            post(`/recommendations/${r.id}/feedback`, {
                              value: "not_interested",
                            }),
                          )
                        }
                      >
                        Not for me
                      </button>
                      <button
                        className="text-button"
                        onClick={() =>
                          act(() =>
                            post(`/recommendations/${r.id}/feedback`, {
                              value: "read",
                            }),
                          )
                        }
                      >
                        {r.feedback === "read"
                          ? "Marked as read"
                          : "Mark as read"}
                      </button>
                      <a
                        className="text-button"
                        target="_blank"
                        rel="noreferrer"
                        href={JSON.parse(r.paper_data).url}
                      >
                        Read the paper <ExternalLink size={14} />
                      </a>
                    </div>
                  </article>
                ))}
              </div>
            ) : (
              <Empty
                icon={Compass}
                title="A little discovery goes a long way."
                text="Start a search. We will read promising papers and explain why they are worth your time."
              />
            )}
          </>
        )}
        {page === "jobs" && (
          <>
            <PageHeading
              eyebrow="GOOD UNDERSTANDING TAKES CARE"
              title="In the making."
              description="You can leave this page. Your studio keeps working."
            />
            {jobs.length ? (
              <div className="jobs">
                {jobs.map((j) => (
                  <article className="job" key={j.id}>
                    <div className="job-top">
                      <Badge state={j.state}>{j.state}</Badge>
                      <span>
                        {j.kind === "lesson"
                          ? "A new lesson"
                          : j.kind === "discover"
                            ? "Finding good papers"
                            : j.kind === "practice"
                              ? "Your recording"
                              : j.kind === "benchmark"
                                ? "Comparing paper readers"
                                : ["calibration","phoneme_probe"].includes(j.kind)
                                  ? "Checking speech models"
                                  : "Adding a paper"}
                      </span>
                      <small>{date(j.created)}</small>
                    </div>
                    <h3>{j.stage}</h3>
                    <progress value={j.progress} max={1} />
                    {j.error && <p className="job-error">{j.error}</p>}
                    <div className="actions">
                      {["queued", "running"].includes(j.state) && (
                        <button
                          className="secondary"
                          onClick={() => act(() => post(`/jobs/${j.id}/pause`))}
                        >
                          <Pause size={14} /> Pause after this step
                        </button>
                      )}
                      {["paused", "failed", "cancelled"].includes(j.state) && (
                        <button
                          className="secondary"
                          onClick={() => act(() => post(`/jobs/${j.id}/retry`))}
                        >
                          <Play size={14} />{" "}
                          {j.state === "failed" ? "Try again" : "Resume"}
                        </button>
                      )}
                      {!["completed", "cancelled"].includes(j.state) && (
                        <button
                          className="text-button"
                          onClick={() =>
                            act(() => post(`/jobs/${j.id}/cancel`))
                          }
                        >
                          Stop
                        </button>
                      )}
                    </div>
                  </article>
                ))}
              </div>
            ) : (
              <Empty
                icon={Activity}
                title="All quiet here."
                text="Your next paper or recording will appear here."
              />
            )}
          </>
        )}
        {page === "review" && (
          <>
            <PageHeading
              eyebrow="MAKE IT YOURS"
              title="A second look."
              description="A small practice today helps an idea stay with you."
            />
            {reviews.length ? (
              <div className="list">
                {reviews.map((r) => (
                  <div className="list-row" key={r.id}>
                    <RotateCcw size={22} />
                    <div>
                      <strong>{JSON.parse(r.lesson_data).title}</strong>
                      <small>
                        {r.data.kind === "read"
                          ? "Speak a sentence again"
                          : "Explain an idea again"}
                      </small>
                    </div>
                    <button
                      className="primary"
                      onClick={() => openLesson(r.lesson_id, r)}
                    >
                      Practice
                    </button>
                    <button
                      className="secondary"
                      onClick={() =>
                        act(() =>
                          post(
                            `/reviews/${encodeURIComponent(r.id)}/complete`,
                            { again: false },
                          ),
                        )
                      }
                    >
                      I remember
                    </button>
                    <button
                      className="text-button"
                      onClick={() =>
                        act(() =>
                          post(
                            `/reviews/${encodeURIComponent(r.id)}/complete`,
                            { again: true },
                          ),
                        )
                      }
                    >
                      Tomorrow
                    </button>
                  </div>
                ))}
              </div>
            ) : (
              <Empty
                icon={Check}
                title="You are up to date."
                text="After you practice, we will bring useful sentences and ideas back at the right time."
              />
            )}
          </>
        )}
        {page === "settings" && (
          <SettingsPage onError={setError} status={status} />
        )}
        {page === "learn" && active && (
          <Learn
            id={active}
            reviewTarget={reviewTarget}
            onRecording={setRecordBusy}
            version={version}
            onError={setError}
            refresh={refresh}
            onBack={() => setPage("library")}
          />
        )}
        <footer>
          <span>PaperSpeak</span>
          <span>Read with care. Speak with confidence.</span>
          <span>All AI runs on your Linux PC.</span>
        </footer>
      </main>
      {evidence && (
        <SourceDrawer source={evidence} close={() => setEvidence(null)} />
      )}{" "}
      {adding && (
        <AddPaper
          close={() => setAdding(false)}
          onError={setError}
          onDone={refresh}
        />
      )}
    </div>
  );
}
function PageHeading({
  eyebrow,
  title,
  description,
}: {
  eyebrow: string;
  title: string;
  description: string;
}) {
  return (
    <section className="page-heading">
      <div className="eyebrow">{eyebrow}</div>
      <h1>{title}</h1>
      <p>{description}</p>
    </section>
  );
}
function Empty({
  icon: Icon,
  title,
  text,
}: {
  icon: typeof BookOpen;
  title: string;
  text: string;
}) {
  return (
    <div className="empty-library">
      <div className="empty-icon">
        <Icon size={27} />
      </div>
      <h3>{title}</h3>
      <p>{text}</p>
    </div>
  );
}
function Login({ onDone }: { onDone: () => void }) {
  const [password, setPassword] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  return (
    <div className="login-page">
      <div className="login-card">
        <span className="brand-symbol">
          <Radio size={28} />
        </span>
        <div className="eyebrow">WELCOME TO PAPERSPEAK</div>
        <h1>
          Your curiosity.
          <br />
          Your own voice.
        </h1>
        <p>Sign in to your private learning studio.</p>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            try {
              await post("/login", { password });
              onDone();
            } catch (e) {
              setError((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <label>
            Studio password
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              autoFocus
            />
          </label>
          {error && (
            <p className="error-text" role="alert">
              {error}
            </p>
          )}
          <button className="primary" disabled={busy}>
            {busy ? "Opening…" : "Come on in"}
            <ArrowRight size={18} />
          </button>
        </form>
        <small>
          Your password is shown by “studio access” on your Linux PC.
        </small>
      </div>
    </div>
  );
}
function AddPaper({
  close,
  onDone,
  onError,
}: {
  close: () => void;
  onDone: () => void;
  onError: (s: string) => void;
}) {
  const [ref, setRef] = useState(""),
    [busy, setBusy] = useState(false);
  const upload = async (file: File) => {
    setBusy(true);
    try {
      const form = new FormData();
      form.append("file", file);
      await api("/papers/upload", { method: "POST", body: form });
      onDone();
      close();
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="modal-backdrop" onClick={close}>
      <section
        className="modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Add a paper"
      >
        <button className="modal-close" onClick={close} aria-label="Close">
          <X />
        </button>
        <div className="eyebrow">START WITH A QUESTION</div>
        <h2>Add a paper.</h2>
        <p>We will read it carefully, then help you hear the big idea.</p>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            try {
              await post("/papers/import", { reference: ref });
              onDone();
              close();
            } catch (e) {
              onError((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <label>
            arXiv link or paper ID
            <input
              placeholder="https://arxiv.org/abs/1706.03762"
              value={ref}
              onChange={(e) => setRef(e.target.value)}
              required
            />
          </label>
          <button disabled={busy} className="primary">
            {busy ? "Adding…" : "Make my lesson"}
            <ArrowRight size={17} />
          </button>
        </form>
        <div className="or-divider">or bring your own paper</div>
        <label className="upload">
          <Upload size={20} /> Choose a PDF
          <input
            type="file"
            accept="application/pdf"
            disabled={busy}
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) upload(f);
            }}
          />
        </label>
        <small>Up to 100 MB. Your paper stays in your private studio.</small>
      </section>
    </div>
  );
}

function SettingsPage({
  onError,
  status,
}: {
  onError: (s: string) => void;
  status: any;
}) {
  const [s, setS] = useState<any>(null),
    [saved, setSaved] = useState(false);
  useEffect(() => {
    api("/settings")
      .then(setS)
      .catch((e) => onError(e.message));
  }, []);
  if (!s) return <p>Loading settings…</p>;
  const update = (key: string, value: any) => {
    setSaved(false);
    setS({ ...s, [key]: value });
  };
  return (
    <>
      <PageHeading
        eyebrow="MAKE YOURSELF AT HOME"
        title="Your studio, your pace."
        description="Choose what you want to learn. We will take care of the preparation."
      />
      <form
        className="settings-panel"
        onSubmit={async (e) => {
          e.preventDefault();
          try {
            await api("/settings", { method: "PUT", body: JSON.stringify(s) });
            setSaved(true);
          } catch (e) {
            onError((e as Error).message);
          }
        }}
      >
        <h2>A new idea each day</h2>
        <label className="check-label">
          <input
            type="checkbox"
            checked={s.discovery_enabled}
            onChange={(e) => update("discovery_enabled", e.target.checked)}
          />{" "}
          Find papers and make lessons automatically
        </label>
        <div className="form-grid">
          <label>
            Start time
            <input
              type="time"
              value={`${String(s.schedule_hour).padStart(2, "0")}:${String(s.schedule_minute).padStart(2, "0")}`}
              onChange={(e) => {
                const [h, m] = e.target.value.split(":").map(Number);
                setS({ ...s, schedule_hour: h, schedule_minute: m });
              }}
            />
          </label>
          <label>
            Time zone
            <input
              value={s.timezone}
              onChange={(e) => update("timezone", e.target.value)}
            />
          </label>
          <label>
            New lessons per day
            <input
              type="number"
              min={0}
              max={3}
              value={s.daily_limit}
              onChange={(e) => update("daily_limit", Number(e.target.value))}
            />
          </label>
        </div>
        <label>
          What interests you?
          <textarea
            rows={3}
            value={s.interests}
            onChange={(e) => update("interests", e.target.value)}
          />
        </label>
        <label>Areas to explore</label>
        <div className="category-options">
          {[
            ["cs.AI", "AI"],
            ["cs.LG", "Learning"],
            ["cs.CL", "Language"],
            ["cs.CV", "Vision"],
            ["cs.RO", "Robotics"],
            ["cs.SD", "Sound"],
            ["stat.ML", "Statistical learning"],
            ["cs.NE", "Neural systems"],
            ["cs.HC", "People & AI"],
          ].map(([id, title]) => (
            <label key={id}>
              <input
                type="checkbox"
                checked={s.categories.includes(id)}
                onChange={(e) =>
                  update(
                    "categories",
                    e.target.checked
                      ? [...s.categories, id]
                      : s.categories.filter((c: string) => c !== id),
                  )
                }
              />
              {title}
            </label>
          ))}
        </div>
        <h2>Local model</h2>
        <label>
          Paper reader
          <select
            value={s.model_profile}
            onChange={(e) => update("model_profile", e.target.value)}
          >
            <option value="qwen-q8">Qwen3.8 27B · Q8</option>
            <option value="qwen-q6">Qwen3.8 27B · Q6</option>
            <option value="muse-q6">Muse Glimmer 30B · Q6</option>
          </select>
        </label>
        <p className="subtle">
          Installed: {status?.models?.join(", ") || "Checking models…"}
        </p>
        <div className="actions">
          <button className="primary">
            Save settings <Check size={16} />
          </button>
          {saved && (
            <span className="saved">Saved. Make yourself comfortable.</span>
          )}
        </div>
      </form>
    </>
  );
}

function Learn({
  id,
  version,
  onError,
  refresh,
  onBack,
  reviewTarget,
  onRecording,
}: {
  id: string;
  reviewTarget: Row | null;
  onRecording: (v: boolean) => void;
  version: number;
  onError: (s: string) => void;
  refresh: () => void;
  onBack: () => void;
}) {
  const [lesson, setLesson] = useState<Row | null>(null),
    [chapterId, setChapterId] = useState(""),
    [index, setIndex] = useState(0),
    [role, setRole] = useState("both"),
    [speed, setSpeed] = useState(0.9),
    [subtitles, setSubtitles] = useState(true),
    [showJapanese, setShowJapanese] = useState(false),
    [playing, setPlaying] = useState(false),
    [loop, setLoop] = useState(false),
    [mode, setMode] = useState<"one" | "chapter" | "all">("one");
  const [source, setSource] = useState<Row | null>(null),
    [attempt, setAttempt] = useState<Row | null>(null),
    [recording, setRecording] = useState(false),
    [seconds, setSeconds] = useState(0),
    [sending, setSending] = useState(false),
    [requestingMic, setRequestingMic] = useState(false),
    [pending, setPending] = useState<any>(null),
    [question, setQuestion] = useState<any>(null),
    [hint, setHint] = useState(0);
  const selfAudio = useRef<HTMLAudioElement | null>(null);
  useEffect(() => {
    const busy = recording || requestingMic || sending;
    onRecording(busy);
    const leave = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    if (busy) window.addEventListener("beforeunload", leave);
    return () => {
      window.removeEventListener("beforeunload", leave);
      onRecording(false);
    };
  }, [recording, requestingMic, sending]);
  const audio = useRef<HTMLAudioElement>(null),
    recorder = useRef<MediaRecorder | null>(null),
    timer = useRef<ReturnType<typeof setInterval> | null>(null),
    stream = useRef<MediaStream | null>(null),
    initialized = useRef("");
  const load = () =>
    api<Row>(`/lessons/${id}`).then((l) => {
      setLesson(l);
      if (initialized.current !== id) {
        initialized.current = id;
        setChapterId(
          reviewTarget?.chapter_id ||
            l.progress?.chapter_id ||
            l.chapters[0]?.id ||
            "",
        );
        const rc = l.chapters.find(
          (c: Row) => c.id === reviewTarget?.chapter_id,
        );
        setIndex(
          reviewTarget
            ? Math.max(
                0,
                rc?.data.turns.findIndex(
                  (t: any) => t.id === reviewTarget.turn_id,
                ) ?? 0,
              )
            : l.progress?.turn_index || 0,
        );
        if (reviewTarget?.data.question_id)
          setQuestion(
            rc?.data.questions.find(
              (q: any) => q.id === reviewTarget.data.question_id,
            ),
          );
        setRole(l.progress?.role || "both");
        setSpeed(l.progress?.speed || 0.9);
        setSubtitles(l.progress?.subtitles ?? true);
        setAttempt(null);
      } else if (!chapterId && l.chapters.length)
        setChapterId(l.chapters[0].id);
    });
  useEffect(() => {
    load().catch((e) => onError(e.message));
  }, [id, version]);
  useEffect(() => {
    pendingRecording("get")
      .then(setPending)
      .catch(() => {});
    return () => {
      selfAudio.current?.pause();
      recorder.current?.state === "recording" && recorder.current.stop();
      stream.current?.getTracks().forEach((t) => t.stop());
      if (timer.current) clearInterval(timer.current);
    };
  }, []);
  useEffect(() => {
    if (attempt?.state === "pending") {
      const t = setInterval(
        () =>
          api<Row>(`/attempts/${attempt.id}`)
            .then((a) => {
              setAttempt(a);
              if (a.state === "ready" || a.state === "unscored") {
                selfAudio.current?.pause();
                selfAudio.current = new Audio(
                  fileUrl(a.data.wav || a.data.audio),
                );
                selfAudio.current.play().catch(() => {});
                refresh();
              }
            })
            .catch((e) => onError(e.message)),
        2000,
      );
      return () => clearInterval(t);
    }
  }, [attempt?.id, attempt?.state]);
  const chapter = lesson?.chapters.find((c: Row) => c.id === chapterId) as
    | Row
    | undefined;
  const turns = chapter?.data.turns || [];
  const turn = turns[index];
  const ready = chapter?.state === "ready";
  const japanese = (key: string, english?: string) => {
    const item = chapter?.data.translation?.items?.[key];
    return item?.english === english ? item.japanese as string : null;
  };
  const requestJapanese = () => {
    if (showJapanese) {
      setShowJapanese(false);
      return;
    }
    setShowJapanese(true);
    post(`/chapters/${chapterId}/translation`)
      .then(refresh)
      .catch((e) => onError(e.message));
  };
  useEffect(() => {
    if (chapterId && lesson) {
      api(`/lessons/${id}/progress`, {
        method: "PUT",
        body: JSON.stringify({
          chapter_id: chapterId,
          turn_index: index,
          role,
          speed,
          subtitles,
        }),
      }).catch(() => {});
    }
  }, [chapterId, index, role, speed, subtitles]);
  useEffect(() => {
    if (audio.current) audio.current.playbackRate = speed;
  }, [speed]);
  useEffect(() => {
    if (playing && audio.current && turn?.audio_verified) {
      audio.current.playbackRate = speed;
      audio.current.play().catch((e) => {
        setPlaying(false);
        onError(e.message);
      });
    }
  }, [index, chapterId, playing]);
  const stop = () => {
    selfAudio.current?.pause();
    audio.current?.pause();
    setPlaying(false);
  };
  const move = (i: number) => {
    stop();
    setIndex(Math.max(0, Math.min(turns.length - 1, i)));
    setAttempt(null);
    setQuestion(null);
  };
  const play = (m: "one" | "chapter" | "all") => {
    if (!turn?.audio_verified) return;
    setMode(m);
    setPlaying(true);
    if (audio.current) {
      audio.current.playbackRate = speed;
      audio.current.play().catch((e) => {
        setPlaying(false);
        onError(e.message);
      });
    }
  };
  const ended = () => {
    if (loop) {
      if (audio.current) {
        audio.current.currentTime = 0;
        audio.current.play().catch(() => {});
      }
      return;
    }
    if (mode !== "one" && index < turns.length - 1) {
      setIndex(index + 1);
      return;
    }
    if (mode === "all") {
      const i = lesson?.chapters.findIndex((c: Row) => c.id === chapterId);
      const next = lesson?.chapters[(i ?? -1) + 1];
      if (next?.state === "ready") {
        setChapterId(next.id);
        setIndex(0);
        return;
      }
    }
    setPlaying(false);
  };
  const send = async (saved: any) => {
    setSending(true);
    try {
      const form = new FormData();
      form.append("file", saved.blob, "practice.webm");
      form.append("chapter_id", saved.chapter_id);
      if (saved.client_id) form.append("client_id", saved.client_id);
      if (saved.turn_id) form.append("turn_id", saved.turn_id);
      if (saved.question_id) form.append("question_id", saved.question_id);
      const result = await api("/attempts", { method: "POST", body: form });
      setAttempt(await api(`/attempts/${result.attempt_id}`));
      await pendingRecording("delete");
      setPending(null);
      refresh();
    } catch (e) {
      setPending(saved);
      onError(
        "Your recording is saved in this browser. " + (e as Error).message,
      );
    } finally {
      setSending(false);
    }
  };
  const startRecording = async () => {
    if (requestingMic || recording || sending) return;
    if (pending) {
      onError("Please send or remove your saved recording first.");
      return;
    }
    stop();
    if (!navigator.mediaDevices?.getUserMedia) {
      onError(
        "Open the secure HTTPS address and trust the studio certificate to use your microphone.",
      );
      return;
    }
    setRequestingMic(true);
    try {
      const s = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      stream.current = s;
      const type = ["audio/webm;codecs=opus", "audio/mp4"].find((t) =>
        MediaRecorder.isTypeSupported(t),
      );
      const r = new MediaRecorder(s, type ? { mimeType: type } : undefined);
      recorder.current = r;
      const chunks: BlobPart[] = [];
      const target = {
        client_id: crypto.randomUUID(),
        chapter_id: chapterId,
        turn_id: question ? undefined : turn.id,
        question_id: question?.id,
      };
      r.ondataavailable = (e) => {
        if (e.data.size) chunks.push(e.data);
      };
      r.onstop = async () => {
        if (timer.current) clearInterval(timer.current);
        s.getTracks().forEach((t) => t.stop());
        setRecording(false);
        const saved = {
          ...target,
          blob: new Blob(chunks, { type: r.mimeType }),
          created: Date.now(),
        };
        setPending(saved);
        try {
          await pendingRecording("put", saved);
        } catch {
          onError(
            "Browser storage is full. Keep this page open until the recording is saved.",
          );
        }
        await send(saved);
      };
      r.start(200);
      setSeconds(0);
      setRecording(true);
      setAttempt(null);
      const start = Date.now();
      timer.current = setInterval(() => {
        setSeconds(Math.floor((Date.now() - start) / 1000));
        if (Date.now() - start >= 89000 && r.state === "recording") r.stop();
      }, 250);
    } catch (e) {
      stream.current?.getTracks().forEach((t) => t.stop());
      onError((e as Error).message);
    } finally {
      setRequestingMic(false);
    }
  };
  if (!lesson) return <p>Opening your lesson…</p>;
  const latest = (lesson.attempts as Row[]).filter((a) =>
    question
      ? a.data.question_id === question.id
      : a.turn_id === turn?.id && a.kind === "read",
  );
  return (
    <div className="learn">
      <button
        className="text-button back"
        disabled={recording}
        onClick={() => {
          stop();
          onBack();
        }}
      >
        <ChevronLeft size={17} /> Back to my library
      </button>
      <div className="lesson-heading">
        <div className="eyebrow">LISTEN · UNDERSTAND · SPEAK</div>
        <h1>{lesson.data.title}</h1>
        <div className="actions">
          <Badge state={lesson.state}>
            {lesson.state === "ready"
              ? "Your lesson is ready"
              : ['failed','paused','cancelled'].includes(lesson.job?.state)?'Preparation needs your attention':"New chapters are on their way"}
          </Badge>
          <a
            target="_blank"
            rel="noreferrer"
            href={
              lesson.paper.data.url ||
              (lesson.paper.data.pdf_path
                ? fileUrl(lesson.paper.data.pdf_path)
                : "#")
            }
            className="text-button"
          >
            Open paper <ExternalLink size={14} />
          </a>
          {lesson.state === "ready" && (
            <button
              className="text-button"
              onClick={() =>
                post(`/papers/${lesson.paper_id}/lessons`)
                  .then(() => {
                    refresh();
                    onBack();
                  })
                  .catch((e) => onError(e.message))
              }
            >
              Make a new version <RefreshCw size={14} />
            </button>
          )}
        </div>
      </div>
      {['failed','paused','cancelled'].includes(lesson.job?.state)&&<div className="notice"><p>{lesson.job.error||'Preparation is stopped. Your finished chapters and recordings are kept.'}</p><button className="secondary" onClick={()=>post(`/jobs/${lesson.job.id}/retry`).then(refresh).catch(e=>onError(e.message))}>Resume preparation</button></div>}
      {!lesson.chapters.length ? (
        <Empty
          icon={Headphones}
          title="First, we read with care."
          text="Your studio is working through the paper. The first chapter will appear here when it is ready."
        />
      ) : (
        <div className="study-layout">
          <aside className="chapters">
            <div className="eyebrow">YOUR LEARNING PATH</div>
            {lesson.chapters.map((c: Row, i: number) => (
              <button
                disabled={recording}
                key={c.id}
                className={c.id === chapterId ? "current" : ""}
                onClick={() => {
                  stop();
                  setChapterId(c.id);
                  setShowJapanese(false);
                  setIndex(0);
                  setAttempt(null);
                  setQuestion(null);
                }}
              >
                <span>{String(i + 1).padStart(2, "0")}</span>
                <div>
                  {c.data.title}
                  <small>
                    {c.state === "ready"
                      ? `${c.data.turns.length} sentences · ${Math.round(c.data.turns.reduce((n: number, t: any) => n + (t.duration || 0), 0) / 60)} min`
                      : ({draft:"Writing the talk",review:"Checking the ideas",revise:"Improving the talk",english:"Making the English clear",questions:"Adding questions",audio:"Making the voices",audio_review:"Checking the voices"} as Record<string,string>)[c.state] || "Getting ready"}
                  </small>
                </div>
                {c.state === "ready" && <Check size={13} />}
              </button>
            ))}
          </aside>
          <section className="study-main">
            <div className="study-title">
              <h2>{chapter?.data.title}</h2>
              <p>{chapter?.data.focus}</p>
              {ready && (
                <button className="secondary" onClick={requestJapanese}>
                  {showJapanese ? "日本語訳を隠す" : "日本語訳を表示"}
                </button>
              )}
              {showJapanese && (
                <>
                  {japanese("title", chapter?.data.title) && <p className="japanese-aid" lang="ja">{japanese("title", chapter?.data.title)}</p>}
                  {japanese("focus", chapter?.data.focus) && <p className="japanese-aid" lang="ja">{japanese("focus", chapter?.data.focus)}</p>}
                  {chapter?.translation_job && ["queued", "running", "paused"].includes(chapter.translation_job.state) && (
                    <p className="subtle">日本語訳を準備しています。完成した文から表示します。</p>
                  )}
                  {chapter?.translation_job?.state === "failed" && (
                    <button className="text-button" onClick={() => post(`/jobs/${chapter.translation_job.id}/retry`).then(refresh).catch((e) => onError(e.message))}>
                      日本語訳を再試行
                    </button>
                  )}
                </>
              )}
            </div>
            {!ready ? (
              <div className="preparing">
                <div className="tiny-wave">
                  <i />
                  <i />
                  <i />
                  <i />
                  <i />
                </div>
                <h3>We are getting this chapter ready.</h3>
                <p>
                  We check the explanation and the voices before you practice.
                </p>
              </div>
            ) : (
              <>
                <div className="player-tools">
                  <button
                    className="secondary"
                    disabled={recording}
                    onClick={() => (playing ? stop() : play("chapter"))}
                  >
                    {playing ? <Pause size={16} /> : <Play size={16} />}{" "}
                    {playing ? "Pause" : "Listen to chapter"}
                  </button>
                  <button
                    className="text-button"
                    disabled={recording}
                    onClick={() => play("all")}
                  >
                    Play all
                  </button>
                  <label className="inline-label">
                    Speed
                    <select
                      value={speed}
                      onChange={(e) => setSpeed(Number(e.target.value))}
                    >
                      {[0.65, 0.8, 0.9, 1, 1.1, 1.25].map((s) => (
                        <option key={s} value={s}>
                          {s}×
                        </option>
                      ))}
                    </select>
                  </label>
                  <button
                    className={`icon-button ${loop ? "active" : ""}`}
                    aria-label="Repeat sentence"
                    aria-pressed={loop}
                    onClick={() => setLoop(!loop)}
                  >
                    <RotateCcw size={17} />
                  </button>
                </div>
                <div className="sentence-card">
                  <div className="sentence-top">
                    <span className="speaker">
                      <span className={`avatar ${turn?.speaker}`}>
                        {turn?.speaker === "host" ? "A" : "R"}
                      </span>
                      {turn?.speaker === "host"
                        ? "Aiden · the curious host"
                        : "Ryan · your guide"}
                    </span>
                    <span>
                      {index + 1} / {turns.length}
                    </span>
                  </div>
                  <div className="spoken-sentence">
                    {subtitles ? (
                      turn?.text
                    ) : (
                      <span className="subtle">
                        Listen first. You can show the words below.
                      </span>
                    )}
                  </div>
                  {showJapanese && subtitles && japanese(`turn:${turn?.id}`, turn?.text) && (
                    <p className="japanese-aid sentence-translation" lang="ja">
                      {japanese(`turn:${turn?.id}`, turn?.text)}
                    </p>
                  )}
                  <div className="sentence-bottom">
                    <button
                      className="text-button"
                      onClick={() => setSubtitles(!subtitles)}
                    >
                      {subtitles ? "Hide words" : "Show words"}
                    </button>
                    <div>
                      {turn?.source_ids?.map((s: string, i: number) => (
                        <button
                          key={s}
                          className="source-chip"
                          onClick={() =>
                            api<Row>(`/sources/${encodeURIComponent(s)}`)
                              .then(setSource)
                              .catch((e) => onError(e.message))
                          }
                        >
                          <FileText size={12} /> Source {i + 1}
                        </button>
                      ))}
                      {turn?.kind !== "paper" && (
                        <span className="subtle">
                          {turn?.kind === "example"
                            ? "An example to help you understand"
                            : turn?.kind === "background"
                              ? "Background idea"
                              : ""}
                        </span>
                      )}
                    </div>
                  </div>
                  <audio
                    ref={audio}
                    src={turn?.audio ? fileUrl(turn.audio) : undefined}
                    onEnded={ended}
                    onPause={() => {}}
                    preload="auto"
                  />
                </div>
                <div className="practice-controls">
                  <button
                    className="icon-button"
                    disabled={recording || index === 0}
                    onClick={() => move(index - 1)}
                    aria-label="Previous sentence"
                  >
                    <ChevronLeft />
                  </button>
                  <button
                    className="secondary"
                    disabled={recording}
                    onClick={() => play("one")}
                  >
                    <Volume2 size={17} /> Hear it
                  </button>
                  <button
                    className={`record-button ${recording ? "recording" : ""}`}
                    disabled={
                      requestingMic || sending || (!recording && !!pending)
                    }
                    onClick={() =>
                      recording ? recorder.current?.stop() : startRecording()
                    }
                  >
                    {recording ? <Square size={16} /> : <Mic size={18} />}{" "}
                    {recording
                      ? `Done · ${minutes(seconds)}`
                      : sending
                        ? "Saving…"
                        : question
                          ? "Answer the question"
                          : "Your turn"}
                  </button>
                  <button
                    className="icon-button"
                    disabled={recording || index >= turns.length - 1}
                    onClick={() => {
                      let n = index + 1;
                      while (
                        n < turns.length - 1 &&
                        role !== "both" &&
                        turns[n].speaker !== role
                      )
                        n++;
                      move(n);
                    }}
                    aria-label="Next sentence"
                  >
                    <ChevronRight />
                  </button>
                </div>
                <div className="practice-caption">
                  <span>Listen. Say it. Listen to yourself.</span>
                  <label>
                    Practice as{" "}
                    <select
                      value={role}
                      disabled={recording}
                      onChange={(e) => setRole(e.target.value)}
                    >
                      <option value="both">Both voices</option>
                      <option value="host">Aiden</option>
                      <option value="guide">Ryan</option>
                    </select>
                  </label>
                </div>
                {pending && !sending && (
                  <div className="notice">
                    <p>A recording is saved in this browser.</p>
                    <button className="secondary" onClick={() => send(pending)}>
                      Send it now
                    </button>
                    <button
                      className="text-button"
                      onClick={() => {
                        pendingRecording("delete");
                        setPending(null);
                      }}
                    >
                      Remove
                    </button>
                  </div>
                )}
                {attempt && (
                  <Assessment
                    attempt={attempt}
                    onRetry={() => startRecording()}
                  />
                )}
                {lesson.data.glossary?.length > 0 && (
                  <details className="transcript">
                    <summary>Words for this paper</summary>
                    <dl>
                      {lesson.data.glossary.map((g: any) => (
                        <React.Fragment key={g.term}>
                          <dt>
                            <strong>{g.term}</strong>
                          </dt>
                          <dd>{g.meaning}</dd>
                          {showJapanese && japanese(`glossary:${g.term}`, g.meaning) && (
                            <dd className="japanese-aid" lang="ja">{japanese(`glossary:${g.term}`, g.meaning)}</dd>
                          )}
                        </React.Fragment>
                      ))}
                    </dl>
                  </details>
                )}
                <section className="understanding">
                  <div className="eyebrow">
                    <CircleHelp size={14} /> MAKE SURE IT MAKES SENSE
                  </div>
                  <h3>Tell the idea in your own words.</h3>
                  {chapter?.data.questions?.map((q: any, i: number) => (
                    <button
                      key={q.id}
                      disabled={recording}
                      className={`question ${question?.id === q.id ? "chosen" : ""}`}
                      onClick={() => {
                        setQuestion(q);
                        setHint(0);
                        setAttempt(null);
                      }}
                    >
                      <span>{i + 1}</span>
                      <span className="question-copy">
                        {q.question}
                        {showJapanese && japanese(`question:${q.id}`, q.question) && (
                          <small className="japanese-aid" lang="ja">{japanese(`question:${q.id}`, q.question)}</small>
                        )}
                      </span>
                      <Mic size={15} />
                    </button>
                  ))}
                  {question && (
                    <div className="question-help">
                      <p>
                        Press “Answer the question” above. A short answer is
                        enough.
                      </p>
                      {question.hints
                        ?.slice(0, hint)
                        .map((h: string, i: number) => (
                          <p key={i} className="hint">
                            {h}
                            {showJapanese && japanese(`hint:${question.id}:${i}`, h) && (
                              <span className="japanese-aid" lang="ja">{japanese(`hint:${question.id}:${i}`, h)}</span>
                            )}
                          </p>
                        ))}
                      <div className="actions">
                        <button
                          className="text-button"
                          disabled={hint >= question.hints.length}
                          onClick={() => setHint(hint + 1)}
                        >
                          A little help
                        </button>
                        <details>
                          <summary>See a sample answer</summary>
                          <p>{question.sample_answer}</p>
                          {showJapanese && japanese(`answer:${question.id}`, question.sample_answer) && (
                            <p className="japanese-aid" lang="ja">{japanese(`answer:${question.id}`, question.sample_answer)}</p>
                          )}
                        </details>
                        <button
                          className="text-button"
                          disabled={recording}
                          onClick={() => setQuestion(null)}
                        >
                          Back to reading
                        </button>
                      </div>
                    </div>
                  )}
                </section>
                <details className="transcript">
                  <summary>Read the conversation</summary>
                  {turns.map((t: any, i: number) => (
                    <button
                      key={t.id}
                      disabled={recording}
                      className={i === index ? "current" : ""}
                      onClick={() => move(i)}
                    >
                      <b>{t.speaker === "host" ? "A" : "R"}</b>
                      <span>
                        {t.text}
                        {showJapanese && japanese(`turn:${t.id}`, t.text) && (
                          <small className="japanese-aid" lang="ja">{japanese(`turn:${t.id}`, t.text)}</small>
                        )}
                      </span>
                    </button>
                  ))}
                </details>
                {latest.length > 0 && (
                  <details className="history">
                    <summary>Your earlier recordings ({latest.length})</summary>
                    {latest.map((a) => (
                      <div key={a.id}>
                        <span>
                          {date(a.created)} ·{" "}
                          {a.data.word_match != null
                            ? `${a.data.word_match}% words matched`
                            : a.state}
                        </span>
                        <audio
                          controls
                          src={fileUrl(a.data.wav || a.data.audio)}
                        />
                        <button
                          className="text-button"
                          onClick={() => setAttempt(a)}
                        >
                          See feedback
                        </button>
                        <button
                          className="text-button"
                          onClick={() =>
                            api(`/attempts/${a.id}`, { method: "DELETE" })
                              .then(refresh)
                              .catch((e) => onError(e.message))
                          }
                        >
                          Delete
                        </button>
                      </div>
                    ))}
                  </details>
                )}
              </>
            )}
          </section>
        </div>
      )}
      {source && <SourceDrawer source={source} close={() => setSource(null)} />}
    </div>
  );
}
function SourceDrawer({ source, close }: { source: Row; close: () => void }) {
  return (
    <div className="drawer-backdrop" onClick={() => close()}>
      <aside className="source-drawer" onClick={(e) => e.stopPropagation()}>
        <button
          className="modal-close"
          aria-label="Close source"
          onClick={() => close()}
        >
          <X />
        </button>
        <div className="eyebrow">CHECK THE ORIGINAL</div>
        <h2>{source.data.label}</h2>
        {source.data.image_path && (
          <img
            src={fileUrl(source.data.image_path)}
            alt={`Original paper ${source.data.label}`}
          />
        )}
        <p className="source-text">{source.data.text}</p>
        {source.data.url && (
          <a
            className="text-button"
            href={source.data.url}
            target="_blank"
            rel="noreferrer"
          >
            Open original source <ExternalLink size={15} />
          </a>
        )}
      </aside>
    </div>
  );
}
function Assessment({
  attempt,
  onRetry,
}: {
  attempt: Row;
  onRetry: () => void;
}) {
  const a = attempt.data;
  return (
    <section className="assessment">
      <div className="eyebrow">YOUR VOICE, A LITTLE CLEARER</div>
      {attempt.state === "pending" ? (
        <>
          <h3>Listening carefully…</h3>
          <p>Your recording is saved. You can stay here while we check it.</p>
        </>
      ) : ["failed","cancelled"].includes(attempt.state) ? (
        <>
          <h3>Your recording is safe.</h3>
          <p>The check did not finish. Open “In the making” to try again.</p>
        </>
      ) : (
        <>
          <h3>
            {attempt.state === "unscored"
              ? "Let’s try that again."
              : "Nice work. Now listen back."}
          </h3>
          <audio controls src={fileUrl(a.wav || a.audio)} />
          {a.transcript && (
            <p className="heard">
              <small>WE HEARD</small>
              {a.transcript}
            </p>
          )}
          {a.word_match != null && (
            <>
              <div className="score-row">
                <div>
                  <strong>
                    {a.word_match}
                    <span>%</span>
                  </strong>
                  <small>Words matched</small>
                </div>
                <div>
                  <strong>{a.pace_wpm}</strong>
                  <small>Words per minute</small>
                </div>
                <div>
                  <strong>{a.pauses?.length || 0}</strong>
                  <small>Longer pauses</small>
                </div>
              </div>
              <div className="word-diff">
                {a.words?.map((w: any, i: number) => (
                  <span
                    key={i}
                    className={w.kind}
                    title={
                      w.kind === "replace" ? `We heard: ${w.heard}` : w.kind
                    }
                  >
                    {w.expected || w.heard}
                    {w.kind === "missing" && " ·"}
                  </span>
                ))}
              </div>
              <p className="subtle">
                Word match shows the words we heard. Sound and rhythm feedback
                are below.
              </p>
            </>
          )}
          {a.pronunciation && (
            <details>
              <summary>Compare the sounds</summary>
              <p>{a.pronunciation.message}</p>
              <div className="phone-comparison">
                <div>
                  <small>THE EXAMPLE</small>
                  <p>{a.pronunciation.reference.ipa}</p>
                </div>
                <div>
                  <small>YOUR SOUNDS</small>
                  <p>{a.pronunciation.learner.ipa}</p>
                </div>
              </div>
              {a.pronunciation.differences?.map((d: any, i: number) => (
                <div className="sound-difference" key={i}>
                  <code>
                    {d.expected || "—"} → {d.heard || "—"}
                  </code>
                  <Badge>
                    {d.status === "uncertain" ? "Not sure" : "Listen & compare"}
                  </Badge>
                  <ClipButton
                    path={a.reference_audio}
                    start={d.reference_start}
                    end={d.reference_end}
                    label="Example"
                  />
                  <ClipButton
                    path={a.wav}
                    start={d.start}
                    end={d.end}
                    label="Your voice"
                  />
                  {d.practice && (
                    <p>
                      {d.practice.tip} Try:{" "}
                      <b>{d.practice.words.join(" · ")}</b>
                    </p>
                  )}
                </div>
              ))}
            </details>
          )}
          {a.word_stress?.length > 0 && (
            <details>
              <summary>Stress inside a word</summary>
              <p>
                In the sound guide, 1 marks the main stress and 2 a lighter
                stress.
              </p>
              {a.word_stress.map((w: any, i: number) => (
                <p key={i}>
                  <b>{w.word}</b> <code>{w.pronunciations[0].join(" ")}</code>
                </p>
              ))}
            </details>
          )}
          {a.syllable_energy && (
            <details>
              <summary>Vowel peaks inside words</summary>
              <p>{a.syllable_energy.message}</p>
              {["reference", "learner"].map((side) => (
                <div key={side}>
                  <small>
                    {side === "reference" ? "THE EXAMPLE" : "YOUR VOICE"}
                  </small>
                  {a.syllable_energy[side].map((w: any, i: number) => (
                    <p key={i}>
                      <b>{w.word}</b>{" "}
                      {w.vowels.map((v: any, j: number) => (
                        <span
                          key={j}
                          className={v.relative_energy > 0.8 ? "stressed" : ""}
                        >
                          /{v.phone}/ {Math.round(v.relative_energy * 100)}
                          %{" "}
                        </span>
                      ))}
                    </p>
                  ))}
                </div>
              ))}
            </details>
          )}
          {a.stress && (
            <details>
              <summary>Rhythm and emphasis</summary>
              <p>
                Different emphasis can change the meaning. Compare it with the
                example.
              </p>
              <small>THE EXAMPLE</small>
              <p>
                {a.reference_stress?.words?.map((w: any, i: number) => (
                  <span key={i} className={w.emphasized ? "stressed" : ""}>
                    {w.word}{" "}
                  </span>
                ))}
              </p>
              <small>YOUR VOICE</small>
              <p>
                {a.stress.words?.map((w: any, i: number) => (
                  <span key={i} className={w.emphasized ? "stressed" : ""}>
                    {w.word}{" "}
                  </span>
                ))}
              </p>
              {a.stress.message && <p>{a.stress.message}</p>}
            </details>
          )}
          {a.comprehension && (
            <div className="comprehension">
              <h4>The idea</h4>
              <p>{a.comprehension.content_feedback}</p>
              <h4>Your English</h4>
              <p>{a.comprehension.english_feedback}</p>
              <blockquote>{a.comprehension.better_answer}</blockquote>
              <p>{a.comprehension.next_step}</p>
            </div>
          )}
          {a.feedback && <p>{a.feedback}</p>}
          <button className="secondary" onClick={onRetry}>
            <Mic size={16} /> Try it once more
          </button>
        </>
      )}
    </section>
  );
}
function ClipButton({
  path,
  start,
  end,
  label,
}: {
  path: string;
  start: number | null;
  end: number | null;
  label: string;
}) {
  const player = useRef<HTMLAudioElement | null>(null);
  useEffect(
    () => () => {
      player.current?.pause();
    },
    [],
  );
  return (
    <button
      className="text-button"
      disabled={start == null || end == null}
      onClick={() => {
        player.current?.pause();
        const p = new Audio(fileUrl(path));
        player.current = p;
        p.currentTime = Math.max(0, (start || 0) - 0.18);
        p.ontimeupdate = () => {
          if (p.currentTime >= (end || 0) + 0.18) p.pause();
        };
        p.play().catch(() => {});
      }}
    >
      <Volume2 size={13} />
      {label}
    </button>
  );
}
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
