import { useEffect, useRef, useState } from "react";
import { ArrowRight, Film, Plus, X } from "lucide-react";
import { api, fileUrl, minutes } from "./api";

type Video = {
  id: string;
  lesson_id: string;
  paper_title: string;
  kind: string;
  label: string;
  completed_at: number;
  data: {
    title: string;
    mp4: string;
    thumbnail?: string;
    thumbnail_jpg?: string;
    description?: string;
    duration?: number;
    bytes: number;
    conference?: string;
    awards?: { label?: string; name: string; venue: string; year: number }[];
  };
  revisions: Video[];
};

const day = (time: number) => new Date(time * 1000).toLocaleDateString("ja-JP", {
  timeZone: "Asia/Tokyo", year: "numeric", month: "long", day: "numeric",
});
const clock = (time: number) => new Date(time * 1000).toLocaleTimeString("ja-JP", {
  timeZone: "Asia/Tokyo", hour: "2-digit", minute: "2-digit", hour12: false,
});
const size = (bytes: number) => `${Math.round(bytes / 1_000_000)} MB`;

type Props = {
  version: number;
  onError: (message: string) => void;
  openLesson: (id: string) => void;
  addPaper: () => void;
  discover: () => void;
};

export function VideoLibrary({ version, onError, openLesson, addPaper, discover }: Props) {
  const [videos, setVideos] = useState<Video[] | null>(null);
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("films");
  const [order, setOrder] = useState("newest");
  const [selected, setSelected] = useState<Video | null>(null);
  useEffect(() => {
    let live = true;
    api<Video[]>("/videos").then(rows => { if (live) setVideos(rows); })
      .catch(error => { if (live) onError(error.message); });
    return () => { live = false; };
  }, [version]);

  const filtered = (videos || []).filter(video =>
    (kind === "films" ? video.kind !== "chapter" : video.kind === kind) &&
    `${video.paper_title} ${video.data.title} ${video.data.conference || ""}`
      .toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()),
  );
  if (order === "oldest") filtered.reverse();
  const groups = new Map<string, Video[]>();
  for (const video of filtered) {
    const key = day(video.completed_at);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(video);
  }
  const filmCount = videos?.filter(v => v.kind !== "chapter").length;

  return <section className="video-library" aria-label="作成した動画">
    <div className="video-library-heading">
      <div><div className="eyebrow">YOUR VIDEO LIBRARY</div><h1>作成した動画</h1>
        <p>完成日時で並ぶ動画一覧。再生、ダウンロード、英語練習をここから。</p></div>
      <div className="actions">
        <a className="secondary" href="#nightly-videos">動画の作成・進捗 <ArrowRight size={16} /></a>
        <button className="secondary" onClick={addPaper}><Plus size={16} /> Add a paper</button>
        <button className="text-button" onClick={discover}>論文を探す <ArrowRight size={16} /></button>
      </div>
    </div>
    <div className="video-library-tools">
      <label className="video-library-search">論文名・タイトル・学会で検索
        <input type="search" placeholder="LoRA、Muninn、RSS…" value={query} onChange={e => setQuery(e.target.value)} />
      </label>
      <label>動画の種類<select aria-label="動画の種類" value={kind} onChange={e => setKind(e.target.value)}>
        <option value="films">全体の動画</option><option value="overview">概要解説</option>
        <option value="deep_dive">詳細解説</option>
        {videos?.some(v => v.kind === "full") && <option value="full">全章まとめ</option>}
        {videos?.some(v => v.kind === "chapter") && <option value="chapter">章別の動画</option>}
      </select></label>
      <label>並び順<select aria-label="並び順" value={order} onChange={e => setOrder(e.target.value)}>
        <option value="newest">新しい順</option><option value="oldest">古い順</option>
      </select></label>
    </div>
    <p className="video-library-count">{videos === null ? "動画を読み込み中…" : `${filtered.length}本を表示 · 完成した全体の動画 ${filmCount}本`}<span>日時は日本時間</span></p>
    {Array.from(groups).map(([date, rows]) => <section className="video-library-day" key={date} aria-label={`${date}の動画`}>
      <h2>{date}</h2>
      <ol>{rows.map(video => <li key={video.id}>
        <article className="video-library-item" data-video-id={video.id} data-completed-at={video.completed_at}>
          <time dateTime={new Date(video.completed_at * 1000).toISOString()}>{clock(video.completed_at)}<small>完成</small></time>
          <button className="video-library-poster" aria-label={`${video.paper_title} ${video.label}を再生`} onClick={() => setSelected(video)}>
            {video.data.thumbnail ? <img loading="lazy" src={fileUrl(video.data.thumbnail)} alt={`${video.paper_title} ${video.label}のサムネイル`} /> : <Film size={36} />}
            <span>▶ 再生</span>
          </button>
          <div className="video-library-content">
            <div className="video-library-meta"><span className={`video-edition ${video.kind}`}>{video.label}</span>
              {video.data.conference && <span>{video.data.conference}</span>}
              {video.data.duration != null && <span>{minutes(video.data.duration)}</span>}<span>{size(video.data.bytes)}</span>
            </div>
            <h3>{video.paper_title}</h3>
            {!!video.data.awards?.length && <p className="video-library-awards">{video.data.awards.map(a => a.label || `${a.venue} ${a.year} ${a.name}`).join(" · ")}</p>}
            <div className="actions">
              <a className="primary" href={fileUrl(video.data.mp4)} download={`${video.data.title}.mp4`}>MP4をダウンロード</a>
              {video.data.thumbnail && <a className="secondary" href={fileUrl(video.data.thumbnail)} download={`${video.data.title}-thumbnail.png`}>サムネイル</a>}
              <button className="text-button" onClick={() => setSelected(video)}>タイトル・説明</button>
              <button className="text-button" onClick={() => openLesson(video.lesson_id)}>英語練習</button>
            </div>
            {video.revisions.length > 0 && <details className="video-library-revisions"><summary>以前の版（{video.revisions.length}本）</summary>
              {video.revisions.map(previous => <div key={previous.id}>
                <span>{day(previous.completed_at)} {clock(previous.completed_at)}{previous.data.duration != null && ` · ${minutes(previous.data.duration)}`}</span>
                <button className="text-button" onClick={() => setSelected(previous)}>再生・投稿情報</button>
                <a href={fileUrl(previous.data.mp4)} download={`${previous.data.title}.mp4`}>MP4をダウンロード</a>
              </div>)}
            </details>}
          </div>
        </article>
      </li>)}</ol>
    </section>)}
    {videos !== null && !filtered.length && <p className="video-library-empty">{query ? "条件に合う動画がありません。" : "この種類の完成動画はまだありません。"}</p>}
    {selected && <VideoDetails key={selected.id} video={selected} close={() => setSelected(null)} onError={onError} />}
  </section>;
}

