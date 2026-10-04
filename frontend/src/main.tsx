import React, { useState, useEffect, useRef } from "react";
import { createRoot } from "react-dom/client";
import {
  BookOpen,
  Compass,
  Headphones,
  Activity,
  Film,
  Clapperboard,
  Settings,
  ArrowUpRight,
  ArrowRight,
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
import { VideoLibrary } from "./VideoLibrary";

type Page = "library" | "create" | "practice" | "discover" | "review" | "settings" | "learn";
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
  ["library", "動画一覧", Film],
  ["create", "動画を作る", Clapperboard],
  ["practice", "英語練習", Headphones],
  ["discover", "論文を探す", Compass],
  ["settings", "設定", Settings],
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
    [reviews, setReviews] = useState<Row[]>([]);
  const [searches, setSearches] = useState<any[]>([]),
    [searchQuery, setSearchQuery] = useState("");
  const [status, setStatus] = useState<any>(null),
    [version, setVersion] = useState(0),
    [error, setError] = useState(""),
    [adding, setAdding] = useState(false);
  const [creationTab, setCreationTab] = useState("auto"),
    [selectedPaper, setSelectedPaper] = useState(""),
    [importJob, setImportJob] = useState<string | null>(null),
    [showAwards, setShowAwards] = useState(false),
    [showJobs, setShowJobs] = useState(false);
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
      api<Row[]>("/reviews"),
      api<any[]>("/paper-searches"),
    ])
      .then(([l, p, j, v, s]) => {
        setLessons(l);
        setPapers(p);
        setJobs(j);
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
    if (recordBusy) { setError("Finish your recording first."); return; }
    setReviewTarget(target);
    setActive(id);
    navigate("learn");
  };
  const createStory = async (paperId: string) => {
    try {
      await post(`/papers/${paperId}/video-projects`);
      setSelectedPaper(paperId);
      setCreationTab("manual");
      navigate("create");
      refresh();
    } catch (e) { setError((e as Error).message); }
  };
  const busy = jobs.filter(j => ["queued", "running"].includes(j.state));
  const imported = jobs.find(j => j.id === importJob);
  useEffect(() => {
    if (imported?.state === "completed" && imported.paper_id) {
      setSelectedPaper(imported.paper_id);
      setCreationTab("manual");
      setImportJob(null);
    }
  }, [imported?.state, imported?.paper_id]);
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
            PaperSpeak<small>論文・動画・英語練習</small>
          </span>
        </a>
        <nav>
          {tabs.map(([key, label, Icon]) => (
            <button
              key={key}
              aria-label={label}
              className={
                page === key || (key === "practice" && ["learn", "review"].includes(page))
                  ? "selected"
                  : ""
              }
              onClick={() => navigate(key)}
            >
              <Icon size={18} />
              {label}
              {key === "create" && busy.some(j => ["nightly_video", "video_project", "import"].includes(j.kind)) && <b>作成中</b>}
              {key === "practice" && reviews.length > 0 && (
                <b>{reviews.length}</b>
              )}
            </button>
          ))}
        </nav>
        <div className="local-status">
          <span
            className={`dot ${status?.worker && Date.now() / 1000 - status.worker.heartbeat < 90 ? "" : "off"}`}
          />
          <div>
            {status?.password_required === false ? "LANで利用中" : "ローカルで利用中"}
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
          <span>PaperSpeak</span>
          <span>
            {new Date().toLocaleDateString("ja-JP", {
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
        {page === "library" && <VideoLibrary version={version} onError={setError} openLesson={openLesson} create={() => navigate("create")} />}
        {page === "create" && <>
          <div className="workspace-heading"><PageHeading eyebrow="論文から2本の動画へ" title="動画を作る" description="論文を自動で選ぶか、保存済みの論文を指定してください。作成中も完成した動画や英語教材を使えます。" />
            <button className="secondary" onClick={() => setAdding(true)}><Upload size={16} /> arXiv・PDFを取り込む</button></div>
          {importJob && <div className="notice" role="status"><div><strong>論文を取り込み中</strong><p>{imported?.error || imported?.stage || "取得後に概要・詳細の2本を自動で作ります。"}</p></div>
            {imported && ["paused", "failed", "cancelled"].includes(imported.state) && <button className="secondary" onClick={() => act(() => post(`/jobs/${imported.id}/resume`))}>取り込みを再開</button>}
            <button className="text-button" onClick={() => setImportJob(null)}>表示を閉じる</button></div>}
          <div className="workspace-tabs" role="tablist" aria-label="動画の作り方">
            <button role="tab" aria-selected={creationTab === "auto"} onClick={() => setCreationTab("auto")}>自動で論文を選ぶ</button>
            <button role="tab" aria-selected={creationTab === "manual"} onClick={() => setCreationTab("manual")}>保存済みの論文から作る</button>
          </div>
          {creationTab === "auto" ? <NightlyVideos version={version} onError={setError} refresh={refresh} openLesson={openLesson} viewVideos={() => navigate("library")} />
            : <StoryStudio papers={papers} lessonPaperIds={lessons.map(l=>l.paper_id)} selectedPaperId={selectedPaper} version={version} onError={setError} refresh={refresh} openLesson={openLesson} />}
          <details className="workspace-details" onToggle={e => setShowAwards(e.currentTarget.open)}><summary>学会の受賞情報・取得状況</summary>{showAwards && <ConferenceAwards version={version} onError={setError} refresh={refresh} />}</details>
          <details className="workspace-details" onToggle={e => setShowJobs(e.currentTarget.open)}><summary>詳しい処理状況</summary>{showJobs && <ProcessingJobs jobs={jobs} act={act} />}</details>
        </>}
        {page === "practice" && <PracticeLibrary lessons={lessons} papers={papers} reviews={reviews.length} openLesson={openLesson} review={() => navigate("review")} />}
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
                      <button className="primary" onClick={() => createStory(paper.paper_id)}>
                        解説・詳解動画を作る <ArrowRight size={16} />
                      </button>
                      <a className="text-button" href={paper.url} target="_blank" rel="noreferrer">原論文を見る <ExternalLink size={14} /></a>
                    </div>
                  </article>
                ))}
              </section>
            )}
          </>
        )}
        {page === "review" && (
          <>
            <PageHeading
              eyebrow="前に練習した表現をもう一度"
              title="今日の復習"
              description="録音練習で保存した文と、理解を確かめる問題を復習できます。"
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
            onBack={() => navigate("practice")}
          />
        )}
      </main>
      {adding && (
        <AddPaper
          close={() => setAdding(false)}
          onError={setError}
          onDone={(result) => {
            setSelectedPaper(result.paper_id || "");
            setCreationTab("manual");
            setImportJob(result.paper_id ? null : result.job_id);
            navigate("create");
            refresh();
          }}
        />
      )}
    </div>
  );
}
function PracticeLibrary({lessons,papers,reviews,openLesson,review}:{lessons:Row[];papers:Row[];reviews:number;openLesson:(id:string)=>void;review:()=>void}) {
  const [query,setQuery]=useState("");
  const paperById=new Map(papers.map(p=>[p.id,p]));
  const groups=new Map<string,Row[]>();
  for(const lesson of lessons) {
    const key=lesson.paper_id||lesson.id;
    if(!groups.has(key))groups.set(key,[]);
    groups.get(key)!.push(lesson);
  }
  const titleFor=(id:string,rows:Row[])=>paperById.get(id)?.title||rows[0].data.title.replace(/\s*[·—]\s*(解説編|詳解編|概要解説|詳細解説)$/,'');
  const labelFor=(l:Row)=>l.data.mode==='overview'?'概要解説':l.data.mode==='deep_dive'?'詳細解説':'英語練習';
  const visible=Array.from(groups).filter(([id,rows])=>`${titleFor(id,rows)} ${rows.map(l=>l.data.title+' '+labelFor(l)).join(' ')}`.toLowerCase().includes(query.trim().toLowerCase()));
  const lessonButton=(l:Row)=><button className="lesson-card" aria-label={`${l.data.title}を練習`} data-lesson-id={l.id} key={l.id} onClick={()=>openLesson(l.id)}>
    <Headphones size={17}/><span className="practice-track-label">{labelFor(l)}<small>{l.ready_chapters||0} / {l.chapter_count||0}場面</small></span>
    <Badge state={l.state==='ready'?'ready':l.state}>{l.state==='ready'?'練習できます':l.ready_chapters?'完成分を練習':'作成中'}</Badge><ArrowUpRight size={18}/>
  </button>;
  return <section aria-label="英語練習教材">
    <div className="workspace-heading"><PageHeading eyebrow="動画と同じ会話で練習" title="英語練習" description="論文を選んで、概要・詳細の会話を練習できます。完成した場面から聞く・話す・理解を確かめる練習へ。" />
      <button className="secondary" onClick={review}><RotateCcw size={16}/> 今日の復習{reviews>0&&`（${reviews}件）`}</button></div>
    <label className="practice-search">教材を検索<input type="search" value={query} onChange={e=>setQuery(e.target.value)} placeholder="論文名・概要解説・詳細解説…"/></label>
    <div className="lesson-grid practice-groups">{visible.map(([id,rows])=>{
      const latest=new Map<string,Row>(),earlier:Row[]=[];
      for(const l of rows){const mode=l.data.mode||'lesson';if(!latest.has(mode))latest.set(mode,l);else earlier.push(l);}
      return <article className="practice-paper" key={id} data-paper-id={id}>
        <div className="card-meta"><span>英語教材</span><time dateTime={new Date(rows[0].created*1000).toISOString()}>{new Date(rows[0].created*1000).toLocaleDateString('ja-JP',{timeZone:'Asia/Tokyo',month:'long',day:'numeric'})}</time></div>
        <h2>{titleFor(id,rows)}</h2><div className="practice-tracks">{Array.from(latest.values()).map(lessonButton)}</div>
        {earlier.length>0&&<details className="practice-revisions"><summary>以前の教材（{earlier.length}件）</summary>{earlier.map(lessonButton)}</details>}
      </article>;
    })}</div>
    {!visible.length&&<p className="notice">{query?'条件に合う教材がありません。':'動画を作ると、同じ会話の英語教材がここに表示されます。'}</p>}
  </section>;
}

