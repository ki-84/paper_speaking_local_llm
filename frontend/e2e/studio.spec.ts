import { test, expect } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await page.locator(".login-page, .learning-home").first().waitFor();
  if (await page.getByLabel("Studio password").isVisible()) {
    await page.getByLabel("Studio password").fill("ui-test-only");
    await page.getByRole("button", { name: "Come on in" }).click();
  }
  await page.locator('nav').getByRole("button", {name:"英語練習",exact:true}).click();
  await expect(
    page.getByRole("button", { name: /Interface test: a small change/ }),
  ).toBeVisible();
  const ls = await (await page.request.get("/api/lessons")).json();
  const interfaceLesson = ls.find((lesson: any) => lesson.data.title === "Interface test: a small change");
  await page.request.put(`/api/lessons/${interfaceLesson.id}/progress`, {
    data: { chapter_id: "test-chapter", turn_index: 0 },
  });
  await page
    .getByRole("button", { name: /Interface test: a small change/ })
    .click();
  await expect(
    page
      .getByText(
        "The model learns a small change instead of changing every weight.",
        { exact: true },
      )
      .first(),
  ).toBeVisible();
});
test("home resumes the course and records explicitly studied chapters", async ({page})=>{
  const lesson=(await(await page.request.get('/api/lessons')).json()).find((l:any)=>l.data.title==='Interface test: a small change');
  await page.getByRole('button',{name:'Next sentence',exact:true}).click();
  await expect.poll(async()=> (await(await page.request.get(`/api/lessons/${lesson.id}`)).json()).progress.turn_index).toBe(1);
  await page.getByRole('button',{name:'この章を学習済みにする',exact:true}).click();
  await expect(page.getByRole('button',{name:'✓ 学習済み · 取り消す',exact:true})).toBeVisible();
  await page.locator('nav').getByRole('button',{name:'ホーム',exact:true}).click();
  const home=page.getByRole('region',{name:'学習ホーム',exact:true});
  await expect(home.getByRole('heading',{name:'今日の学習',exact:true})).toBeVisible();
  const card=home.locator('.home-course').filter({has:page.getByRole('heading',{name:'Interface test: a small change',exact:true})});
  await expect(card.getByText(/学習済み 1 \/ 2章/)).toBeVisible();
  await expect(card.getByText(/2文目/)).toBeVisible();
  await card.getByRole('button',{name:'続きから学ぶ'}).click();
  await expect(page.locator('.spoken-sentence')).toHaveText('The old weights stay fixed.');
  await page.locator('nav').getByRole('button',{name:'ホーム',exact:true}).click();
  await page.setViewportSize({width:390,height:844});
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBeTruthy();
  await page.screenshot({path:'../data/evaluation/learning-home-mobile.png',fullPage:true});
});

test("recall rating changes the schedule without overwriting the course resume point",async({page})=>{
  const lesson=(await(await page.request.get('/api/lessons')).json()).find((l:any)=>l.data.title==='Interface test: a small change');
  await page.getByRole('button',{name:'Next sentence',exact:true}).click();
  await expect.poll(async()=> (await(await page.request.get(`/api/lessons/${lesson.id}`)).json()).progress.turn_index).toBe(1);
  await page.locator('nav').getByRole('button',{name:'ホーム',exact:true}).click();
  await page.getByRole('button',{name:'復習を始める',exact:true}).click();
  await page.getByRole('button',{name:'録音して練習',exact:true}).first().click();
  const review=page.getByRole('article',{name:'思い出す練習',exact:true});
  await expect(review.getByRole('button',{name:'答えを確認する'})).toBeVisible();
  await expect(page.locator('.spoken-sentence')).toContainText('Listen first.');
  await review.getByRole('button',{name:'答えを確認する'}).click();
  await expect(review.getByRole('button',{name:/難しい/})).toBeEnabled();
  await review.getByRole('button',{name:/難しい/}).click();
  await expect(review.getByRole('status')).toContainText('記録しました');
  const saved=await(await page.request.get(`/api/lessons/${lesson.id}`)).json();
  expect(saved.progress.turn_index).toBe(1);
  await page.screenshot({path:'../data/evaluation/fsrs-review-session.png',fullPage:true});
  await page.locator('nav').getByRole('button',{name:'ホーム',exact:true}).click();
  const home=page.getByRole('region',{name:'学習ホーム',exact:true});
  await expect(home.getByText(/今日の復習/)).toBeVisible();
});

