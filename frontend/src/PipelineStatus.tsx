import { useEffect, useState } from "react";
import { api } from "./api";

const clock = (seconds: number) => new Date(seconds * 1000).toLocaleTimeString("ja-JP", {
  timeZone: "Asia/Tokyo", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
});

function relatedJobs(run: any): any[] {
  const jobs = [run?.job, run?.continuing_run?.job, run?.project?.job];
  for (const track of Object.values(run?.project?.data?.modes || {}) as any[]) {
    jobs.push(track.thumbnails?.job);
    jobs.push(...(track.videos || []).map((video: any) => video.job));
  }
  return jobs.filter(Boolean);
}

export function PipelineStatus({ run }: { run: any }) {
  const [health, setHealth] = useState<any>(null);
  const [disconnected, setDisconnected] = useState(false);
  useEffect(() => {
    let live = true;
    let pending = false;
    let request: AbortController | null = null;
    const read = async () => {
      if (pending) return;
      pending = true;
      const controller = new AbortController();
      request = controller;
      const timeout = setTimeout(() => controller.abort(), 10000);
      try {
        const result = await api("/pipeline-health", { signal: controller.signal });
        if (live) { setHealth(result); setDisconnected(false); }
      } catch {
        if (live) setDisconnected(true);
      } finally { clearTimeout(timeout); pending = false; request = null; }
    };
    void read();
    const timer = setInterval(read, 15000);
    return () => { live = false; clearInterval(timer); request?.abort(); };
  }, [run.id]);

  const active = run.continuing_run || run;
  const controller = run.project?.job || active.job;
  const stopped = [run.job, run.project?.job, run.continuing_run?.job].some(job => ["paused", "cancelled"].includes(job?.state));
  const jobs = relatedJobs(run);
  const worker = health?.worker;
  const fresh = worker?.heartbeat && Date.now() / 1000 - worker.heartbeat < 90;
  const workingHere = fresh && jobs.some(job => job.id === worker.job_id);
  const issues = (health?.["pipeline-health"]?.issues || []).filter((issue: any) => jobs.some(job => job.id === issue.job_id));
  const recoveries = (health?.["pipeline-health"]?.history || []).filter((item: any) => jobs.some(job => job.id === item.job_id));
  let text = "処理状況を確認しています";
  if (run.state === "ready") text = "完成しています";
  else if (stopped || ["paused", "cancelled"].includes(active.state)) text = "利用者による停止を保持しています";
  else if (disconnected) text = "接続を再確認中 · 状況は自動で更新します";
  else if (issues.length || controller?.state === "failed") text = "作成時の問題を確認できます";
  else if (run.state === "waiting") text = "前日分の完成後に自動で開始します";
  else if (run.state === "skipped") text = "この日の追加作成は見送りです";
  else if (workingHere) text = "処理中 · サーバーから応答があります";
  else if (fresh) text = "処理待ち · サーバーから応答があります";
  else if (health) text = "サーバーの応答を再確認中 · 復旧状況を自動更新します";

  const updates = Object.values(run.project?.data?.modes || {}).map((track: any) => track.generation_progress?.updated || 0);
  const progressAt = Math.max(0, ...updates);
  return <div className="pipeline-status" role="group" aria-label="作成の稼働確認">
    <p role="status">{text}</p>
    <p className="subtle">
      {worker?.heartbeat && <>サーバー最終応答 {clock(worker.heartbeat)}（日本時間）</>}
      {progressAt > 0 && <> · 保存した進捗の更新 {clock(progressAt)}</>}
    </p>
    {recoveries.length > 0 && <p className="subtle">保存済み工程からの自動復旧を{recoveries.length}回確認しました。</p>}
    {issues.length > 0 && <details><summary>この作成の確認事項</summary>{issues.map((issue: any, index: number) => <p key={index}>{issue.reason}</p>)}</details>}
  </div>;
}
