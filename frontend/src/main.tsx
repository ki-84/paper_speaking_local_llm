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
import { VisualPanel } from "./VisualPanel";

type Page = "library" | "discover" | "review" | "jobs" | "settings" | "learn";
function microphoneError(error: unknown, selectedMic = "") {
  const reason = error instanceof Error ? error.name : "";
  if (reason === "NotAllowedError" || reason === "PermissionDeniedError")
    return "Microphone access is blocked. Allow it in Chrome's site settings and Mac Settings → Privacy & Security → Microphone.";
  if (reason === "NotFoundError" || reason === "OverconstrainedError")
    return selectedMic
      ? "The selected microphone is unavailable. Choose System default or another microphone."
      : "Chrome found no usable system microphone. Check Mac Settings → Sound → Input, then click Refresh microphones here.";
  if (reason === "NotReadableError" || reason === "TrackStartError")
    return "Chrome cannot read this microphone. Close other apps using it, or choose another microphone.";
  return error instanceof Error ? error.message : "Chrome could not start the microphone.";
}
const tabs: [Page, string, typeof BookOpen][] = [
  ["library", "My library", BookOpen],
  ["discover", "論文を探す", Compass],
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

function suggestedVideoTitle(paperTitle: string, chapterNumber?: number) {
  const suffix = chapterNumber === undefined ? " — 全章" : ` — 第${chapterNumber}章`;
  const title = paperTitle.replace(/\s+/g, " ").trim() || "Paper lesson";
  const limit = 100 - Array.from(suffix).length;
  const characters = Array.from(title);
  return (characters.length > limit
    ? characters.slice(0, limit - 1).join("").trimEnd() + "…"
    : title) + suffix;
}

function VideoTitleSuggestion({ title, onError }: { title: string; onError: (message: string) => void }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => setCopied(false), [title]);
  return <div className="video-title-suggestion">
    <label>推奨タイトル（YouTube用）<textarea value={title} rows={2} readOnly onFocus={(event) => event.currentTarget.select()} /></label>
    <button className="secondary" onClick={async () => {
      try {
        await navigator.clipboard.writeText(title);
        setCopied(true);
      } catch {
        onError("Could not copy the title. Select the title and copy it manually.");
      }
    }}>{copied ? "コピーしました" : "タイトルをコピー"}</button>
  </div>;
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
  const [searches, setSearches] = useState<any[]>([]),
    [searchQuery, setSearchQuery] = useState("");
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
      api<any[]>("/paper-searches"),
    ])
      .then(([l, p, j, r, v, s]) => {
        setLessons(l);
        setPapers(p);
        setJobs(j);
        setRecs(r);
        setReviews(v);
        setSearches(s);
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
  const createLesson = async (paperId: string) => {
    try {
      const result = await post(`/papers/${paperId}/lessons`);
      refresh();
      openLesson(result.lesson_id);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const busy = jobs.filter((j) => ["queued", "running"].includes(j.state));
  const latestSearch = searches[0];
  const searchRunning = latestSearch && ["queued", "running", "paused"].includes(latestSearch.state);
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
            <StoryStudio papers={papers} version={version} onError={setError} refresh={refresh} openLesson={openLesson} />
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
                    data-lesson-id={l.id}
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
                    <p className="subtle">{l.data.format === "paper-story-1" ? "Film & C1 English practice · 動画と英語練習" : l.data.format === "paper-visual-2" ? "Visual lesson · 図付き教材" : "Earlier edition · 旧版"}</p>
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
                          onClick={() => createLesson(p.id)}
                        >
                          Make a lesson + MP4 · 教材と動画を作る
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
              eyebrow="読みたいテーマから探す"
              title="次に読みたい論文を探す。"
              description="日本語で興味を入力すると、関連するAI論文を日本語の題名と要約で選べます。"
            />
            <section className="prompt-search">
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  if (searchQuery.trim()) act(() => post("/paper-searches", { query: searchQuery.trim() }));
                }}
              >
                <label htmlFor="paper-search-query">どんな論文を読みたいですか？</label>
                <textarea
                  id="paper-search-query"
                  value={searchQuery}
                  onChange={(event) => setSearchQuery(event.target.value)}
                  maxLength={600}
                  rows={3}
                  placeholder="例：ロボットが初めて見る物をつかむための学習方法を知りたい"
                />
                <div className="actions">
                  <button className="primary" type="submit" disabled={!!searchRunning || searchQuery.trim().length < 3}>
                    <Compass size={16} /> 日本語で論文を探す
                  </button>
                  <small>検索語の作成と日本語訳はローカルモデルで行います。論文情報はarXivから取得します。</small>
                </div>
              </form>
            </section>
            {latestSearch && (
              <section className="search-section">
                <h2>「{latestSearch.query}」の検索結果</h2>
                {searchRunning && (
                  <p className="search-progress">検索・日本語化を進めています。完成した候補から表示します。 {Math.round(100 * latestSearch.progress)}%</p>
                )}
                {latestSearch.state === "failed" && (
                  <p className="job-error">検索を完了できませんでした。{latestSearch.error}</p>
                )}
                {latestSearch.state === "completed" && !latestSearch.results.length && (
                  <p>該当する論文が見つかりませんでした。別の言葉でお試しください。</p>
                )}
                {latestSearch.results.map((paper: any) => (
                  <article className="recommendation" key={paper.paper_id}>
                    <div className="card-meta"><Badge>要旨から選定</Badge><span>{paper.published?.slice(0, 10)} · {paper.source_id}{paper.version}</span></div>
                    <h3>{paper.title_ja}</h3>
                    <p>{paper.summary_ja}</p>
                    <div className="learning-box"><strong>希望との関連</strong><p>{paper.fit_ja}</p></div>
                    <details>
                      <summary>原題・英語の要旨</summary>
                      <strong>{paper.title}</strong><p>{paper.abstract}</p>
                    </details>
                    <div className="actions">
                      <button className="primary" onClick={() => createLesson(paper.paper_id)}>
                        この論文で教材とMP4を作る <ArrowRight size={16} />
                      </button>
                      <a className="text-button" href={paper.url} target="_blank" rel="noreferrer">原論文を見る <ExternalLink size={14} /></a>
                    </div>
                  </article>
                ))}
              </section>
            )}
            <h2>本文を確認したおすすめ</h2>
            <div className="discovery-banner">
              <Compass size={24} />
              <p>
                こちらは論文の本文を読んで選んだ候補です。
                <small>
                  毎日の自動探索を、今すぐ実行することもできます。
                </small>
              </p>
              <button
                className="primary"
                onClick={() => act(() => post("/discover"))}
              >
                <RefreshCw size={16} /> 新しい論文を探す
              </button>
            </div>
            {recs.length ? (
              <div className="recommendations">
                {recs.map((r) => (
                  <article className="recommendation" key={r.id}>
                    <div className="card-meta">
                      <Badge state={r.state}>
                        {r.state === "selected" ? "教材化中" : r.state === "recommended" ? "おすすめ" : "見送り候補"}
                      </Badge>
                      <span>{r.day}</span>
                    </div>
                    <h3>{r.data.ja?.title || "日本語の題名を準備中…"}</h3>
                    <p>{r.data.ja?.summary || "要旨を日本語にしています。"}</p>
                    <div className="learning-box">
                      <strong>なぜ面白いか</strong>
                      <p>{r.data.ja?.why || "日本語の説明を準備中です。"}</p>
                      <strong>学べること</strong>
                      <p>{r.data.ja?.learn || "日本語の説明を準備中です。"}</p>
                    </div>
                    <details>
                      <summary>論文中の根拠を見る ({r.data.source_ids?.length || 0})</summary>
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
                          <FileText size={12} /> 根拠 {i + 1}
                        </button>
                      ))}
                      </div>
                    </details>
                    {r.data.ja?.cautions?.length > 0 && (
                      <details>
                        <summary>読むときの注意点</summary>
                        <ul>
                          {r.data.ja.cautions.map((c: string, i: number) => (
                            <li key={i}>{c}</li>
                          ))}
                        </ul>
                      </details>
                    )}
                    <details><summary>原題・英語の要旨</summary><strong>{r.title}</strong><p>{JSON.parse(r.paper_data).abstract}</p></details>
                    <div className="actions">
                      <button
                        className="primary"
                        disabled={!r.data.ja?.title}
                        onClick={() => createLesson(r.paper_id)}
                      >
                        この論文で教材とMP4を作る <ArrowRight size={16} />
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
                        <Bookmark size={16} /> 興味あり
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
                        興味なし
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
                        {r.feedback === "read" ? "既読" : "既読にする"}
                      </button>
                      <a
                        className="text-button"
                        target="_blank"
                        rel="noreferrer"
                        href={JSON.parse(r.paper_data).url}
                      >
                        原論文を見る <ExternalLink size={14} />
                      </a>
                    </div>
                  </article>
                ))}
              </div>
            ) : (
              <Empty
                icon={Compass}
                title="おすすめを準備しています。"
                text="上の入力欄から、読みたいテーマの論文を日本語で探せます。"
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
                            : j.kind === "paper_search"
                              ? "日本語の希望から論文を検索"
                            : j.kind === "recommendation_ja"
                              ? "推薦を日本語に翻訳"
                            : j.kind === "practice"
                              ? "Your recording"
                              : j.kind === "revoice"
                                ? "Refreshing Maya's voice"
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
            onOpenLesson={openLesson}
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
            {busy ? "Adding…" : "Make my lesson + MP4 · 教材と動画を作る"}
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
  onOpenLesson,
}: {
  id: string;
  reviewTarget: Row | null;
  onRecording: (v: boolean) => void;
  version: number;
  onError: (s: string) => void;
  refresh: () => void;
  onBack: () => void;
  onOpenLesson: (id: string) => void;
}) {
  const [lesson, setLesson] = useState<Row | null>(null),
    [chapterId, setChapterId] = useState(""),
    [index, setIndex] = useState(0),
    [role, setRole] = useState("both"),
    [speed, setSpeed] = useState(1),
    [subtitles, setSubtitles] = useState(true),
    [showJapanese, setShowJapanese] = useState(true),
    [playing, setPlaying] = useState(false),
    [loop, setLoop] = useState(false),
    [mode, setMode] = useState<"one" | "chapter" | "all">("one");
  const [source, setSource] = useState<Row | null>(null),
    [attempt, setAttempt] = useState<Row | null>(null),
    [recording, setRecording] = useState(false),
    [seconds, setSeconds] = useState(0),
    [sending, setSending] = useState(false),
    [requestingMic, setRequestingMic] = useState(false),
    [testingMic, setTestingMic] = useState(false),
    [micLevel, setMicLevel] = useState(0),
    [micDevices, setMicDevices] = useState<MediaDeviceInfo[]>([]),
    [selectedMic, setSelectedMic] = useState(() => localStorage.getItem("paperspeak-microphone") || ""),
    [activeMic, setActiveMic] = useState(""),
    [micMessage, setMicMessage] = useState(""),
    [pending, setPending] = useState<any>(null),
    [question, setQuestion] = useState<any>(null),
    [hint, setHint] = useState(0);
  const selfAudio = useRef<HTMLAudioElement | null>(null);
  const [visualMode, setVisualMode] = useState<"auto" | "pinned">("auto");
  const [visualKey, setVisualKey] = useState<string | null>(null);
  const selectVisual = (mode: "auto" | "pinned", key: string | null) => {
    setVisualMode(mode); setVisualKey(key);
  };
  useEffect(() => {
    const busy = recording || requestingMic || testingMic || sending;
    onRecording(busy);
    const leave = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    if (busy) window.addEventListener("beforeunload", leave);
    return () => {
      window.removeEventListener("beforeunload", leave);
      onRecording(false);
    };
  }, [recording, requestingMic, testingMic, sending]);
  const audio = useRef<HTMLAudioElement>(null),
    recorder = useRef<MediaRecorder | null>(null),
    timer = useRef<ReturnType<typeof setInterval> | null>(null),
    meterTimer = useRef<ReturnType<typeof setInterval> | null>(null),
    meterContext = useRef<AudioContext | null>(null),
    stream = useRef<MediaStream | null>(null),
    initialized = useRef("");
  const refreshMicDevices = async () => {
    const devices = await navigator.mediaDevices?.enumerateDevices();
    const inputs = (devices || []).filter((device) => device.kind === "audioinput" && device.deviceId && device.deviceId !== "default");
    setMicDevices(inputs);
    return inputs;
  };
  useEffect(() => {
    refreshMicDevices().catch(() => {});
    const changed = () => { refreshMicDevices().catch(() => {}); };
    navigator.mediaDevices?.addEventListener?.("devicechange", changed);
    return () => navigator.mediaDevices?.removeEventListener?.("devicechange", changed);
  }, []);
  const stopMeter = () => {
    if (meterTimer.current) clearInterval(meterTimer.current);
    meterTimer.current = null;
    meterContext.current?.close().catch(() => {});
    meterContext.current = null;
    setMicLevel(0);
  };
  const releaseMic = () => {
    stopMeter();
    stream.current?.getTracks().forEach((track) => track.stop());
    stream.current = null;
    setTestingMic(false);
  };
  const openMic = async () => {
    const audioOptions = {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    };
    let s: MediaStream;
    try {
      s = await navigator.mediaDevices.getUserMedia({
        audio: selectedMic ? { ...audioOptions, deviceId: { exact: selectedMic } } : audioOptions,
      });
      setMicMessage("");
    } catch (error) {
      const reason = error instanceof Error ? error.name : "";
      if (!selectedMic || !["NotFoundError", "OverconstrainedError"].includes(reason)) throw error;
      // Chrome may rotate device IDs after a permission or certificate change.
      localStorage.removeItem("paperspeak-microphone");
      setSelectedMic("");
      s = await navigator.mediaDevices.getUserMedia({ audio: audioOptions });
      setMicMessage("Your saved microphone was unavailable. Using the system default microphone.");
    }
    stream.current = s;
    const track = s.getAudioTracks()[0];
    if (!track) {
      s.getTracks().forEach((item) => item.stop());
      throw new Error("Chrome opened the microphone without an audio track. Choose another microphone.");
    }
    setActiveMic(track?.label || "Microphone");
    refreshMicDevices().catch(() => {});
    try {
      const context = new AudioContext();
      meterContext.current = context;
      const analyser = context.createAnalyser();
      analyser.fftSize = 2048;
      context.createMediaStreamSource(s).connect(analyser);
      await context.resume();
      const samples = new Float32Array(analyser.fftSize);
      meterTimer.current = setInterval(() => {
        analyser.getFloatTimeDomainData(samples);
        let power = 0;
        for (const sample of samples) power += sample * sample;
        setMicLevel(Math.min(100, Math.round(Math.sqrt(power / samples.length) * 800)));
      }, 100);
    } catch {
      // Recording still works when a browser cannot show a live level.
      stopMeter();
    }
    return s;
  };
  const testMic = async () => {
    if (testingMic) {
      releaseMic();
      return;
    }
    if (requestingMic || recording || sending) return;
    if (!navigator.mediaDevices?.getUserMedia) {
      const message = "Open the secure HTTPS address and trust the studio certificate to use your microphone.";
      setMicMessage(message);
      onError(message);
      return;
    }
    stop();
    setRequestingMic(true);
    setMicMessage("Opening microphone… Allow Chrome to use it if asked.");
    try {
      await openMic();
      setTestingMic(true);
    } catch (e) {
      releaseMic();
      refreshMicDevices().catch(() => {});
      setMicMessage(microphoneError(e, selectedMic));
      onError(microphoneError(e, selectedMic));
    } finally {
      setRequestingMic(false);
    }
  };
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
        setSpeed(l.progress?.speed === 0.9 ? 1 : (l.progress?.speed ?? 1));
        setSubtitles(l.progress?.subtitles ?? true);
        selectVisual(l.progress?.visual_mode || "auto", l.progress?.visual_key || null);
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
      if (meterTimer.current) clearInterval(meterTimer.current);
      meterContext.current?.close().catch(() => {});
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
  const earlierVisual = turns.slice(0, index).reverse().find((t: any) => t.visual)?.visual;
  const visualCue = turn?.visual || (earlierVisual ? {key: earlierVisual.key, focus: []} : null);
  const ready = chapter?.state === "ready";
  const chapterVideos = (lesson?.videos as Row[] || []).filter((v) => v.kind === "chapter" && v.chapter_id === chapterId);
  const currentVideo = chapterVideos.find((v) => v.state === "ready") || chapterVideos[0];
  const readyChapterVideo = (lesson?.videos as Row[] || []).find((v) => v.kind === "chapter" && v.state === "ready");
  const readyChapterNumber = readyChapterVideo ? (lesson?.chapters.findIndex((c: Row) => c.id === readyChapterVideo.chapter_id) ?? -1) + 1 : 0;
  const completeVideos = (lesson?.videos as Row[] || []).filter((v) => v.kind === "full");
  const completeVideo = completeVideos.find((v) => v.state === "ready") || completeVideos[0];
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
          visual_mode: visualMode,
          visual_key: visualKey,
        }),
      }).catch(() => {});
    }
  }, [chapterId, index, role, speed, subtitles, visualMode, visualKey]);
  const spokenRate = speed * (turn?.voice === "Ryan" ? 1.2 : 1);
  useEffect(() => {
    if (audio.current) {
      audio.current.preservesPitch = true;
      audio.current.playbackRate = spokenRate;
    }
  }, [spokenRate]);
  useEffect(() => {
    if (playing && audio.current && turn?.audio_verified) {
      audio.current.playbackRate = spokenRate;
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
      audio.current.playbackRate = spokenRate;
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
        selectVisual("auto", null);
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
    if (testingMic) releaseMic();
    if (pending) {
      onError("Please send or remove your saved recording first.");
      return;
    }
    stop();
    if (!navigator.mediaDevices?.getUserMedia) {
      const message = "Open the secure HTTPS address and trust the studio certificate to use your microphone.";
      setMicMessage(message);
      onError(message);
      return;
    }
    setRequestingMic(true);
    setMicMessage("Opening microphone… Allow Chrome to use it if asked.");
    try {
      const s = await openMic();
      const type = ["audio/webm;codecs=opus", "audio/mp4"].find((t) =>
        MediaRecorder.isTypeSupported(t),
      );
      const r = new MediaRecorder(s, type ? { mimeType: type } : undefined);
      recorder.current = r;
      const chunks: BlobPart[] = [];
      let recorderFailed = false;
      r.addEventListener("error", () => {
        recorderFailed = true;
        setMicMessage("Recording stopped because Chrome lost the microphone. Try another microphone.");
        if (r.state === "recording") r.stop();
      });
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
        releaseMic();
        setRecording(false);
        const blob = new Blob(chunks, { type: r.mimeType });
        if (recorderFailed || !blob.size) {
          setMicMessage("No audio was captured. Check the microphone input level and try again.");
          return;
        }
        const saved = {
          ...target,
          blob,
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
      releaseMic();
      refreshMicDevices().catch(() => {});
      setMicMessage(microphoneError(e, selectedMic));
      onError(microphoneError(e, selectedMic));
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
              : lesson.job?.state === "paused"
                ? "Preparation paused"
                : ['failed','cancelled'].includes(lesson.job?.state)?'Preparation needs your attention':"New chapters are on their way"}
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
          {lesson.data.format !== "paper-story-1" && (lesson.state === "ready" || lesson.data.format !== "paper-visual-2") && (
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
              {lesson.data.format !== "paper-visual-2" ? "Make a visual lesson · 図付き教材を作る" : "Make a new version · 新しい版を作る"} <RefreshCw size={14} />
            </button>
          )}
        </div>
      </div>
      <StoryProjectPanel paperId={lesson.paper_id} projectId={lesson.data.project_id} version={version} onError={onError} refresh={refresh} openLesson={onOpenLesson} />
      {lesson.data.format === "paper-story-1" && chapter?.data.expressions?.length > 0 && <section className="story-expressions">
        <h2>English you can use · 会話から学ぶ英語</h2>
        {chapter?.data.expressions.map((e:any) => <div key={e.phrase}><strong>{e.phrase}</strong><p lang="ja">{e.meaning_ja}</p><p>{e.usage_en}</p>{e.example_en && <small>{e.example_en}</small>}<button className="secondary" disabled={recording} onClick={()=>{const exact=turns.findIndex((t:any)=>t.story_utterance_id===e.utterance_id&&t.text.toLowerCase().includes(e.phrase.toLowerCase()));const at=exact>=0?exact:turns.findIndex((t:any)=>t.story_utterance_id===e.utterance_id);if(at>=0){move(at);document.querySelector('.sentence-card')?.scrollIntoView({behavior:'smooth',block:'center'});}}}>Practice this expression · この表現を練習</button></div>)}
      </section>}
      {lesson.data.format === "paper-visual-2" && <section className="video-export" aria-label="YouTube video export">
        <div>
          <strong>Video for YouTube · 動画を書き出し</strong>
          <p>Figures, English voice, and English/Japanese subtitles are built into the video.</p>
          <p lang="ja">図・英語音声・英語と日本語の字幕を動画に直接入れます。</p>
          <p className="subtle">{lesson.chapters.filter((c: Row) => c.state === "ready").length} / {lesson.chapters.length} chapters ready · 完成した章からMP4を作り、全章完成後に一本へまとめます。</p>
          <p lang="ja">公開するときは完成したMP4をダウンロードし、YouTube Studioから手動でアップロードできます。</p>
          <a href="https://studio.youtube.com/" target="_blank" rel="noreferrer">Open YouTube Studio · 手動で公開する <ExternalLink size={14} /></a>
          {lesson.youtube_connected ? <p className="subtle">YouTube: {lesson.youtube_auto_upload ? "automatic private upload is on · 完成後に非公開で自動アップロード" : "automatic upload is off · 自動アップロード停止中"}</p>
            : <p className="subtle">Optional private auto-upload needs a Google connection. · 非公開の自動投稿を使う場合だけGoogle接続が必要です。</p>}
          {lesson.youtube_connected && <button className="text-button" onClick={() => api("/youtube/auto-upload", {method:"PUT",body:JSON.stringify({enabled:!lesson.youtube_auto_upload})}).then(refresh).catch((e) => onError(e.message))}>{lesson.youtube_auto_upload ? "Stop auto-upload · 自動アップロード停止" : "Enable auto-upload · 自動アップロード開始"}</button>}
          {completeVideo?.data.youtube?.url && <a href={completeVideo.data.youtube.url} target="_blank" rel="noreferrer">Open private YouTube video · YouTubeで開く <ExternalLink size={14} /></a>}
          {completeVideo?.upload_job && !completeVideo?.data.youtube?.url && <p className="subtle">{completeVideo.upload_job.stage}{completeVideo.upload_job.error ? ` · ${completeVideo.upload_job.error}` : ""}</p>}
          {completeVideo?.upload_job?.state === "failed" && <button className="secondary" onClick={() => post(`/jobs/${completeVideo.upload_job.id}/retry`).then(refresh).catch((e) => onError(e.message))}>Retry YouTube upload · アップロード再試行</button>}
        </div>
        {completeVideo?.state === "ready" ? <div className="video-download-panel">
          <VideoTitleSuggestion title={completeVideo.data.title || suggestedVideoTitle(lesson.paper.title)} onError={onError} />
          <div className="video-links">
            <a className="primary" href={fileUrl(completeVideo.data.mp4)} download={`${completeVideo.data.title || suggestedVideoTitle(lesson.paper.title)}.mp4`}>Download complete MP4 · 全章動画</a>
            <a href={fileUrl(completeVideo.data.en_srt)} download>English SRT</a>
            <a href={fileUrl(completeVideo.data.ja_srt)} download>日本語 SRT</a>
          </div>
        </div> : completeVideo?.job?.state === "failed" ? <button className="secondary" onClick={() => post(`/jobs/${completeVideo.job.id}/retry`).then(refresh).catch((e) => onError(e.message))}>Retry complete video · 全章動画を再試行</button> : readyChapterVideo && readyChapterNumber > 0 ? <div className="video-download-panel">
          <VideoTitleSuggestion title={readyChapterVideo.data.title || suggestedVideoTitle(lesson.paper.title, readyChapterNumber)} onError={onError} />
          <div className="video-links"><a className="primary" href={fileUrl(readyChapterVideo.data.mp4)} download={`${readyChapterVideo.data.title || suggestedVideoTitle(lesson.paper.title, readyChapterNumber)}.mp4`}>Download first MP4 · 完成した章の動画</a><span className="subtle">{completeVideo?.job?.stage || "Building the remaining chapters · 残りの章を作成中"}</span></div>
        </div> : <span className="subtle">{completeVideo?.job?.stage || "Building chapters · 章を作成中"}</span>}
      </section>}
      {['failed','paused','cancelled'].includes(lesson.job?.state)&&<div className="notice"><p>{lesson.job.error||'Preparation is stopped. Your finished chapters and recordings are kept.'}</p><button className="secondary" onClick={()=>post(`/jobs/${lesson.job.id}/retry`).then(refresh).catch(e=>onError(e.message))}>Resume preparation · 教材作成を再開</button></div>}
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
                  selectVisual("auto", null);
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
                      : ({visuals:"Preparing figures and diagrams",visual_draft:"Explaining the figures",visual_dialogue_review:"Checking the talk and visuals",draft:"Writing the talk",review:"Checking the ideas",revise:"Improving the talk",english:"Making the English clear",questions:"Adding questions",question_review:"Checking questions",audio:"Making the voices",audio_review:"Checking the voices",translation:"Preparing Japanese"} as Record<string,string>)[c.state] || "Getting ready"}
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
              {ready && (chapter?.data.best_effort_omissions?.length || 0) > 0 && (
                <p className="subtle">Some details were left out because they could not be checked. See the original paper for the full results. · 確認できなかった内容は省略しています。詳細は論文の原文をご覧ください。</p>
              )}
              {currentVideo?.state === "ready" && <div className="chapter-video-links">
                <VideoTitleSuggestion title={currentVideo.data.title || suggestedVideoTitle(lesson.paper.title, (chapter?.ordinal ?? 0) + 1)} onError={onError} />
                <a className="secondary" href={fileUrl(currentVideo.data.mp4)} download={`${currentVideo.data.title || suggestedVideoTitle(lesson.paper.title, (chapter?.ordinal ?? 0) + 1)}.mp4`}>Download chapter MP4 · この章の動画</a>
                <a href={fileUrl(currentVideo.data.en_srt)} download>English SRT</a>
                <a href={fileUrl(currentVideo.data.ja_srt)} download>日本語 SRT</a>
              </div>}
              {currentVideo?.job?.state === "failed" && <button className="secondary" onClick={() => post(`/jobs/${currentVideo.job.id}/retry`).then(refresh).catch((e) => onError(e.message))}>Retry chapter video · 動画を再試行</button>}
              {currentVideo && currentVideo.state !== "ready" && currentVideo.job?.state !== "failed" && <p className="subtle">{currentVideo.job?.stage || "Preparing chapter video · この章の動画を準備中"}</p>}
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
                  {ready && !japanese("title", chapter?.data.title) && !chapter?.translation_job && (
                    <p className="subtle">日本語訳を準備しています。</p>
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
                          {s}×{s === 1 ? " · Natural" : ""}
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
                <div className={chapter?.data.visuals?.length ? "visual-session" : "audio-session"}>
                <VisualPanel references={chapter?.data.visuals || []} assets={lesson.visuals || []} cue={visualCue} mode={visualMode} selected={visualKey} onSelect={selectVisual} onSource={(sid) => api<Row>(`/sources/${encodeURIComponent(sid)}`).then(setSource).catch((e) => onError(e.message))} />
                <div className="spoken-practice">
                <div className="sentence-card">
                  <div className="sentence-top">
                    <span className="speaker">
                      <span className={`avatar ${turn?.speaker}`}>
                        {turn?.speaker === "host" ? "A" : (turn?.voice || "Maya")[0]}
                      </span>
                      {turn?.speaker === "host"
                        ? "Aiden · the curious host"
                        : `${turn?.voice || "Maya"} · your guide`}
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
                    disabled={requestingMic || sending}
                    onClick={() =>
                      recording ? recorder.current?.stop() : pending ? send(pending) : startRecording()
                    }
                  >
                    {recording ? <Square size={16} /> : <Mic size={18} />}{" "}
                    {recording
                      ? `Done · ${minutes(seconds)}`
                      : sending
                        ? "Saving…"
                        : requestingMic
                          ? "Opening microphone…"
                          : pending
                            ? "Send saved recording"
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
                </div>
                </div>
                <div className="mic-check">
                  <label htmlFor="practice-microphone">Microphone</label>
                  <select
                    id="practice-microphone"
                    value={selectedMic}
                    disabled={recording || requestingMic || testingMic || sending}
                    onChange={(e) => {
                      setSelectedMic(e.target.value);
                      localStorage.setItem("paperspeak-microphone", e.target.value);
                    }}
                  >
                    <option value="">System default</option>
                    {selectedMic && !micDevices.some((device) => device.deviceId === selectedMic) && (
                      <option value={selectedMic}>Last selected microphone</option>
                    )}
                    {micDevices.map((device) => (
                      <option key={device.deviceId} value={device.deviceId}>
                        {device.label || "Microphone"}
                      </option>
                    ))}
                  </select>
                  <button
                    className="text-button"
                    disabled={recording || requestingMic || sending}
                    onClick={testMic}
                  >
                    {testingMic ? "Stop mic check" : "Check microphone"}
                  </button>
                  <button
                    className="text-button"
                    disabled={recording || requestingMic || sending}
                    onClick={() => refreshMicDevices()
                      .then((inputs) => setMicMessage(inputs.length
                        ? `${inputs.length} microphone${inputs.length === 1 ? "" : "s"} found. Choose one above and check it.`
                        : "Chrome found no selectable microphone. Check Mac Settings → Sound → Input and Chrome's microphone permission."))
                      .catch((error) => setMicMessage(microphoneError(error, selectedMic)))}
                  >
                    Refresh microphones
                  </button>
                  {(testingMic || recording) && (
                    <div className="mic-signal" role="status">
                      <span>Input level · {activeMic}</span>
                      <progress aria-label="Microphone input level" max={100} value={micLevel} />
                      {micLevel === 0 && <small>Say hello. If this bar stays still, choose another mic or check Mac Sound → Input.</small>}
                    </div>
                  )}
                  {micMessage && <p className="mic-message" role="status">{micMessage}</p>}
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
                      <option value="guide">Maya</option>
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
          <h3>{a.phase === "normalize" ? "Your recording is in line…" : a.phase === "transcribe" ? "Listening to your words…" : "Checking sounds and rhythm…"}</h3>
          <p>Your recording is saved. You can stay here while we check it.</p>
          {a.transcript && <p className="heard"><small>WE HEARD</small>{a.transcript}</p>}
          {a.word_match != null && <p><strong>{a.word_match}%</strong> words matched · {a.pace_wpm} words per minute. Sound and rhythm feedback is still on its way.</p>}
          {a.pronunciation_focus && (
            <PronunciationCoach focus={a.pronunciation_focus} referenceAudio={a.reference_audio} recordingAudio={a.wav || a.audio} />
          )}
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
          {a.pronunciation_focus && (
            <PronunciationCoach focus={a.pronunciation_focus} referenceAudio={a.reference_audio} recordingAudio={a.wav || a.audio} />
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
function PronunciationCoach({
  focus,
  referenceAudio,
  recordingAudio,
}: {
  focus: any;
  referenceAudio?: string;
  recordingAudio?: string;
}) {
  return (
    <section className="pronunciation-focus" aria-label="Words to check">
      <h4>Words to check <span lang="ja">· 確認したい単語</span></h4>
      <p>{focus.message}</p>
      <p lang="ja">{focus.message_ja}</p>
      {focus.items?.length ? focus.items.map((item: any, i: number) => (
        <div className="pronunciation-focus-item" key={i}>
          <strong>{item.expected || "—"} → {item.heard || "—"}</strong>
          {item.kind === "same_sound" && <span className="same-sound">Same sound · 同じ発音</span>}
          <p>{item.message}</p>
          <p lang="ja">{item.message_ja}</p>
          {item.tip && <>
            <p className="sound-tip"><strong>{item.sound}</strong> {item.tip}</p>
            <p lang="ja">{item.tip_ja}</p>
          </>}
          <div className="pronunciation-focus-audio">
            {referenceAudio && item.reference_start != null && item.reference_end != null && (
              <ClipButton path={referenceAudio} start={item.reference_start} end={item.reference_end} label="Example · お手本" />
            )}
            {recordingAudio && item.start != null && item.end != null && (
              <ClipButton path={recordingAudio} start={item.start} end={item.end} label="Your voice · 自分の声" />
            )}
          </div>
        </div>
      )) : <p>No clear word differences were found. Listen to the full recording to check the sounds.<br /><span lang="ja">明確な単語の違いは見つかりませんでした。発音は録音全体を聞いて確認してください。</span></p>}
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
type StoryPanelProps = {paperId:string;projectId?:string;version:number;onError:(s:string)=>void;refresh:()=>void;openLesson:(id:string)=>void};

function StoryStudio({papers, ...props}: Omit<StoryPanelProps,"paperId"> & {papers:Row[]}) {
  const [selected,setSelected] = useState("");
  useEffect(() => {if (!selected && papers.length) setSelected((papers.find(p=>p.source_id === "2106.09685") || papers[0]).id);}, [papers,selected]);
  if (!papers.length) return null;
  return <section className="story-studio"><label>One paper. Two stories. · 論文から2本の動画
    <select value={selected} onChange={e=>setSelected(e.target.value)}>{papers.map(p=><option key={p.id} value={p.id}>{p.title}</option>)}</select>
    </label>{selected && <StoryProjectPanel paperId={selected} {...props} />}</section>;
}

function StoryProjectPanel({paperId,projectId,version,onError,refresh,openLesson}:StoryPanelProps) {
  const [project,setProject] = useState<any>(null);
  const [busy,setBusy] = useState(false);
  useEffect(() => {
    let alive=true;
    const load=async()=> {
      const id=projectId || (await api<any[]>("/video-projects")).find(p=>p.paper_id===paperId)?.id;
      const value=id ? await api<any>(`/video-projects/${id}`) : null;
      if(alive)setProject(value);
    };
    load().catch(e=>{if(alive)onError(e.message);});
    return ()=>{alive=false;};
  },[paperId,projectId,version]);
  const create=async()=> {
    setBusy(true);
    try {const r=await post(`/papers/${paperId}/video-projects`);setProject(await api(`/video-projects/${r.project_id}`));refresh();}
    catch(e){onError((e as Error).message);}finally{setBusy(false);}
  };
  const control=async(action:string)=> {
    try {await post(`/jobs/${project.job.id}/${action}`);refresh();}catch(e){onError((e as Error).message);}
  };
  const copy=(text:string)=>navigator.clipboard.writeText(text).catch(e=>onError(e.message));
  return <section className="story-project" aria-label="解説・詳解動画">
    <div className="story-heading"><div><h2>Stories worth watching · 解説と詳解</h2><p>自然なC1英語の会話、動く図解、英日字幕。数式なしの解説編と、原理まで学ぶ詳解編。</p></div>
      {!project ? <button className="primary" disabled={busy} onClick={create}>{busy?"Starting…":"解説・詳解動画を作る"} <Play size={16}/></button>
      : <div className="actions"><Badge state={project.state}>{project.state==="ready"?"2本の動画が完成":project.job?.stage || "Preparing two stories"}</Badge>
        {['queued','running'].includes(project.job?.state) && <button className="secondary" onClick={()=>control('pause')}>Pause · 一時停止</button>}
        {['paused','failed','cancelled'].includes(project.job?.state) && <button className="primary" onClick={()=>control('resume')}>Resume · 続きから再開</button>}</div>}
    </div>
    {project && <>
      {project.job?.error && <p role="alert">{project.job.error}</p>}
      <progress max={1} value={project.job?.progress || 0}/>
      <div className="story-film-grid">{Object.entries(project.data.modes || {}).map(([mode,entry])=> {
        const track=entry as any;
        const complete=track.videos?.find((v:any)=>v.kind===mode&&v.state==='ready');
        const preview=track.videos?.find((v:any)=>v.kind===`${mode}_preview`&&v.state==='ready');
        const visible=complete||preview;
        return <article className="story-film" key={mode}><div className="eyebrow">{mode==='overview'?'THE IDEA · 数式なし':'UNDER THE HOOD · 数式と原理'}</div>
          <h3>{track.packaging?.title || track.label}</h3><p>{track.preset.range.join('〜')}分 · {complete?'完成':track.phase}</p>
          {visible && <video controls preload="metadata" poster={fileUrl(visible.data.thumbnail)} src={fileUrl(visible.data.mp4)} />}
          {preview&&!complete&&<small>冒頭約90秒のプレビューです。全体の作成は続いています。</small>}
          <div className="actions">{complete&&<a className="primary" href={fileUrl(complete.data.mp4)} download={`${complete.data.title}.mp4`}>Download MP4 · 動画</a>}
            {visible&&<a className="secondary" href={fileUrl(visible.data.thumbnail)} download={`${visible.data.title}.png`}>Thumbnail · サムネイル</a>}
            <button className="secondary" onClick={()=>openLesson(track.lesson_id)}>Practice English · 英語練習</button></div>
          {track.packaging&&<details><summary>Titles & description · タイトルと説明欄</summary>
            {track.packaging.candidates.map((c:any,i:number)=><div className="story-title" key={i}><span>{c.title_ja}</span><button className="text-button" onClick={()=>copy(c.title_ja)}>Copy</button></div>)}
            <textarea aria-label={`${track.label} YouTube description`} readOnly value={complete?.data.description || track.packaging.description || "動画の完成時に説明文とタイムスタンプを用意します。"}/>
            <button className="secondary" onClick={()=>copy(complete?.data.description || track.packaging.description || '')}>Copy description · 説明欄をコピー</button></details>}
          {!!track.scenes?.length&&<details><summary>Script & review · 脚本と確認結果</summary>{track.scenes.map((s:any,i:number)=><div className="story-script" key={i}><h4>{s.title} / {s.title_ja}</h4>{s.utterances.map((u:any)=><p key={u.id}><strong>{u.speaker==='guide'?'Maya':'Aiden'}:</strong> {u.text}</p>)}{Object.entries(s.reviews||{}).map(([kind,value])=>{const review=value as any;return <small className="story-review" key={kind}>{kind==='content'?'Content · 内容':'Editing · 編集'}: {review.status||'checking'}{review.history?.at(-1)?.notes&&` — ${review.history.at(-1).notes}`}</small>;})}</div>)}</details>}
          {!!track.expressions?.length&&<details><summary>Useful English · 使える表現</summary>{track.expressions.map((e:any)=><p key={e.phrase}><strong>{e.phrase}</strong> — {e.meaning_ja}<br/>{e.usage_en}</p>)}</details>}
        </article>;
      })}</div>
      {!!project.data.warnings?.length&&<details><summary>Generation notes · 作成時の補足 ({project.data.warnings.length})</summary>{project.data.warnings.map((w:any,i:number)=><p key={i}>{w.reason} · {w.action}</p>)}</details>}
      <p className="subtle">完成した動画はYouTube Studioへ手動でアップロードできます。英語と日本語の字幕は動画に直接入ります。</p>
    </>}
  </section>;
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