function VideoDetails({ video, close, onError }: { video: Video; close: () => void; onError: (message: string) => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [copied, setCopied] = useState("");
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  const copy = async (text: string, label: string) => {
    try { await navigator.clipboard.writeText(text); setCopied(label); }
    catch { onError("コピーできませんでした。文字を選択してコピーしてください。"); }
  };
  return <dialog ref={dialog} className="video-library-dialog" aria-label={`${video.label}の再生と投稿情報`}
    onCancel={close} onClose={close} onClick={e => { if (e.target === dialog.current) close(); }}>
    <button className="modal-close" aria-label="動画を閉じる" onClick={close}><X size={20} /></button>
    <p className="video-library-meta">{video.label} · {day(video.completed_at)} {clock(video.completed_at)}完成</p>
    <h2>{video.paper_title}</h2>
    <video controls preload="metadata" poster={video.data.thumbnail ? fileUrl(video.data.thumbnail) : undefined} src={fileUrl(video.data.mp4)} />
    <label>YouTube用タイトル<textarea readOnly value={video.data.title} rows={2} onFocus={e => e.currentTarget.select()} /></label>
    <button className="secondary" onClick={() => copy(video.data.title, "タイトル")}>タイトルをコピー</button>
    {video.data.description && <><label>YouTube用説明文<textarea readOnly rows={8} value={video.data.description} onFocus={e => e.currentTarget.select()} /></label>
      <button className="secondary" onClick={() => copy(video.data.description!, "説明文")}>説明文をコピー</button></>}
    {copied && <p role="status">{copied}をコピーしました。</p>}
  </dialog>;
}
