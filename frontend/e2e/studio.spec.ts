import { test, expect } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await page.locator(".login-page, .lesson-card").first().waitFor();
  if (await page.getByLabel("Studio password").isVisible()) {
    await page.getByLabel("Studio password").fill("ui-test-only");
    await page.getByRole("button", { name: "Come on in" }).click();
  }
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
test("Japanese paper search shows titles, abstracts, and a selectable paper", async ({ page }) => {
  await page.getByRole("button", { name: "論文を探す" }).click();
  await expect(page.getByRole("heading", { name: "次に読みたい論文を探す。" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "初めて見る物をつかむロボット", exact: true })).toBeVisible();
  await expect(page.getByText("未知の物をつかむ学習について読めます。")).toBeVisible();
  await expect(page.getByRole("heading", { name: "ロボットが新しい物をつかむ研究" })).toBeVisible();
  await page.getByRole("button", { name: "この論文で教材とMP4を作る" }).first().click();
  await expect(page.getByRole("region", { name: "YouTube video export" })).toBeVisible();
  await expect.poll(async () => {
    const lessons = await (await page.request.get("/api/lessons")).json();
    return lessons.some((lesson: any) => lesson.data.title === "A Robot That Learns to Grasp");
  }).toBeTruthy();
  await page.getByRole("button", { name: "論文を探す" }).click();
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
  await page.getByRole("button", { name: "Settings", exact: true }).click();
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
  await page.unroute("**/api/attempts");
  await page.getByRole("button", { name: "Send it now" }).click();
  await expect(
    page.getByText(
      "Your recording is saved. You can stay here while we check it.",
    ),
  ).toBeVisible();
});
