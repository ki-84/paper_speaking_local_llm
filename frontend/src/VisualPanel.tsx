import { useEffect, useRef, useState } from "react";
import { fileUrl, Row } from "./api";

export type VisualCue = { key: string; focus?: string[] } | null;
export type VisualReference = { key: string; asset_id: string };

export function VisualPanel({ references, assets, cue, mode, selected, onSelect, onSource }: {
  references: VisualReference[];
  assets: Row[];
  cue: VisualCue;
  mode: "auto" | "pinned";
  selected: string | null;
  onSelect: (mode: "auto" | "pinned", key: string | null) => void;
  onSource: (id: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [fullPage, setFullPage] = useState(false);
  const [zoom, setZoom] = useState(1);
  const zoomView = useRef<HTMLDivElement>(null);
  const key = mode === "pinned" ? selected : cue?.key;
  const ref = references.find(r => r.key === key);
  const asset = assets.find(a => a.id === ref?.asset_id);
  useEffect(() => {
    setFullPage(false);
  }, [asset?.id]);
  useEffect(() => { setZoom(1); }, [expanded, asset?.id]);
  useEffect(() => {
    if (!expanded) return;
    const escape = (e: KeyboardEvent) => { if (e.key === "Escape") setExpanded(false); };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [expanded]);
  if (!references.length) return null;
  const data = asset?.data;
  const focus = cue?.key === key && !fullPage ? cue?.focus || [] : [];
  const focusRegion = () => {
    const view = zoomView.current, img = view?.querySelector("img");
    const region = data?.regions?.find((r: any) => focus.includes(r.id));
    if (!view || !img || !region) return;
    const center = region.box ? [region.box[0]+region.box[2]/2, region.box[1]+region.box[3]/2]
      : region.points.reduce((sum: number[], p: number[]) => [sum[0]+p[0]/region.points.length, sum[1]+p[1]/region.points.length], [0,0]);
    const image = img.getBoundingClientRect(), frame = view.getBoundingClientRect();
    view.scrollBy({left: image.left+image.width*center[0]-frame.left-view.clientWidth/2,
      top: image.top+image.height*center[1]-frame.top-view.clientHeight/2, behavior:"smooth"});
  };
  const picture = (large: boolean) => data && (
    <div className={`visual-image-wrap ${large ? "large" : ""}`} style={large ? {transform:`scale(${zoom})`, transformOrigin:"top left"} : undefined}>
      <img src={fileUrl(fullPage && data.full_page_path ? data.full_page_path : data.image_path)} alt={`${data.title_en}: ${data.description_en}`} />
      {data.regions?.filter((r: any) => focus.includes(r.id)).map((r: any) => (
        r.points ? <svg key={r.id} className="visual-focus-path" viewBox="0 0 1 1" preserveAspectRatio="none" aria-label={`${r.label_en} / ${r.label_ja}`}><polyline points={r.points.map((p: number[]) => p.join(",")).join(" ")} /></svg>
        : <span key={r.id} className="visual-highlight" title={`${r.label_en} / ${r.label_ja}`} style={{ left: `${r.box[0]*100}%`, top: `${r.box[1]*100}%`, width: `${r.box[2]*100}%`, height: `${r.box[3]*100}%` }} />
      ))}
    </div>
  );
  return <section className="visual-panel" aria-label="Lesson visuals">
    <div className="visual-toolbar">
      <strong>Look & understand <span lang="ja">· 図で理解する</span></strong>
      <button className={mode === "auto" ? "text-button active" : "text-button"} aria-pressed={mode === "auto"} onClick={() => onSelect("auto", null)}>Auto · 自動表示</button>
      {asset && <button className="text-button" aria-pressed={mode === "pinned"} onClick={() => onSelect(mode === "pinned" ? "auto" : "pinned", mode === "pinned" ? null : key!)}>{mode === "pinned" ? "Unpin · 固定を解除" : "Pin · この図を固定"}</button>}
    </div>
    {data ? <>
      <p className="visual-kind">{asset.kind === "original" ? `${data.label} · 論文の原図 · p.${data.page}` : asset.kind === "example" ? "Hypothetical example · 仮の例" : "Learning diagram · 説明用の補助図"}</p>
      <h3>{data.title_en}</h3><p className="japanese-aid" lang="ja">{data.title_ja}</p>
      <div className="visual-picture">{picture(false)}</div>
      <p>{data.description_en}</p><p className="japanese-aid" lang="ja">{data.description_ja}</p>
      <div className="visual-toolbar">
        <button className="secondary" onClick={() => setExpanded(true)}>Enlarge · 拡大</button>
        {data.full_page_path && <button className="text-button" aria-pressed={fullPage} onClick={() => setFullPage(!fullPage)}>{fullPage ? "Figure · 図に戻る" : "Original page · 原ページ"}</button>}
        {data.source_ids?.map((sid: string, i: number) => <button key={sid} className="text-button" onClick={() => onSource(sid)}>Evidence {i+1} · 出典</button>)}
      </div>
      {data.terms?.length > 0 && <details><summary>Figure labels · 図の用語</summary><dl className="visual-terms">{data.terms.map((t: any, i: number) => <div key={i}><dt>{t.en}</dt><dd lang="ja">{t.ja}</dd></div>)}</dl></details>}
      {asset.kind === "original" && data.extraction === "page_fallback" && <p className="subtle">The full page keeps the figure complete. · 図の欠落を避けるためページ全体を表示しています。</p>}
    </> : <p className="subtle">No figure for this sentence. You can choose one below. <span lang="ja">この文に対応する図はありません。下から選ぶこともできます。</span></p>}
    <div className="visual-choices" aria-label="Choose a figure">
      {references.map(r => {
        const a = assets.find(x => x.id === r.asset_id);
        if (!a?.data.image_path || !a.data.review?.passed) return null;
        return <button key={r.key} className={r.key === key ? "selected" : ""} aria-pressed={r.key === key} onClick={() => onSelect("pinned", r.key)}>
          <img src={fileUrl(a.data.image_path)} alt="" loading="lazy" /><span>{a.data.title_en}<small lang="ja">{a.data.title_ja}</small></span>
        </button>;
      })}
    </div>
    {expanded && data && <div className="visual-modal" role="dialog" aria-modal="true" aria-label="Enlarged lesson figure" onClick={() => setExpanded(false)}>
      <div onClick={e => e.stopPropagation()}>
        <div className="visual-toolbar">
          <label>Zoom · 拡大率 <select aria-label="Figure zoom" value={zoom} onChange={e => setZoom(Number(e.target.value))}>{[1,1.5,2,3].map(n => <option key={n} value={n}>{n*100}%</option>)}</select></label>
          {focus.length > 0 && <button className="secondary" onClick={focusRegion}>Focus here · 注目箇所へ</button>}
          <button className="secondary" autoFocus onClick={() => setExpanded(false)}>Close · 閉じる</button>
        </div>
        <div className="visual-zoom-view" ref={zoomView}>{picture(true)}</div>
        <p>{data.title_en} · {data.title_ja}</p>
        <p className="subtle">Scroll to explore the enlarged figure. · 拡大した図はスクロールできます。</p>
      </div>
    </div>}
  </section>;
}