function ProcessingJobs({jobs,act}:{jobs:Row[];act:(fn:()=>Promise<any>)=>Promise<void>}) {
  const [history,setHistory]=useState(false);
  const kinds:Record<string,string>={video_project:'概要・詳細動画',story_video:'動画の書き出し',nightly_video:'論文の自動選定',thumbnail:'サムネイル',import:'論文の取り込み',paper_search:'日本語の論文検索',practice:'録音の評価',award_refresh:'受賞情報の更新'};
  const states:Record<string,string>={running:'処理中',queued:'待機中',paused:'一時停止',failed:'問題あり',cancelled:'停止済み',completed:'完了'};
  const visible=jobs.filter(j=>kinds[j.kind]&&(history||j.state!=='completed'&&j.state!=='cancelled'));
  return <section aria-label="処理状況"><label className="check-label"><input type="checkbox" checked={history} onChange={e=>setHistory(e.target.checked)}/> 完了・停止した処理も表示</label>
    <div className="jobs">{visible.map(j=><article className="job" key={j.id}><div className="job-top"><Badge state={j.state}>{states[j.state]||j.state}</Badge><strong>{kinds[j.kind]}</strong><small>{date(j.created)}</small></div>
      <p>{j.stage}</p><progress value={j.progress} max={1}/>{j.error&&<p className="job-error">{j.error}</p>}
      <div className="actions">{['queued','running'].includes(j.state)&&<button className="secondary" onClick={()=>act(()=>post(`/jobs/${j.id}/pause`))}>一時停止</button>}
        {['paused','failed','cancelled'].includes(j.state)&&<button className="secondary" onClick={()=>act(()=>post(`/jobs/${j.id}/resume`))}>続きから再開</button>}
        {!['completed','cancelled'].includes(j.state)&&<button className="text-button" onClick={()=>act(()=>post(`/jobs/${j.id}/cancel`))}>停止</button>}</div>
    </article>)}</div>{!visible.length&&<p>処理中の作業はありません。</p>}
  </section>;
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
  onDone: (result: any) => void;
  onError: (s: string) => void;
}) {
  const [ref, setRef] = useState(""),
    [busy, setBusy] = useState(false);
  const upload = async (file: File) => {
    setBusy(true);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("create_video", "true");
      const result = await api("/papers/upload", { method: "POST", body: form });
      onDone(result);
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
        aria-label="論文を取り込む"
      >
        <button className="modal-close" onClick={close} aria-label="Close">
          <X />
        </button>
        <div className="eyebrow">arXiv / PDF</div>
        <h2>論文を取り込んで動画を作る</h2>
        <p>概要解説・詳細解説の動画と、同じ会話で練習できる英語教材を作ります。</p>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            try {
              const result = await post("/papers/import", { reference: ref, generate: false, create_video: true });
              onDone(result);
              close();
            } catch (e) {
              onError((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <label>
            arXivのURLまたは論文ID
            <input
              placeholder="https://arxiv.org/abs/1706.03762"
              value={ref}
              onChange={(e) => setRef(e.target.value)}
              required
            />
          </label>
          <button disabled={busy} className="primary">
            {busy ? "取り込み中…" : "取り込んで2本の動画を作る"}
            <ArrowRight size={17} />
          </button>
        </form>
        <div className="or-divider">またはPDFを取り込む</div>
        <label className="upload">
          <Upload size={20} /> PDFを選ぶ
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
        <small>100 MBまで。生成とデータ保存はこのLinux内で行います。</small>
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
        eyebrow="夜間運転とローカルモデル"
        title="設定"
        description="夜間の開始時刻・対象分野を変更できます。"
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
        <h2>夜間に解説・詳解を自動作成</h2>
        <label className="check-label"><input type="checkbox" checked={s.nightly_video_enabled} onChange={e=>update("nightly_video_enabled",e.target.checked)}/> 毎晩、注目論文から2本の動画と英語教材を作る</label>
        <label className="check-label"><input type="checkbox" checked={s.nightly_video_awards_first} onChange={e=>update("nightly_video_awards_first",e.target.checked)}/> 最近のAI・ロボティクス学会の優秀論文賞・Test of Time賞を優先する</label>
        <p>最新・前年の受賞を公式に確認した未動画化の論文を優先します。Test of Time賞は古い論文も対象とし、発表年と受賞年を区別します。適した受賞論文がなければ、新着論文から選びます。</p>
        <p>朝の完成を目指し、長引いても続行します。前日の作成が残っている日は追加しません。</p>
        <div className="form-grid"><label>開始時刻 · 日本時間<input type="time" value={`${String(s.nightly_video_hour).padStart(2,"0")}:${String(s.nightly_video_minute).padStart(2,"0")}`} onChange={e=>{const [h,m]=e.target.value.split(":").map(Number);setSaved(false);setS({...s,nightly_video_hour:h,nightly_video_minute:m});}}/></label>
        <label>対象分野<select multiple value={s.nightly_video_categories} onChange={e=>update("nightly_video_categories",Array.from(e.target.selectedOptions,o=>o.value))}>{[["cs.AI","AI"],["cs.LG","機械学習"],["cs.CL","LLM・言語"],["cs.CV","画像・視覚"],["cs.RO","ロボティクス"]].map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label></div>
        <details className="workspace-details"><summary>モデルと診断</summary>
        <label>
          論文を読むモデル
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
          導入済み: {status?.models?.join(", ") || "確認中…"}
        </p>
        </details>
        <div className="actions">
          <button className="primary">
            設定を保存 <Check size={16} />
          </button>
          {saved && (
            <span className="saved">保存しました。</span>
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
        <ChevronLeft size={17} /> Back to lessons · 教材一覧
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
        </div>
      </div>
      {lesson.data.format === "paper-story-1" && chapter?.data.expressions?.length > 0 && <details className="story-expressions">
        <summary>Useful English · 会話から学ぶ表現</summary>
        {chapter?.data.expressions.map((e:any) => <div key={e.phrase}><strong>{e.phrase}</strong><p lang="ja">{e.meaning_ja}</p><p>{e.usage_en}</p>{e.example_en && <small>{e.example_en}</small>}<button className="secondary" disabled={recording} onClick={()=>{const exact=turns.findIndex((t:any)=>t.story_utterance_id===e.utterance_id&&t.text.toLowerCase().includes(e.phrase.toLowerCase()));const at=exact>=0?exact:turns.findIndex((t:any)=>t.story_utterance_id===e.utterance_id);if(at>=0){move(at);document.querySelector('.sentence-card')?.scrollIntoView({behavior:'smooth',block:'center'});}}}>Practice this expression · この表現を練習</button></div>)}
      </details>}
      {lesson.data.format === 'paper-story-1' && ['failed','paused','cancelled'].includes(lesson.job?.state)&&<div className="notice"><p>{lesson.job.error||'Preparation is stopped. Your finished chapters and recordings are kept.'}</p><button className="secondary" onClick={()=>post(`/jobs/${lesson.job.id}/resume`).then(refresh).catch(e=>onError(e.message))}>Resume preparation · 教材作成を再開</button></div>}
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
const awardStatusLabels:Record<string,string>={"verified winners":"受賞確認済み","not announced":"開催前・受賞未発表","finalists only":"最終候補のみ","no confirmed winners":"受賞者未確認",unavailable:"取得できず","not checked":"未確認"};
const awardReceiptLabel=(r:any)=>r.stale?'保存済み・今回の取得失敗':r.coverage?.partial?`未取得の賞あり · ${r.coverage.confirmed_categories}賞確認 / ${r.coverage.category_count}掲載枠`:awardStatusLabels[r.status]||r.status;

function ConferenceAwards({version,onError,refresh}:{version:number;onError:(s:string)=>void;refresh:()=>void}) {
  const [catalogue,setCatalogue]=useState<any>(null);
  const [selectedVenue,setSelectedVenue]=useState(''),[busy,setBusy]=useState(false);
  useEffect(()=>{let live=true;api<any>("/conference-awards").then(r=>{if(live)setCatalogue(r);}).catch(e=>{if(live)onError(e.message);});return()=>{live=false;};},[version]);
  const update=async()=>{setBusy(true);try{await post('/conference-awards/refresh');refresh();}catch(e){onError((e as Error).message);}finally{setBusy(false);}};
  const control=async(action:string)=>{try{await post(`/jobs/${catalogue.refresh_job.id}/${action}`);refresh();}catch(e){onError((e as Error).message);}};
  if(!catalogue)return null;
  const venues=Array.from(new Set<string>(catalogue.sources.map((r:any)=>r.source.venue)));
  const years=Array.from(new Set<number>(catalogue.sources.map((r:any)=>r.source.year))).sort((a,b)=>b-a);
  const job=catalogue.refresh_job,updating=['queued','running'].includes(job?.state);
  return <section className="conference-awards" aria-label="学会別の受賞論文">
    <div className="actions"><h3>学会別の受賞論文</h3><button className="secondary" disabled={busy||updating} onClick={update}>{busy||updating?'受賞情報を更新中…':'受賞情報を更新'}</button>{['paused','failed','cancelled'].includes(job?.state)&&<button className="text-button" onClick={()=>control('resume')}>受賞情報の更新を再開</button>}</div>
    <p className="award-total">{catalogue.winner_count}件の受賞情報 / {catalogue.paper_count}本 · {venues.length}学会</p>
    {updating&&<><p>{job.stage}</p><progress max={1} value={job.progress||0}/><button className="text-button" onClick={()=>control('pause')}>受賞情報の更新を一時停止</button></>}
    {job?.error&&<p role="alert">{job.error}</p>}
    <p className="subtle">学会名を押すと、論文・賞・出典を見られます。未取得の賞がある学会には、その賞も表示します。件数は確認できた受賞情報で、全賞の取得完了を示すものではありません。</p>
    <p className="subtle">通常の取得で不足すると、ローカルAIが保存した本文と公式リンクを調べて補完します。失敗した出典は最大3回試し、次へ進みます。</p>
    <table className="award-coverage-table"><caption>学会ごとの取得状況</caption><thead><tr><th scope="col">学会</th>{years.map(y=><th scope="col" key={y}>{y}年</th>)}</tr></thead><tbody>{venues.map(venue=><tr key={venue}><th scope="row"><button className="text-button" aria-label={`${venue}の受賞情報を見る`} onClick={()=>setSelectedVenue(venue)}>{venue}</button></th>{years.map(year=>{
      const r=catalogue.sources.find((s:any)=>s.source.venue===venue&&s.source.year===year);
      return <td key={year}>{r?<><strong>{r.winner_count}件 / {r.paper_count}本</strong><small>{awardReceiptLabel(r)}</small></>:<small>対象外</small>}</td>;
    })}</tr>)}</tbody></table>
    <details className="award-paper-lists" open={!!selectedVenue} onToggle={e=>{if(!e.currentTarget.open)setSelectedVenue('');else if(!selectedVenue)setSelectedVenue('all');}}><summary>受賞論文の一覧と取得状況</summary>
    <label>表示する学会 <select aria-label="受賞論文を表示する学会" value={selectedVenue||'all'} onChange={e=>setSelectedVenue(e.target.value)}><option value="all">すべての学会</option>{venues.map(v=><option key={v} value={v}>{v}</option>)}</select></label>
    <div className="award-coverage-grid">{catalogue.sources.filter((r:any)=>!selectedVenue||selectedVenue==='all'||r.source.venue===selectedVenue).map((r:any)=>{
      const s=r.source;
      return <article key={`${s.venue}-${s.year}`} aria-label={`${s.venue} ${s.year}の受賞情報`}>
        <h3><a href={s.url} target="_blank" rel="noreferrer">{s.venue} {s.year}</a></h3>
        <p className="award-coverage-status">{awardReceiptLabel(r)} · 受賞情報 {r.winner_count}件 / 論文 {r.paper_count}本</p>
        {r.checked_at&&<p className="subtle">確認: {new Date(r.checked_at*1000).toLocaleString('ja-JP',{timeZone:'Asia/Tokyo'})}（日本時間）</p>}
        {r.stale&&r.last_successful_check&&<p className="subtle">保存済みの受賞情報を表示しています。取得成功: {new Date(r.last_successful_check*1000).toLocaleString('ja-JP',{timeZone:'Asia/Tokyo'})}</p>}
        {r.conference_start&&<p className="subtle">開催開始: {r.conference_start}</p>}
        {r.coverage?.missing_categories?.length>0&&<details className="award-missing-categories"><summary>受賞者を未取得の賞（{r.coverage.missing_categories.length}件）</summary><ul>{r.coverage.missing_categories.map((a:any)=><li key={a.name}><a href={a.official_url} target="_blank" rel="noreferrer">{a.name}</a> · 賞の掲載は確認済み、受賞者は未取得</li>)}</ul></details>}
        {r.local_ai&&<details className="award-ai-research"><summary>ローカルAIの調査記録 · {r.local_ai.state==='completed'?'確認済み':'調査中'} · {r.local_ai.units}工程</summary>{r.local_ai.records?.map((record:any,i:number)=><p key={i}>{record.url&&<a href={record.url} target="_blank" rel="noreferrer">出典</a>} · {record.reason_ja}{record.error&&<> · {record.error}</>}</p>)}</details>}
        {r.papers.length>0&&<details><summary>受賞論文を表示（{r.paper_count}本）</summary><ul>{r.papers.map((p:any,i:number)=><li key={`${p.title}-${p.name}-${i}`}><strong>{p.title}</strong><br/><a href={p.official_url} target="_blank" rel="noreferrer">{p.name}</a>{p.evidence_type==='institution'&&<span> · 所属機関の受賞発表</span>}{p.source_page&&<span> · 冊子 {p.source_page}ページ</span>}{p.kind==='test-of-time'&&<span> · 長年の影響を評価する賞</span>}</li>)}</ul></details>}
        {r.failures?.length>0&&<details className="award-fetch-failures"><summary>{r.papers.length?'一部の取得先に問題':'取得先の問題'}（{r.failures.length}件）</summary><ul>{r.failures.map((f:any,i:number)=><li key={i}><a href={f.url} target="_blank" rel="noreferrer">{f.url}</a><p>{f.reason}</p></li>)}</ul></details>}
      </article>;
    })}</div></details>
  </section>;
}

function NightlyVideos({version,onError,refresh,openLesson,viewVideos}:{version:number;onError:(s:string)=>void;refresh:()=>void;openLesson:(id:string)=>void;viewVideos:()=>void}) {
  const [runs,setRuns]=useState<any[] | null>(null),[busy,setBusy]=useState(false),[expanded,setExpanded]=useState(false);
  useEffect(()=>{let live=true;api<any[]>("/nightly-video-runs").then(r=>{if(live)setRuns(r);}).catch(e=>{if(live)onError(e.message);});return()=>{live=false;};},[version]);
  const start=async()=>{setBusy(true);try{await post("/nightly-video-runs");refresh();}catch(e){onError((e as Error).message);}finally{setBusy(false);}};
  const run=runs?.[0],activeRun=run?.continuing_run||run,activeJob=activeRun?.job;
  const control=async(action:string)=>{try{await post(`/jobs/${activeJob.id}/${action}`);refresh();}catch(e){onError((e as Error).message);}};
  const labels:Record<string,string>={searching:"論文を探索中",reading:"本文を確認中",building:"動画を作成中",ready:"2本が完成",skipped:"今夜は見送り",paused:"一時停止",failed:"作成時の問題",cancelled:"停止済み"};
  return <section id="nightly-videos" className="nightly-panel" aria-label="自動選定と動画作成"><div className="story-heading"><div><h2>論文を自動で選ぶ</h2><p>最近の学会の受賞論文を中心に、概要・詳細の2本を作ります。夜間運転は設定から変更できます。</p></div><button className="primary" disabled={busy} onClick={start}>{busy?"開始中…":"今すぐ論文を選んで作る"}</button></div>
  {run?<><div className="actions"><Badge state={run.state}>{labels[run.state]||run.state}</Badge><span>{run.day} · 日本時間{run.data.manual&&' · 手動実行'}</span>{['queued','running'].includes(activeJob?.state)&&<button className="secondary" onClick={()=>control('pause')}>一時停止</button>}{['paused','failed','cancelled'].includes(activeJob?.state)&&<button className="secondary" onClick={()=>control('resume')}>続きから再開</button>}</div>{run.state==='ready'&&<p>完成した動画を残して、別の論文で追加作成できます。</p>}
  <h3>{activeRun.data.selected?.title||activeJob?.stage||run.data.reason}</h3>{run.continuing_run&&<p>継続中: {run.continuing_run.day}の動画</p>}{activeRun.data.selected?.assessment?.why_ja&&<details><summary>論文を選んだ理由</summary><p>{activeRun.data.selected.assessment.why_ja}</p></details>}
  {activeRun.data.selected?.awards?.map((a:any)=><p key={`${a.venue}-${a.year}-${a.name}`}><a href={a.official_url} target="_blank" rel="noreferrer">{a.venue} {a.year} · {a.name}</a> · 公式受賞情報確認済み{a.kind==='test-of-time'&&<> · 長年の影響を評価する賞{activeRun.data.selected.published&&<> · 論文発表 {activeRun.data.selected.published.slice(0,4)}年 / 受賞 {a.year}年</>}</>}</p>)}
  {run.data.award_fallback_reason&&<p>{run.data.award_fallback_reason}</p>}
  {run.data.selected?.attention&&<p className="subtle">注目情報: <a href={run.data.selected.attention.source_url} target="_blank" rel="noreferrer">Hugging Face Daily Papers</a> · {new Date(run.data.selected.attention.retrieved_at*1000).toLocaleString()}</p>}
  {run.data.reason&&<p>{run.data.reason}</p>}{run.data.waiting_reason&&(run.state==='building'||run.continuing_run)&&<p>{run.data.waiting_reason}</p>}{activeJob&&<progress max={1} value={run.project?.job?.progress||activeJob.progress||0}/>}
  {run.project&&<><NightlyFilmCards project={run.project} openLesson={openLesson} viewVideos={viewVideos}/><button className="text-button" onClick={()=>setExpanded(!expanded)}>{expanded?'詳細を閉じる':'脚本・プレビュー・サムネイル設定'}</button>{expanded&&<StoryProjectPanel paperId={run.project.paper_id} projectId={run.project.id} version={version} onError={onError} refresh={refresh} openLesson={openLesson}/>}</>}
  <details><summary>選定・作成の記録</summary>{run.data.award_sources?.map((r:any,i:number)=><p key={`award-${i}`}><a href={r.source.url} target="_blank" rel="noreferrer">{r.source.venue} {r.source.year}</a>: {awardStatusLabels[r.status]||'受賞を確認できず'}</p>)}{run.data.search_plans?.map((p:any,i:number)=><p key={`plan-${i}`}>ローカルAIの検索方針: {p.reason_ja}</p>)}{run.data.search_history?.map((q:any,i:number)=><p key={`query-${i}`}>{q.title||q.category} · {q.terms?.join(' / ')} · {q.reason_ja}{q.result_count!==undefined&&<> · {q.result_count}候補</>}{q.matched!==undefined&&<> · {q.matched?'題名一致':'一致する取得先なし'}</>}</p>)}{run.data.review_summary?.map((r:any)=><p key={r.paper_id}>{r.title} — {r.assessment.why_ja}</p>)}{Object.entries(run.data.timings||{}).filter(([,v])=>typeof v==='number').map(([k,v])=><p key={k}>{k}: {Math.round(Number(v)/60)}分</p>)}{run.data.warnings?.map((w:any,i:number)=><p key={i}>{w.unit}: {w.reason}</p>)}</details>
  {runs&&runs.length>1&&<details><summary>過去の夜間運転</summary><div className="run-history">{runs.slice(1).map(r=><div key={r.id}><span>{r.day}</span><Badge state={r.state}>{labels[r.state]||r.state}</Badge><strong>{r.data.selected?.title||r.data.reason||'選定中'}</strong>{r.project&&<button className="text-button" onClick={viewVideos}>完成動画を見る</button>}</div>)}</div></details>}</>:runs===null?<p role="status">作成状況を読み込み中…</p>:<p>まだ夜間運転の記録がありません。設定で開始時刻と分野を変更できます。</p>}
  </section>;
}

function NightlyFilmCards({project,openLesson,viewVideos}:{project:any;openLesson:(id:string)=>void;viewVideos:()=>void}) {
  return <div className="nightly-tracks">{Object.entries(project.data.modes).map(([mode,value])=>{
    const track=value as any;
    const film=track.videos.find((v:any)=>v.kind===mode&&v.state==='ready');
    return <article key={mode} aria-label={track.label}><strong>{track.label}</strong>{film?<p>動画完成 · {minutes(film.data.duration)}</p>:<StoryGenerationProgress track={track}/>}
      {film&&<button className="secondary" onClick={viewVideos}>完成動画を見る</button>}<button className="text-button" onClick={()=>openLesson(track.lesson_id)}>英語練習</button>
    </article>;
  })}</div>;
}
function StoryGenerationProgress({track}:{track:any}) {
  const p=track.generation_progress;
  const labels:Record<string,string>={script:'脚本・図を作成中',tts:p?.speech_checked?'音声の読み違いを修正中':'音声を作成中',align:'音声チェック・日本語字幕を作成中',learning:'英語練習を用意しています',export:'動画を出力中'};
  return <div className="story-generation-progress"><p>{labels[track.phase]||'作成準備中'}</p>
    {p&&<>{track.phase==='script'&&<p>準備できた場面 {p.visuals_ready}／{p.scenes_total}</p>}
      {p.utterances_total>0&&<p>音声 {p.speech_ready}／{p.utterances_total} · 音声チェック {p.speech_checked}／{p.utterances_total}</p>}
      {p.scenes_total>0&&<p>日本語字幕 {p.subtitled_scenes}／{p.scenes_total}場面{track.phase==='learning'&&<> · 英語練習 {p.practice_scenes}／{p.scenes_total}場面</>}</p>}
      {p.updated&&<small>最終更新 {new Date(p.updated*1000).toLocaleTimeString('ja-JP',{timeZone:'Asia/Tokyo'})}（日本時間）</small>}</>}
  </div>;
}
function ThumbnailChoices({projectId,mode,track,onError,refresh}:{projectId:string;mode:string;track:any;onError:(s:string)=>void;refresh:()=>void}) {
  const [busy,setBusy]=useState(false);
  const row=track.thumbnails;
  const select=async(id:string)=>{setBusy(true);try{await api(`/thumbnail-sets/${row.id}/selection`,{method:"PUT",body:JSON.stringify({candidate_id:id})});refresh();}catch(e){onError((e as Error).message);}finally{setBusy(false);}};
  const regenerate=async()=>{setBusy(true);try{await post(`/video-projects/${projectId}/thumbnails`,{mode});refresh();}catch(e){onError((e as Error).message);}finally{setBusy(false);}};
  if(!track.packaging||!track.scenes?.length||track.scenes.some((s:any)=>!s.utterances?.length))return null;
  return <div className="thumbnail-choices"><div className="actions"><h4>サムネイル候補</h4><button className="text-button" disabled={busy||row?.state==='building'} onClick={regenerate}>{row?.state==='building'?'作成中…':row?'3案を再生成':'3案を作る'}</button></div>
  {row?.data.review?.notes_ja&&<p className="subtle">{row.data.review.notes_ja}</p>}
  <div className="thumbnail-grid">{row?.data.candidates?.map((c:any,i:number)=><article key={c.id} className={row.data.selected_id===c.id?'selected':''}>{c.png?<img src={fileUrl(c.png)} alt={`候補${i+1}: ${c.lines.join(' ')}`}/>:<p>{c.lines.join(' / ')} · 作成待ち</p>}<small>{row.data.recommended_id===c.id?'AI推奨 · ':''}{row.data.selected_id===c.id?'選択中':`候補 ${i+1}`}</small>{c.png&&<><button className="secondary" disabled={busy||row.data.selected_id===c.id} onClick={()=>select(c.id)}>この案を使う</button><div className="actions"><a href={fileUrl(c.png)} download={`${track.packaging.title}-thumbnail-${i+1}.png`}>PNG</a><a href={fileUrl(c.jpg)} download={`${track.packaging.title}-thumbnail-${i+1}.jpg`}>JPEG</a></div></>}</article>)}</div>
  {row?.job?.error&&<p role="alert">{row.job.error}</p>}</div>;
}

type StoryPanelProps = {paperId:string;projectId?:string;version:number;onError:(s:string)=>void;refresh:()=>void;openLesson:(id:string)=>void};

function StoryStudio({papers,lessonPaperIds,selectedPaperId,...props}:Omit<StoryPanelProps,"paperId">&{papers:Row[];lessonPaperIds:string[];selectedPaperId:string}) {
  const [selected,setSelected]=useState(selectedPaperId),[query,setQuery]=useState(""),[showOthers,setShowOthers]=useState(false);
  const activeIds=new Set(lessonPaperIds);
  const current=papers.filter(p=>activeIds.has(p.id)||p.id===selectedPaperId);
  const offered=(showOthers||!current.length?papers:current).filter(p=>`${p.title} ${p.source_id}`.toLowerCase().includes(query.trim().toLowerCase()));
  useEffect(()=>{if(selectedPaperId){setQuery("");setSelected(selectedPaperId);}},[selectedPaperId]);
  useEffect(()=>{if(!offered.some(p=>p.id===selected))setSelected(offered[0]?.id||"");},[offered,selected]);
  if(!papers.length)return <p className="notice">まだ論文がありません。arXiv・PDFを取り込むか、「論文を探す」から選んでください。</p>;
  return <section className="story-studio">
    <label>保存済みの論文を検索<input type="search" value={query} onChange={e=>setQuery(e.target.value)} placeholder="論文名・arXiv ID…"/></label>
    <div className="paper-picker-tools"><label className="check-label"><input type="checkbox" checked={showOthers} onChange={e=>setShowOthers(e.target.checked)}/> 参考文献・未作成の論文も表示</label><small>{offered.length}本</small></div>
    {offered.length?<label>動画を作る論文<select value={selected} onChange={e=>setSelected(e.target.value)}>{offered.map(p=><option key={p.id} value={p.id}>{p.title}</option>)}</select></label>:<p>条件に合う保存済み論文がありません。</p>}
    {selected&&<StoryProjectPanel paperId={selected} {...props}/>}
  </section>;
}

function StoryProjectPanel({paperId,projectId,version,onError,refresh,openLesson}:StoryPanelProps) {
  const [project,setProject] = useState<any>(null);
  const [busy,setBusy] = useState(false), [loading,setLoading] = useState(true);
  useEffect(() => { setProject(null); setLoading(true); }, [paperId,projectId]);
  useEffect(() => {
    let alive=true;
    const load=async()=> {
      const id=projectId || (await api<any[]>("/video-projects")).find(p=>p.paper_id===paperId)?.id;
      const value=id ? await api<any>(`/video-projects/${id}`) : null;
      if(alive)setProject(value);
    };
    load().catch(e=>{if(alive)onError(e.message);}).finally(()=>{if(alive)setLoading(false);});
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
    <div className="story-heading"><div><h2>概要解説と詳細解説</h2><p>概要はアイデアを、詳細は原理・数式を解説します。図解と英日字幕、同じ会話の英語教材も一緒に作ります。</p></div>
      {!project ? <button className="primary" disabled={busy||loading} onClick={create}>{loading?"読み込み中…":busy?"開始中…":"解説・詳解動画を作る"} <Play size={16}/></button>
      : <div className="actions"><Badge state={project.state}>{project.state==="ready"?"2本の動画が完成":project.job?.stage || "2本の動画を準備中"}</Badge>
        {['queued','running'].includes(project.job?.state) && <button className="secondary" onClick={()=>control('pause')}>一時停止</button>}
        {['paused','failed','cancelled'].includes(project.job?.state) && <button className="primary" onClick={()=>control('resume')}>続きから再開</button>}</div>}
    </div>
    {project && <>
      {project.job?.error && <p role="alert">{project.job.error}</p>}
      <progress max={1} value={project.job?.progress || 0}/>
      <div className="story-film-grid">{Object.entries(project.data.modes || {}).map(([mode,entry])=> {
        const track=entry as any;
        const complete=track.videos?.find((v:any)=>v.kind===mode&&v.state==='ready');
        const preview=track.videos?.find((v:any)=>v.kind===`${mode}_preview`&&v.state==='ready');
        const visible=complete||preview;
        return <article className="story-film" key={mode}><div className="eyebrow">{mode==='overview'?'概要解説 · 数式なし':'詳細解説 · 数式と原理'}</div>
          <h3>{track.packaging?.title || track.label}</h3>{complete ? <p>{minutes(complete.data.duration)} · 完成</p> : <StoryGenerationProgress track={track}/>}
          {visible && <video controls preload="metadata" poster={fileUrl(visible.data.thumbnail)} src={fileUrl(visible.data.mp4)} />}
          {preview&&!complete&&<small>冒頭約90秒のプレビューです。全体の作成は続いています。</small>}
          <div className="actions">{complete&&<a className="primary" href={fileUrl(complete.data.mp4)} download={`${complete.data.title}.mp4`}>MP4をダウンロード</a>}
            {visible&&<a className="secondary" href={fileUrl(visible.data.thumbnail)} download={`${visible.data.title}.png`}>サムネイルをダウンロード</a>}
            <button className="secondary" onClick={()=>openLesson(track.lesson_id)}>この会話で英語練習</button></div>
          <details><summary>サムネイルを選ぶ・作り直す</summary><ThumbnailChoices projectId={project.id} mode={mode} track={track} onError={onError} refresh={refresh}/></details>
          {track.packaging&&<details><summary>投稿タイトル・説明文</summary>
            {track.packaging.candidates.map((c:any,i:number)=><div className="story-title" key={i}><span>{c.title_ja}</span><button className="text-button" onClick={()=>copy(c.title_ja)}>コピー</button></div>)}
            <textarea aria-label={`${track.label} YouTube description`} readOnly value={complete?.data.description || track.packaging.description || "動画の完成時に説明文とタイムスタンプを用意します。"}/>
            <button className="secondary" onClick={()=>copy(complete?.data.description || track.packaging.description || '')}>説明文をコピー</button></details>}
          {!!track.scenes?.length&&<details><summary>脚本・確認結果</summary>{track.scenes.map((s:any,i:number)=><div className="story-script" key={i}><h4>{s.title} / {s.title_ja}</h4>{s.utterances.map((u:any)=><p key={u.id}><strong>{u.speaker==='guide'?'Maya':'Aiden'}:</strong> {u.text}</p>)}{Object.entries(s.reviews||{}).map(([kind,value])=>{const review=value as any;return <small className="story-review" key={kind}>{kind==='content'?'Content · 内容':'Editing · 編集'}: {review.status||'checking'}{review.history?.at(-1)?.notes&&` — ${review.history.at(-1).notes}`}</small>;})}</div>)}</details>}
          {!!track.expressions?.length&&<details><summary>使える英語表現</summary>{track.expressions.map((e:any)=><p key={e.phrase}><strong>{e.phrase}</strong> — {e.meaning_ja}<br/>{e.usage_en}</p>)}</details>}
        </article>;
      })}</div>
      {!!project.data.warnings?.length&&<details><summary>作成時の補足 ({project.data.warnings.length})</summary>{project.data.warnings.map((w:any,i:number)=><p key={i}>{w.reason} · {w.action}</p>)}</details>}
      <p className="subtle">完成した動画はYouTube Studioへ手動でアップロードできます。英語と日本語の字幕は動画に直接入ります。</p>
    </>}
  </section>;
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