test("video library shows completed films by date with filters and folded revisions", async ({ page }) => {
  const row = (id: string, kind: string, completed: number, paper = "A useful robot paper") => ({
    id, kind, completed_at: completed, lesson_id: "test-lesson", paper_title: paper,
    label: ({ overview: "概要解説", deep_dive: "詳細解説", full: "全章まとめ", chapter: "第1章" } as Record<string, string>)[kind],
    data: { title: `${paper} ${kind}`, mp4: `videos/${id}.mp4`, thumbnail: "visuals/ui-original.png", duration: 120, bytes: 1000000, conference: "RSS 2026", description: "English practice with this paper." },
    revisions: [] as any[],
  });
  const overview = row("overview", "overview", 1791080000);
  overview.revisions = [row("old-render", "overview", 1790990000)];
  await page.route("**/api/videos", route => route.fulfill({ json: [
    row("deep", "deep_dive", 1791087000), overview,
    row("older-full", "full", 1790920000, "Earlier LoRA lesson"),
    row("chapter", "chapter", 1790830000),
  ] }));
  await page.locator('nav').getByRole("button", { name: "動画一覧", exact: true }).click();
  const panel = page.getByRole("region", { name: "作成した動画", exact: true });
  const ids = () => panel.locator(".video-library-item").evaluateAll(items => items.map(el => el.getAttribute("data-video-id")));
  await expect.poll(ids).toEqual(["deep", "overview"]);
  await expect(panel.locator(".video-library-day h2").first()).toHaveText("2026年10月4日");
  await expect(panel.getByText("日時は日本時間")).toBeVisible();
  await panel.getByText("以前の版（1本）", { exact: true }).click();
  await expect(panel.getByRole("link", { name: "MP4をダウンロード", exact: true })).toHaveCount(3);
  await expect(panel.locator(".video-library-revisions a")).toHaveAttribute("href", "/api/files/videos/old-render.mp4");
  await panel.getByLabel("並び順", { exact: true }).selectOption("oldest");
  await expect.poll(ids).toEqual(["overview", "deep"]);
  await panel.getByLabel("論文名・タイトル・学会で検索").fill("RSS");
  await expect.poll(ids).toEqual(["overview", "deep"]);
  await panel.getByLabel("論文名・タイトル・学会で検索").fill("");
  await panel.getByLabel("動画の種類", { exact: true }).selectOption("deep_dive");
  await expect.poll(ids).toEqual(["deep"]);
  await panel.getByRole("button", { name: "タイトル・説明", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "詳細解説の再生と投稿情報" });
  await expect(dialog.getByLabel("YouTube用タイトル")).toHaveValue("A useful robot paper deep_dive");
  await expect(dialog.getByLabel("YouTube用説明文")).toHaveValue("English practice with this paper.");
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(panel.getByRole("option", {name:"章別の動画"})).toHaveCount(0);
  await expect(panel.getByRole("option", {name:"全章まとめ"})).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
});
test("Japanese paper search shows titles, abstracts, and a selectable paper", async ({ page }) => {
  await page.locator('nav').getByRole("button", { name: "論文を探す" }).click();
  await expect(page.getByRole("heading", { name: "次に読みたい論文を探す。" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "初めて見る物をつかむロボット", exact: true })).toBeVisible();
  await expect(page.getByText("未知の物をつかむ学習について読めます。")).toBeVisible();
  await expect(page.getByRole("heading", { name: "本文を確認したおすすめ" })).toHaveCount(0);
  const beforeJobs=await (await page.request.get("/api/jobs")).json();
  await page.getByRole("button", { name: "解説・詳解動画を作る",exact:true }).click();
  await expect(page.getByRole("region", {name:"解説・詳解動画",exact:true})).toBeVisible();
  await expect.poll(async () => {
    const lessons = await (await page.request.get("/api/lessons")).json();
    return lessons.filter((lesson: any) => lesson.data.format === "paper-story-1" && lesson.data.title.includes("A Robot That Learns to Grasp")).length;
  }).toBe(2);
  const afterJobs=await (await page.request.get("/api/jobs")).json();
  expect(afterJobs.filter((j:any)=>j.kind==='lesson').map((j:any)=>j.id)).toEqual(beforeJobs.filter((j:any)=>j.kind==='lesson').map((j:any)=>j.id));
  await page.locator('nav').getByRole("button", { name: "論文を探す" }).click();
  await page.getByLabel("どんな論文を読みたいですか？").fill("触覚を使うロボットの論文");
  await page.getByRole("button", { name: "日本語で論文を探す" }).click();
  await expect(page.getByRole("heading", { name: "「触覚を使うロボットの論文」の検索結果" })).toBeVisible();
});
test("source, audio, question hints, and saved position", async ({ page }) => {
  await page.getByRole("button", { name: "Source 1" }).click();
  await expect(
    page.getByText("Original evidence for the interface test."),
  ).toBeVisible();
  await page.getByRole("button", { name: "Close source" }).click();
  await page.getByRole("button", { name: "Hear it", exact: true }).click();
  await expect
    .poll(() =>
      page
        .locator(".sentence-card audio")
        .evaluate((a: HTMLAudioElement) => a.currentTime),
    )
    .toBeGreaterThan(0);
  await page.getByRole("button", { name: "Next sentence" }).click();
  await expect(page.locator(".spoken-sentence")).toHaveText(
    "The old weights stay fixed.",
  );
  await page.reload();
  await page.locator('nav').getByRole("button", {name:"英語練習",exact:true}).click();
  await page
    .getByRole("button", { name: /Interface test: a small change/ })
    .click();
  await expect(page.locator(".spoken-sentence")).toHaveText(
    "The old weights stay fixed.",
  );
  await page.locator(".question").filter({ hasText: "What stays fixed" }).click();
  await page.getByRole("button", { name: "A little help" }).click();
  await expect(page.getByText("Think about the old model.")).toBeVisible();
  await page.screenshot({
    path: "../data/evaluation/study-ui.png",
    fullPage: true,
  });
});
test("Japanese aid keeps English and the source visible", async ({ page }) => {
  await expect(page.getByRole("button", { name: "日本語訳を隠す" })).toBeVisible();
  await page.getByRole("button", { name: "Next sentence" }).click();
  await expect(page.getByText("元の重みは固定されたままです。").first()).toBeVisible();
  await expect(page.locator(".spoken-sentence")).toHaveText("The old weights stay fixed.");
  await page.getByRole("button", { name: "Source 1" }).click();
  await expect(page.getByText("Original evidence for the interface test.")).toBeVisible();
});
test("visuals follow speech and keep a pinned figure across reloads", async ({ page }) => {
  const panel = page.getByRole("region", { name: "Lesson visuals" });
  await expect(panel.getByRole("heading", { name: "An original figure" })).toBeVisible();
  await expect(panel.locator(".visual-highlight")).toHaveCount(1);
  await page.getByRole("button", { name: "Listen to chapter", exact: true }).click();
  // Exercise the same ended event as continuous audio, without waiting for the fixture audio.
  await page.locator(".sentence-card audio").dispatchEvent("ended");
  await expect(panel.getByRole("heading", { name: "What stays fixed" })).toBeVisible();
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  await page.getByRole("button", {name:"Next sentence", exact:true}).click();
  await expect(panel.getByRole("heading", {name:"What stays fixed", exact:true})).toBeVisible();
  await expect(panel.locator(".visual-highlight")).toHaveCount(0);
  await page.getByRole("button", {name:"Previous sentence", exact:true}).click();
  await panel.getByRole("button", { name: "An original figure 論文の原図" }).click();
  await expect(panel.getByRole("heading", { name: "An original figure" })).toBeVisible();
  const lesson = (await (await page.request.get("/api/lessons")).json()).find((l: any) => l.data.title === "Interface test: a small change");
  await expect.poll(async () => (await (await page.request.get(`/api/lessons/${lesson.id}`)).json()).progress.visual_key).toBe("V1");
  await page.reload();
  await page.locator('nav').getByRole("button", {name:"英語練習",exact:true}).click();
  await page.getByRole("button", { name: /Interface test: a small change/ }).click();
  await expect(panel.getByRole("heading", { name: "An original figure" })).toBeVisible();
  await panel.getByRole("button", { name: "Auto · 自動表示" }).click();
  await expect(panel.getByRole("heading", { name: "What stays fixed" })).toBeVisible();
  await panel.getByRole("button", { name: "Enlarge · 拡大" }).click();
  await expect(page.getByRole("dialog", { name: "Enlarged lesson figure" })).toBeVisible();
  await page.getByLabel("Figure zoom").selectOption("2");
  await expect.poll(() => page.locator(".visual-zoom-view").evaluate((el) => el.scrollWidth > el.clientWidth)).toBeTruthy();
  await page.getByRole("button", {name:"Focus here · 注目箇所へ"}).click();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
});
test("recording feedback explains a word to compare in English and Japanese", async ({ page }) => {
  await page.getByRole("button", { name: "Next sentence" }).click();
  await page.getByText("Your earlier recordings (1)").click();
  await page.getByRole("button", { name: "See feedback" }).click();
  const coach = page.getByRole("region", { name: "Words to check" });
  await expect(coach.getByRole("heading", { name: /Words to check/ })).toBeVisible();
  await expect(coach.getByText("fixed → mixed")).toBeVisible();
  await expect(coach.getByText(/Try \/f\/ in/)).toBeVisible();
  await expect(coach.getByText(/聞き比べてください/)).toBeVisible();
  await expect(coach.getByRole("button", { name: "Example · お手本" })).toBeEnabled();
  await expect(coach.getByRole("button", { name: "Your voice · 自分の声" })).toBeEnabled();
});
test("play all resets a pinned figure when the next chapter reuses its key", async ({page}) => {
  const panel = page.getByRole("region", {name:"Lesson visuals"});
  await panel.getByRole("button", {name:"Pin · この図を固定", exact:true}).click();
  await page.getByRole("button", {name:"Next sentence", exact:true}).click();
  await page.getByRole("button", {name:"Play all", exact:true}).click();
  await page.locator(".sentence-card audio").dispatchEvent("ended");
  await expect(page.locator(".spoken-sentence")).toHaveText("Can we keep that idea in mind?");
  await page.locator(".sentence-card audio").dispatchEvent("ended");
  await expect(page.locator(".chapters button.current")).toContainText("Keep the base");
  await expect(panel.getByRole("heading", {name:"What stays fixed", exact:true})).toBeVisible();
  await expect(panel.getByRole("button", {name:"Auto · 自動表示", exact:true})).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", {name:"Pause", exact:true}).click();
});
test("browser microphone saves a real recording and guards navigation", async ({
  page,
}) => {
  await page.getByRole("button", { name: "Check microphone" }).click();
  await expect(page.getByRole("progressbar", { name: "Microphone input level" })).toBeVisible();
  await expect.poll(() => page.getByRole("progressbar", { name: "Microphone input level" }).evaluate((meter: HTMLProgressElement) => meter.value)).toBeGreaterThan(0);
  await expect.poll(() => page.locator("#practice-microphone option").count()).toBeGreaterThan(1);
  await page.getByRole("button", { name: "Stop mic check" }).click();
  await page.getByRole("button", { name: "Your turn", exact: true }).click();
  await expect(page.getByRole("button", { name: /Done ·/ })).toBeVisible();
  await page.locator('nav').getByRole("button", { name: "設定", exact: true }).click();
  await expect(page.getByText("Finish your recording first.")).toBeVisible();
  await page.waitForTimeout(1200);
  await page.getByRole("button", { name: /Done ·/ }).click();
  await expect(
    page.getByText(
      "Your recording is saved. You can stay here while we check it.",
    ),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: "Your recording is in line…" })).toBeVisible();
  const response = await page.request.get("/api/lessons");
  const lesson = (await response.json()).find((item: any) => item.data.title === "Interface test: a small change");
  const detail = await (
    await page.request.get(`/api/lessons/${lesson.id}`)
  ).json();
  expect(detail.attempts.length).toBeGreaterThan(0);
  const audio = await page.request.get(
    "/api/files/" + detail.attempts[0].data.audio,
  );
  expect(audio.ok()).toBeTruthy();
  expect((await audio.body()).length).toBeGreaterThan(1000);
});
test("failed upload stays in this browser and can be sent again", async ({
  page,
}) => {
  await page.route("**/api/attempts", (route) =>
    route.abort("internetdisconnected"),
  );
  await page.getByRole("button", { name: "Your turn", exact: true }).click();
  await page.waitForTimeout(1200);
  await page.getByRole("button", { name: /Done ·/ }).click();
  await expect(
    page.getByText("A recording is saved in this browser.", { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Send saved recording" })).toBeEnabled();
  await page.unroute("**/api/attempts");
  await page.getByRole("button", { name: "Send saved recording" }).click();
  await expect(
    page.getByText(
      "Your recording is saved. You can stay here while we check it.",
    ),
  ).toBeVisible();
});

test("a stale saved microphone falls back to the system default", async ({ page }) => {
  await page.evaluate(() => localStorage.setItem("paperspeak-microphone", "missing-device-id"));
  await page.reload();
  await page.locator('nav').getByRole("button", {name:"英語練習",exact:true}).click();
  await page.getByRole("button", { name: /Interface test: a small change/ }).click();
  await expect(page.getByRole("option", { name: "Last selected microphone" })).toHaveCount(1);
  await page.getByRole("button", { name: "Your turn", exact: true }).click();
  await expect(page.getByRole("button", { name: /Done ·/ })).toBeVisible();
  await expect(page.locator("#practice-microphone")).toHaveValue("");
  await expect(page.getByText("Your saved microphone was unavailable. Using the system default microphone.")).toBeVisible();
  await page.waitForTimeout(1200);
  await page.getByRole("button", { name: /Done ·/ }).click();
});

test("a denied microphone gives a visible instruction beside the controls", async ({ page }) => {
  await page.evaluate(() => {
    navigator.mediaDevices.getUserMedia = async () => {
      throw new DOMException("Denied", "NotAllowedError");
    };
  });
  await page.getByRole("button", { name: "Your turn", exact: true }).click();
  await expect(page.locator(".mic-message")).toContainText("Microphone access is blocked");
  await expect(page.getByRole("button", { name: "Your turn", exact: true })).toBeEnabled();
});

test("a missing system microphone explains the problem and refreshes connected devices", async ({ page }) => {
  await page.evaluate(() => {
    navigator.mediaDevices.getUserMedia = async () => {
      throw new DOMException("No device", "NotFoundError");
    };
    navigator.mediaDevices.enumerateDevices = async () => [{
      kind: "audioinput", deviceId: "connected-device", label: "Connected microphone",
    } as MediaDeviceInfo];
  });
  await page.getByRole("button", { name: "Check microphone" }).click();
  await expect(page.locator(".mic-message")).toContainText("Chrome found no usable system microphone");
  await page.getByRole("button", { name: "Refresh microphones" }).click();
  await expect(page.getByRole("option", { name: "Connected microphone" })).toHaveCount(1);
});

test("nightly videos are independent of old lesson automation", async ({ page }) => {
  await page.locator('nav').getByRole("button", { name: "設定", exact: true }).click();
  await expect(page.getByRole("heading", { name: "夜間に解説・詳解を自動作成" })).toBeVisible();
  const old = await (await page.request.get('/api/settings')).json();
  await page.getByLabel("毎晩、注目論文から2本の動画と英語教材を作る").check();
  await page.getByLabel("最近のAI・ロボティクス学会の優秀論文賞・Test of Time賞を優先する").check();
  await page.getByLabel("開始時刻 · 日本時間").fill('02:15');
  await page.getByRole('button', {name:/設定を保存/}).click();
  await expect.poll(async()=> (await (await page.request.get('/api/settings')).json()).nightly_video_minute).toBe(15);
  const current = await (await page.request.get('/api/settings')).json();
  expect(current.discovery_enabled).toBe(old.discovery_enabled);
  expect(current.nightly_video_enabled).toBe(true);
  expect(current.nightly_video_awards_first).toBe(true);
  await page.request.put('/api/settings',{data:old});
});

test("library nightly start is idempotent and can pause and resume", async ({ page }) => {
  await page.locator('nav').getByRole('button',{name:'動画を作る',exact:true}).click();
  const panel=page.getByRole('region',{name:'自動選定と動画作成'});
  await expect(panel).toBeVisible();
  await panel.getByRole('button',{name:'今すぐ論文を選んで作る'}).click();
  await expect(panel.getByText('論文を探索中',{exact:true})).toBeVisible();
  await panel.getByRole('button',{name:'今すぐ論文を選んで作る'}).click();
  expect((await (await page.request.get('/api/nightly-video-runs')).json()).length).toBe(1);
  await panel.getByRole('button',{name:'一時停止',exact:true}).click();
  await expect(panel.getByRole('button',{name:'続きから再開'})).toBeVisible();
  await panel.getByRole('button',{name:'続きから再開'}).click();
  await expect(panel.getByText('論文を探索中',{exact:true})).toBeVisible();
});

test("nightly selection shows the verified conference award source", async ({page}) => {
  const source='https://roboticsconference.org/2026/program/awards/';
  await page.route('**/api/nightly-video-runs', route=>route.fulfill({json:[{id:'award-run',day:'2026-10-03',state:'ready',job:null,data:{selected:{title:'A useful robot method',awards:[{venue:'RSS',year:2026,name:'Outstanding Paper Award',official_url:source}]},search_plans:[{reason_ja:'候補の少ないロボティクスを補います。'}],search_history:[{category:'cs.RO',terms:['robot learning'],reason_ja:'新しい学習法を探します。',result_count:3}],timings:{}}}]}));
  await page.locator('nav').getByRole('button',{name:'動画を作る',exact:true}).click();
  const panel=page.getByRole('region',{name:'自動選定と動画作成'});
  await expect(panel.getByRole('link',{name:'RSS 2026 · Outstanding Paper Award'})).toHaveAttribute('href',source);
  await expect(panel.getByText(/公式受賞情報確認済み/)).toBeVisible();
  await panel.getByText('選定・作成の記録',{exact:true}).click();
  await expect(panel.getByText('ローカルAIの検索方針: 候補の少ないロボティクスを補います。')).toBeVisible();
  await expect(panel.getByText(/robot learning.*新しい学習法を探します。.*3候補/)).toBeVisible();
});

test("nightly backlog waiting is distinct from a skipped day and shows catch-up", async ({page}) => {
  let run:any={id:'today',day:'2026-10-06',state:'waiting',job:null,data:{reason:'前日分の完成後に今日の分を自動で開始します。',waiting_reason:'Deep dive is continuing'},continuing_run:{id:'previous',day:'2026-10-05',data:{selected:{title:'The previous paper'}},job:{id:'previous-job',state:'queued',stage:'Deep dive is continuing',progress:.9}}};
  await page.route('**/api/nightly-video-runs',route=>route.fulfill({json:[run]}));
  await page.locator('nav').getByRole('button',{name:'動画を作る',exact:true}).click();
  const panel=page.getByRole('region',{name:'自動選定と動画作成'});
  await expect(panel.getByText('前日分の完成待ち',{exact:true})).toBeVisible();
  await expect(panel.getByText('この日は見送り',{exact:true})).toHaveCount(0);
  await expect(panel.getByText('継続中: 2026-10-05の動画',{exact:true})).toBeVisible();
  await expect(panel.getByRole('button',{name:'一時停止',exact:true})).toBeVisible();
  run={id:'today',day:'2026-10-06',state:'searching',data:{phase:'attention'},job:{id:'today-job',state:'queued',stage:'Collecting attention',progress:0}};
  await page.locator('nav').getByRole('button',{name:'英語練習',exact:true}).click();
  await page.locator('nav').getByRole('button',{name:'動画を作る',exact:true}).click();
  await expect(panel.getByText('論文を探索中',{exact:true})).toBeVisible();
  await expect(panel.getByText('前日分の完成待ち',{exact:true})).toHaveCount(0);
});

test("pipeline monitor distinguishes long work, lost responses and a user pause", async ({page}) => {
  const now=Date.now()/1000;
  let job:any={id:'long-job',state:'running',stage:'Writing and editing',progress:.12};
  let health:any={worker:{heartbeat:now,job_id:'long-job',step_started:now-3600},'pipeline-health':{issues:[],history:[]}};
  let reads=0,mutations=0;
  await page.route('**/api/pipeline-health',route=>{if(route.request().method()!=='GET')mutations++;reads++;return route.fulfill({json:health});});
  await page.route('**/api/nightly-video-runs',route=>route.fulfill({json:[{id:'monitor-run',day:'2026-10-06',state:job.state==='paused'?'paused':'building',job,data:{selected:{title:'A long paper'}},project:{data:{modes:{}}}}]}));
  await page.locator('nav').getByRole('button',{name:'動画を作る',exact:true}).click();
  const monitor=page.getByRole('group',{name:'作成の稼働確認'});
  await expect(monitor).toContainText('処理中 · サーバーから応答があります');
  await expect(monitor).toContainText('サーバー最終応答');
  health.worker.heartbeat=now-180;
  await page.locator('nav').getByRole('button',{name:'英語練習',exact:true}).click();
  await page.locator('nav').getByRole('button',{name:'動画を作る',exact:true}).click();
  await expect(monitor).toContainText('サーバーの応答を再確認中');
  health.worker.heartbeat=Date.now()/1000;
  job={...job,state:'paused'};
  await page.locator('nav').getByRole('button',{name:'英語練習',exact:true}).click();
  await page.locator('nav').getByRole('button',{name:'動画を作る',exact:true}).click();
  await expect(monitor).toContainText('利用者による停止を保持しています');
  expect(reads).toBeGreaterThanOrEqual(3);
  expect(mutations).toBe(0);
});

test("pipeline monitor automatically refreshes after a lost connection without starting jobs", async ({page}) => {
  let disconnected=true,reads=0;
  await page.route('**/api/pipeline-health',route=>{
    reads++;
    return disconnected?route.abort('failed'):route.fulfill({json:{worker:{heartbeat:Date.now()/1000,job_id:'recover-job'},'pipeline-health':{issues:[],history:[{job_id:'recover-job'}]}}});
  });
  await page.route('**/api/nightly-video-runs',route=>route.fulfill({json:[{id:'recover-run',day:'2026-10-06',state:'searching',job:{id:'recover-job',state:'running',progress:0},data:{}}]}));
  await page.clock.install();
  await page.locator('nav').getByRole('button',{name:'動画を作る',exact:true}).click();
  const monitor=page.getByRole('group',{name:'作成の稼働確認'});
  await expect(monitor).toContainText('接続を再確認中');
  disconnected=false;
  await page.clock.fastForward(16000);
  await expect(monitor).toContainText('処理中 · サーバーから応答があります');
  await expect(monitor).toContainText('自動復旧を1回確認しました');
  expect(reads).toBeGreaterThan(1);
});

test("conference awards show counts, official winners and acquisition gaps", async ({page}) => {
  const icra='https://www.ieee-ras.org/awards-recognition/conference-awards/ieee-icra-best-conference-paper-award/';
  await page.route('**/api/conference-awards',route=>route.fulfill({json:{winner_count:2,paper_count:1,sources:[
    {source:{venue:'ICRA',year:2025,url:icra},status:'verified winners',winner_count:2,paper_count:1,checked_at:1791016700,papers:[{title:'A Useful Robot Paper',name:'Best Conference Paper Award',official_url:icra},{title:'A Useful Robot Paper',name:'Best Student Paper Award',official_url:icra}],failures:[]},
    {source:{venue:'CoRL',year:2026,url:'https://www.corl.org/'},status:'not announced',conference_start:'2026-11-09',winner_count:0,paper_count:0,papers:[],failures:[]},
    {source:{venue:'IROS',year:2025,url:'https://iros25.org/'},status:'unavailable',winner_count:0,paper_count:0,papers:[],failures:[{url:'https://iros25.org/',reason:'Certificate validation failed'}]},
  ]}}));
  await page.locator('nav').getByRole('button',{name:'動画を作る',exact:true}).click();
  await page.getByText('学会の受賞情報・取得状況',{exact:true}).click();
  const panel=page.getByRole('region',{name:'学会別の受賞論文'});
  await expect(panel.getByRole('table')).toBeVisible();
  await expect(panel.locator('.award-total')).toContainText('2件の受賞情報 / 1本');
  await panel.getByRole('button',{name:'ICRAの受賞情報を見る'}).click();
  const winner=panel.getByRole('article',{name:'ICRA 2025の受賞情報'});
  await expect(winner.getByText('受賞確認済み · 受賞情報 2件 / 論文 1本')).toBeVisible();
  await winner.getByText('受賞論文を表示（1本）').click();
  await expect(winner.getByRole('link',{name:'Best Conference Paper Award',exact:true})).toHaveAttribute('href',icra);
  await expect(winner.getByText('A Useful Robot Paper',{exact:true})).toHaveCount(2);
  await expect(panel.getByRole('table').getByText('開催前・受賞未発表')).toBeVisible();
  await panel.getByRole('button',{name:'IROSの受賞情報を見る'}).click();
  await panel.getByText('取得先の問題（1件）').click();
  await expect(panel.getByText('Certificate validation failed')).toBeVisible();
});

test("award information can be refreshed without starting another film", async ({page}) => {
  await page.locator('nav').getByRole('button',{name:'動画を作る',exact:true}).click();
  const before=await (await page.request.get('/api/nightly-video-runs')).json();
  await page.getByText('学会の受賞情報・取得状況',{exact:true}).click();
  const panel=page.getByRole('region',{name:'学会別の受賞論文'});
  await panel.getByRole('button',{name:'受賞情報を更新',exact:true}).click();
  await expect(panel.getByRole('button',{name:'受賞情報を更新中…',exact:true})).toBeDisabled();
  await expect.poll(async()=>{
    const jobs=await (await page.request.get('/api/jobs')).json();
    return jobs.filter((j:any)=>j.kind==='award_refresh').length;
  }).toBe(1);
  const after=await (await page.request.get('/api/nightly-video-runs')).json();
  expect(after.map((r:any)=>r.id)).toEqual(before.map((r:any)=>r.id));
  await panel.getByRole('button',{name:'受賞情報の更新を一時停止'}).click();
  await expect(panel.getByRole('button',{name:'受賞情報の更新を再開'})).toBeVisible();
  await panel.getByRole('button',{name:'受賞情報の更新を再開'}).click();
  await expect(panel.getByRole('button',{name:'受賞情報を更新中…',exact:true})).toBeDisabled();
});

test("Test of Time shows the publication year separately from the award year", async ({page}) => {
  const source='https://neurips.cc/virtual/2025/awards_detail';
  await page.route('**/api/nightly-video-runs', route=>route.fulfill({json:[{id:'classic-run',day:'2026-10-04',state:'ready',job:null,data:{selected:{title:'An enduring learning method',published:'2015-06-01T00:00:00Z',awards:[{venue:'NeurIPS',year:2025,name:'Test of Time Award',kind:'test-of-time',official_url:source}]},timings:{}}}]}));
  await page.locator('nav').getByRole('button',{name:'動画を作る',exact:true}).click();
  const panel=page.getByRole('region',{name:'自動選定と動画作成'});
  await expect(panel.getByRole('link',{name:'NeurIPS 2025 · Test of Time Award'})).toHaveAttribute('href',source);
  await expect(panel.getByText(/長年の影響を評価する賞/)).toContainText('論文発表 2015年 / 受賞 2025年');
});

test("creation shows compact progress and sends completed films to the video library", async ({page}) => {
  const track={label:'解説編',lesson_id:'old-lesson',phase:'complete',videos:[{kind:'overview',state:'ready',data:{title:'Previous film',mp4:'videos/old.mp4',duration:150}}]};
  const previous={id:'previous',day:'2026-10-03',state:'ready',job:null,data:{selected:{title:'Previous paper'}},project:{data:{modes:{overview:track}}}};
  await page.route('**/api/nightly-video-runs',route=>route.fulfill({json:[{id:'new',day:'2026-10-04',state:'building',job:null,data:{manual:true},project:{data:{modes:{overview:track}}}},previous]}));
  await page.locator('nav').getByRole('button',{name:'動画を作る',exact:true}).click();
  const panel=page.getByRole('region',{name:'自動選定と動画作成'});
  await expect(panel.getByRole('article',{name:'解説編'})).toContainText('動画完成');
  await expect(panel.locator('img')).toHaveCount(0);
  await panel.getByText('過去の夜間運転',{exact:true}).click();
  await expect(panel.getByText('Previous paper',{exact:true})).toBeVisible();
  await panel.getByRole('button',{name:'完成動画を見る',exact:true}).first().click();
  await expect(page.getByRole('region',{name:'作成した動画'})).toBeVisible();
  await expect(page.getByRole('region',{name:'自動選定と動画作成'})).toHaveCount(0);
});

test('conference coverage shows missing prizes and institutional evidence', async ({page})=>{
  const url='https://2026.ieee-icra.org/awards/';
  await page.route('**/api/conference-awards', route=>route.fulfill({json:{winner_count:1,paper_count:1,sources:[{
    source:{venue:'ICRA',year:2026,url},status:'verified winners',winner_count:1,paper_count:1,
    coverage:{partial:true,confirmed_categories:1,category_count:2,missing_categories:[{name:'Best Paper Award on Robot Learning',official_url:url}]},
    local_ai:{state:'completed',units:1,records:[{url,reason_ja:'ページの構成変更に合わせて出典を確認しました。'}]},
    papers:[{title:'A Useful Robot Paper',name:'Best Student Paper Award',official_url:'https://www.cs.cmu.edu/~dpathak/',evidence_type:'institution'}],failures:[]
  }]}}));
  await page.locator('nav').getByRole('button',{name:'動画を作る',exact:true}).click();
  await page.getByText('学会の受賞情報・取得状況',{exact:true}).click();
  const panel=page.getByRole('region',{name:'学会別の受賞論文'});
  await expect(panel.getByRole('table')).toContainText('未取得の賞あり · 1賞確認 / 2掲載枠');
  await panel.getByRole('button',{name:'ICRAの受賞情報を見る'}).click();
  const card=panel.getByRole('article',{name:'ICRA 2026の受賞情報'});
  await card.getByText('受賞者を未取得の賞（1件）').click();
  await expect(card.getByRole('link',{name:'Best Paper Award on Robot Learning'})).toHaveAttribute('href',url);
  await card.getByText('受賞論文を表示（1本）').click();
  await expect(card.getByText('所属機関の受賞発表',{exact:false})).toBeVisible();
  await card.getByText('ローカルAIの調査記録 · 確認済み · 1工程',{exact:true}).click();
  await expect(card.getByText(/ページの構成変更に合わせて出典を確認しました。/)).toBeVisible();
});

test("workspaces separate watching, making and practice without legacy controls", async ({page}) => {
  await expect(page.getByRole('region',{name:'解説・詳解動画',exact:true})).toHaveCount(0);
  await expect(page.getByRole('region',{name:'YouTube video export'})).toHaveCount(0);
  await page.locator('nav').getByRole('button',{name:'動画一覧',exact:true}).click();
  await expect(page.locator('.lesson-card,.story-project,.nightly-panel')).toHaveCount(0);
  await page.getByRole('button',{name:'新しい動画を作る',exact:true}).click();
  await expect(page.getByRole('region',{name:'自動選定と動画作成'})).toBeVisible();
  await expect(page.getByRole('region',{name:'学会別の受賞論文'})).toHaveCount(0);
  await page.getByRole('tab',{name:'保存済みの論文から作る'}).click();
  await expect(page.getByLabel('動画を作る論文')).toBeVisible();
  await page.locator('nav').getByRole('button',{name:'設定',exact:true}).click();
  await expect(page.getByText('従来の論文探索・章教材')).toHaveCount(0);
  await expect(page.getByLabel('Find papers and make lessons automatically')).toHaveCount(0);
  for (const name of ['動画一覧','動画を作る','英語練習','論文を探す','設定']) {
    await page.locator('nav').getByRole('button',{name,exact:true}).click();
    await page.setViewportSize({width:390,height:844});
    await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
    await expect.poll(()=>page.locator('nav button').evaluateAll(buttons=>buttons.every(b=>parseFloat(getComputedStyle(b).fontSize)>0))).toBeTruthy();
  }
});

test("arXiv and PDF import buttons create stories instead of legacy lessons", async ({page}) => {
  await page.locator('nav').getByRole('button',{name:'動画を作る',exact:true}).click();
  await page.getByRole('button',{name:'arXiv・PDFを取り込む'}).click();
  const dialog=page.getByRole('dialog',{name:'論文を取り込む'});
  await dialog.getByLabel('arXivのURLまたは論文ID').fill('2106.09685');
  const request=page.waitForRequest(r=>r.url().endsWith('/api/papers/import')&&r.method()==='POST');
  await dialog.getByRole('button',{name:'取り込んで2本の動画を作る'}).click();
  expect((await request).postDataJSON()).toEqual({reference:'2106.09685',generate:false,create_video:true});
  await expect(page.getByText('論文を取り込み中',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'arXiv・PDFを取り込む'}).click();
  await dialog.locator('input[type=file]').setInputFiles({name:'New paper.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-1.4\nUI story import fixture')});
  await expect(dialog).toHaveCount(0);
  await expect.poll(async()=>{
    const ls=await (await page.request.get('/api/lessons')).json();
    return ls.filter((l:any)=>l.data.format==='paper-story-1'&&l.data.title.includes('New paper')).length;
  }).toBe(2);
});

test('practice shows one paper with two editions and folds older lessons', async ({page})=>{
  const base=(await (await page.request.get('/api/lessons')).json()).find((l:any)=>l.data.title==='Interface test: a small change');
  const overview={...base,data:{...base.data,title:'One useful paper · 解説編',format:'paper-story-1',mode:'overview'}};
  const deep={...base,id:'test-deep',state:'building',ready_chapters:1,chapter_count:5,data:{...base.data,title:'One useful paper · 詳解編',format:'paper-story-1',mode:'deep_dive'}};
  const earlier={...overview,id:'earlier-lesson',created:base.created-86400,data:{...overview.data,title:'Earlier conversation'}};
  await page.route('**/api/lessons',route=>route.fulfill({json:[overview,deep,earlier]}));
  await page.route('**/api/papers',route=>route.fulfill({json:[{id:base.paper_id,title:'One useful paper',data:{}}]}));
  await page.reload();
  await page.locator('nav').getByRole('button',{name:'英語練習',exact:true}).click();
  await expect(page.getByRole('heading',{name:'One useful paper',exact:true})).toHaveCount(1);
  await expect(page.getByRole('button',{name:'One useful paper · 詳解編を練習',exact:true})).toContainText('完成分を練習');
  await expect(page.getByRole('button',{name:'Earlier conversationを練習',exact:true})).not.toBeVisible();
  await page.getByText('以前の教材（1件）',{exact:true}).click();
  await expect(page.getByRole('button',{name:'Earlier conversationを練習',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'One useful paper · 解説編を練習',exact:true}).click();
  await expect(page.locator('.spoken-sentence')).toHaveText('The model learns a small change instead of changing every weight.');
});

test('saved-paper picker prioritises current projects and searches other saved papers on request', async ({page})=>{
  const base=(await (await page.request.get('/api/lessons')).json()).find((l:any)=>l.data.title==='Interface test: a small change');
  await page.route('**/api/lessons',route=>route.fulfill({json:[base]}));
  await page.route('**/api/papers',route=>route.fulfill({json:[
    {id:'archived-paper',title:'Old legacy paper',source_id:'1001.00001',lesson_count:1,data:{}},
    {id:'reference-paper',title:'Related reference',source_id:'1234.56789',lesson_count:0,data:{}},
    {id:base.paper_id,title:'Current project paper',source_id:'2106.09685',lesson_count:2,data:{}},
  ]}));
  await page.reload();
  await page.locator('nav').getByRole('button',{name:'動画を作る',exact:true}).click();
  await page.getByRole('tab',{name:'保存済みの論文から作る'}).click();
  const picker=page.getByLabel('動画を作る論文');
  await expect(picker.locator('option')).toHaveCount(1);
  await expect(picker).toHaveValue(base.paper_id);
  await page.getByLabel('参考文献・未作成の論文も表示').check();
  await expect(picker.locator('option')).toHaveCount(3);
  await page.getByLabel('保存済みの論文を検索').fill('1234.56789');
  await expect(picker.locator('option')).toHaveCount(1);
  await expect(picker).toHaveValue('reference-paper');
  await page.getByLabel('参考文献・未作成の論文も表示').uncheck();
  await expect(page.getByText('条件に合う保存済み論文がありません。')).toBeVisible();
  await expect(page.getByRole('region',{name:'解説・詳解動画',exact:true})).toHaveCount(0);
});
