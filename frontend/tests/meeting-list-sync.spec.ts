import { expect, test, type Page } from "@playwright/test";

async function harness(page: Page) {
  await page.clock.install();
  await page.route("**/__meeting-sync-harness.html", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: '<!doctype html><html><body><div id="harness"></div><script type="module" src="/tests/fixtures/meeting-sync-harness.ts"></script></body></html>',
    }),
  );
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/failed")) {
      await route.fulfill({ status: 500, json: { detail: "失败" } });
    } else if (route.request().method() === "DELETE") {
      await route.fulfill({ status: 204 });
    } else {
      await route.fulfill({ json: { ok: true } });
    }
  });
  await page.goto("/__meeting-sync-harness.html");
  await page.waitForFunction(() => (window as any).meetingSyncHarness);
}

async function count(page: Page) {
  return page.evaluate(() => (window as any).meetingSyncHarness.calls);
}

test("会议成功写入合并同步，查询禁用缓存，失败及其他业务不广播", async ({
  page,
}) => {
  await harness(page);
  await page.evaluate(async () => {
    const h = (window as any).meetingSyncHarness;
    await h.api("/meetings?limit=20");
    await h.api("/tasks", "POST", { content: "other business" });
    await h.api("/meetings/failed", "DELETE").catch(() => {});
  });
  await page.clock.runFor(200);
  expect(await count(page)).toBe(0);
  const options = await page.evaluate(
    () => (window as any).meetingSyncHarness.fetchOptions,
  );
  expect(options[0]).toEqual({
    url: "/api/meetings?limit=20",
    cache: "no-store",
  });
  await page.evaluate(async () => {
    const h = (window as any).meetingSyncHarness;
    await h.api("/meetings/draft-a", "DELETE");
    await h.api("/series", "POST", { name: "new series" });
    await h.api("/meeting-series/series-a", "DELETE");
  });
  await page.clock.runFor(200);
  expect(await count(page)).toBe(1);
});

test("另一个标签的变动通知自动刷新且共享标记不包含会议资料", async ({
  page,
  context,
}) => {
  await harness(page);
  const other = await context.newPage();
  await harness(other);
  await other.evaluate(async () => {
    await (window as any).meetingSyncHarness.api(
      "/meetings/private-meeting-id",
      "DELETE",
    );
  });
  await expect
    .poll(() =>
      page.evaluate(
        () => (window as any).meetingSyncHarness.storageEvents.length,
      ),
    )
    .toBe(1);
  await page.clock.runFor(200);
  expect(await count(page)).toBe(1);
  const marker = await page.evaluate(() =>
    localStorage.getItem("meeting-list-change"),
  );
  expect(marker).toMatch(/^\d+:0\.\d+$/);
  expect(marker).not.toContain("private-meeting-id");
});

test("只在可见时定时同步，恢复可见与焦点时刷新", async ({ page }) => {
  await harness(page);
  await page.clock.runFor(15001);
  expect(await count(page)).toBe(1);
  await page.evaluate(() => {
    const h = (window as any).meetingSyncHarness;
    h.visibility = "hidden";
    document.dispatchEvent(new Event("visibilitychange"));
    h.announce();
  });
  await page.clock.runFor(45000);
  expect(await count(page)).toBe(1);
  await page.evaluate(() => {
    (window as any).meetingSyncHarness.visibility = "visible";
    document.dispatchEvent(new Event("visibilitychange"));
    window.dispatchEvent(new Event("focus"));
  });
  await page.clock.runFor(1);
  expect(await count(page)).toBe(2);
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await page.clock.runFor(1);
  expect(await count(page)).toBe(3);
});

test("在途刷新只排队一次，离开页面移除所有后续刷新", async ({ page }) => {
  await harness(page);
  await page.evaluate(() => {
    const h = (window as any).meetingSyncHarness;
    h.block = true;
    h.announce();
  });
  await page.clock.runFor(200);
  expect(await count(page)).toBe(1);
  await page.evaluate(() => {
    const h = (window as any).meetingSyncHarness;
    for (let i = 0; i < 50; i += 1) h.announce();
    window.dispatchEvent(new Event("focus"));
  });
  await page.clock.runFor(1000);
  expect(await count(page)).toBe(1);
  await page.evaluate(() => {
    const h = (window as any).meetingSyncHarness;
    h.block = false;
    h.release();
  });
  await page.clock.runFor(1);
  expect(await count(page)).toBe(2);
  expect(
    await page.evaluate(() => (window as any).meetingSyncHarness.maxActive),
  ).toBe(1);
  await page.evaluate(() => {
    const h = (window as any).meetingSyncHarness;
    h.unmount();
    h.announce();
    window.dispatchEvent(new Event("focus"));
  });
  await page.clock.runFor(45000);
  expect(await count(page)).toBe(2);
});

test("后台刷新失败后仍可恢复，且不产生未处理异常", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await harness(page);
  await page.evaluate(() => {
    const h = (window as any).meetingSyncHarness;
    h.failNext = true;
    h.announce();
  });
  await page.clock.runFor(200);
  expect(await count(page)).toBe(1);
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await page.clock.runFor(1);
  expect(await count(page)).toBe(2);
  expect(errors).toEqual([]);
});
