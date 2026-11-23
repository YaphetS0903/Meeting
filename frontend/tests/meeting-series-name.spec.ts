import { expect, test, type Page } from "@playwright/test";
import { fileURLToPath } from "node:url";

const evidence = fileURLToPath(
  new URL("../../../.validation/series-row-rename-20261105/", import.meta.url),
);

async function fixture(page: Page, role = "admin") {
  const me = {
    id: "recorder",
    name: "编制人",
    active: true,
    department_id: "dep",
    is_admin: role === "admin",
    can_rename_meetings: role !== "employee",
  };
  const series = Array.from({ length: 9 }, (_, i) => ({
    id: `series-${i}`,
    name: i === 0 ? "采购周会" : `会议系列${i + 1}`,
    department_id: "dep",
    can_rename: role === "admin" || i === 0,
  }));
  const row = {
    id: "meeting",
    title: "原有单场会议名称",
    series_id: "series-0",
    series_name: "采购周会",
    meeting_date: "2026-11-04",
    department_id: "dep",
    published_version: 1,
    has_draft: false,
    can_continue: false,
    can_delete: false,
    item_count: 0,
  };
  const control = {
    writes: [] as any[],
    errors: [] as string[],
    fail: false,
    optionsFail: false,
    series,
    row,
  };
  page.on("pageerror", (error) => control.errors.push(error.message));
  await page.addInitScript(() =>
    sessionStorage.setItem("token", "isolated-series-name"),
  );
  await page.route("**/api/**", async (route) => {
    const request = route.request(),
      url = new URL(request.url()),
      path = url.pathname;
    let body: any;
    if (path === "/api/me") body = me;
    else if (path === "/api/auth/options") body = { wecom: false };
    else if (path === "/api/directory")
      body = {
        users: [me],
        departments: [{ id: "dep", name: "采购部" }],
        series,
      };
    else if (path === "/api/meeting-series") {
      const items = series
        .filter(
          (s) =>
            !url.searchParams.get("q") ||
            s.name.includes(url.searchParams.get("q")!),
        )
        .map((s, i) => ({
          key: `series:${s.id}`,
          name: s.name,
          meeting_count: [3, 2, 2, 2, 2, 2, 1, 1, 0][i],
          latest_date: "2026-11-04",
        }));
      body = { items, total: items.length, meeting_total: 15 };
    } else if (path === "/api/meetings")
      body = {
        items: url.searchParams.get("view") === "history" ? [] : [row],
        total: 1,
      };
    else if (path === "/api/meeting-series-name-options") {
      if (control.optionsFail)
        return route.fulfill({
          status: 503,
          json: { detail: "系列读取失败，请稍后重试" },
        });
      body = { items: series, total: 9 };
    } else if (/^\/api\/meeting-series\/series-\d+\/name$/.test(path)) {
      control.writes.push({ path, ...request.postDataJSON() });
      if (control.fail)
        return route.fulfill({
          status: 409,
          json: { detail: "会议系列名称已被修改，请重新选择系列后再试" },
        });
      const selected = series.find((s) => path.includes(`/${s.id}/`))!;
      selected.name = request.postDataJSON().name;
      body = { id: selected.id, name: selected.name };
    } else throw new Error(`Unexpected API: ${request.method()} ${path}`);
    await route.fulfill({ json: body });
  });
  return control;
}

function dialog(page: Page) {
  return page.getByRole("dialog", { name: "编辑会议系列名称", exact: true });
}
function editButton(page: Page, name = "采购周会") {
  return page
    .getByRole("button", { name: `编辑系列名称：${name}`, exact: true })
    .filter({ visible: true });
}

for (const width of [1366, 390, 320]) {
  test(`行内编辑直接选中系列，保存保留单场名称（${width}）`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    const control = await fixture(page);
    await page.goto("/meetings");
    await expect(
      page.getByRole("heading", { name: "会议系列 9", exact: true }),
    ).toBeVisible();
    await expect(
      page.locator(".panel-heading").getByRole("button", { name: /编辑/ }),
    ).toHaveCount(0);
    await expect(
      page.locator("button:visible[aria-label^='编辑系列名称：']"),
    ).toHaveCount(9);
    await page.screenshot({
      path: evidence + `rows-${width}.png`,
      fullPage: true,
      animations: "disabled",
    });
    await editButton(page).click();
    await expect(page).toHaveURL(/\/meetings$/);
    const modal = dialog(page);
    await expect(modal).toContainText("当前系列：采购周会");
    await expect(modal.getByRole("combobox")).toHaveCount(0);
    await expect(
      modal.getByRole("textbox", { name: "新系列名称" }),
    ).toHaveValue("采购周会");
    await expect(
      modal.getByRole("button", { name: "保存名称" }),
    ).toBeDisabled();
    await modal
      .getByRole("textbox", { name: "新系列名称" })
      .fill("采购部周例会");
    await expect(
      modal.getByRole("button", { name: "保存名称" }),
    ).toBeInViewport();
    await page.screenshot({
      path: evidence + `dialog-${width}.png`,
      fullPage: true,
      animations: "disabled",
    });
    await modal.getByRole("button", { name: "保存名称" }).click();
    await expect(modal).not.toBeVisible();
    await expect(editButton(page, "采购部周例会")).toBeVisible();
    expect(control.writes).toEqual([
      {
        path: "/api/meeting-series/series-0/name",
        name: "采购部周例会",
        expected_name: "采购周会",
      },
    ]);
    if (width < 768)
      await page
        .locator(".series-mobile-row")
        .filter({ hasText: "采购部周例会" })
        .click();
    else
      await page
        .getByRole("button", { name: "采购部周例会", exact: true })
        .click();
    await expect(
      page.getByRole("heading", { name: "采购部周例会 1", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "原有单场会议名称", exact: true }),
    ).toBeVisible();
    expect(control.row.title).toBe("原有单场会议名称");
    expect(control.errors).toEqual([]);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  });
}

