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
  await page.request.put(`/api/lessons/${ls[0].id}/progress`, {
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
  await page.getByRole("button", { name: /What stays fixed/ }).click();
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
  const response = await page.request.get("/api/lessons");
  const lesson = (await response.json())[0];
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