test("系列内隐藏编辑入口，返回后按行编辑，取消不提交", async ({ page }) => {
  const control = await fixture(page, "recorder");
  await page.goto("/meetings?series=series:series-0");
  await expect(
    page.getByRole("heading", { name: "采购周会 1", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: /^编辑系列名称：/ }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "会议系列", exact: true }).click();
  await editButton(page).click();
  const modal = dialog(page);
  await modal.getByRole("textbox", { name: "新系列名称" }).fill("取消的名称");
  await modal.getByRole("button", { name: "取消", exact: true }).click();
  expect(control.writes).toEqual([]);
  await editButton(page).click();
  await expect(modal.getByRole("textbox", { name: "新系列名称" })).toHaveValue(
    "采购周会",
  );
});

test("筛选后的行内编辑仍对应本系列", async ({ page }) => {
  await fixture(page);
  await page.goto("/meetings");
  await page
    .getByRole("textbox", { name: "搜索会议系列名称" })
    .fill("采购");
  await page.getByRole("button", { name: "查询", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "会议系列 1", exact: true }),
  ).toBeVisible();
  await expect(
    page.locator("button:visible[aria-label^='编辑系列名称：']"),
  ).toHaveCount(1);
  await editButton(page).click();
  await expect(
    dialog(page).getByRole("textbox", { name: "新系列名称" }),
  ).toHaveValue("采购周会");
});

test("普通员工无入口，编制人仅本人系列有编辑操作且无删除操作", async ({
  page,
}) => {
  await fixture(page, "employee");
  await page.goto("/meetings");
  await expect(
    page.getByRole("button", { name: "采购周会", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: /^编辑系列名称：/ }),
  ).toHaveCount(0);
  await page.unroute("**/api/**");
  await fixture(page, "recorder");
  await page.reload();
  await expect(editButton(page)).toBeVisible();
  await expect(editButton(page, "会议系列2")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /^删除系列：/ })).toHaveCount(
    0,
  );
  await editButton(page).click();
  await expect(dialog(page)).toContainText("当前系列：采购周会");
});

test("并发改名冲突保留输入和原系列", async ({ page }) => {
  const control = await fixture(page);
  control.fail = true;
  await page.goto("/meetings");
  await editButton(page).click();
  const modal = dialog(page);
  await modal
    .getByRole("textbox", { name: "新系列名称" })
    .fill("希望保留的名称");
  await modal.getByRole("button", { name: "保存名称" }).click();
  await expect(modal.getByRole("alert")).toContainText("会议系列名称已被修改");
  await expect(modal.getByRole("textbox", { name: "新系列名称" })).toHaveValue(
    "希望保留的名称",
  );
  expect(control.series[0].name).toBe("采购周会");
});

test("编制人编辑权限读取失败可重试，会议列表保持可用", async ({ page }) => {
  const control = await fixture(page, "recorder");
  control.optionsFail = true;
  await page.goto("/meetings");
  await expect(page.getByRole("alert")).toContainText("系列读取失败");
  await expect(
    page.getByRole("button", { name: "采购周会", exact: true }),
  ).toBeVisible();
  await expect(editButton(page)).toHaveCount(0);
  control.optionsFail = false;
  await page
    .getByRole("button", { name: "重试读取编辑权限", exact: true })
    .click();
  await expect(editButton(page)).toBeVisible();
  await editButton(page).click();
  await expect(
    dialog(page).getByRole("textbox", { name: "新系列名称" }),
  ).toHaveValue("采购周会");
});

test("点击其他行和空系列编辑时预填对应名称，不触发进入系列", async ({
  page,
}) => {
  const control = await fixture(page);
  await page.goto("/meetings");
  for (const name of ["会议系列2", "会议系列9"]) {
    await editButton(page, name).click();
    await expect(page).toHaveURL(/\/meetings$/);
    await expect(
      dialog(page).getByRole("textbox", { name: "新系列名称" }),
    ).toHaveValue(name);
    await dialog(page)
      .getByRole("button", { name: "取消", exact: true })
      .click();
  }
  await expect(
    page
      .getByRole("button", { name: "删除系列：会议系列9", exact: true })
      .filter({ visible: true }),
  ).toBeVisible();
  expect(control.writes).toEqual([]);
  expect(control.errors).toEqual([]);
});
