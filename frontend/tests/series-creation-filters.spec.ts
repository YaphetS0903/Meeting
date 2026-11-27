import { expect, test, type Page } from "@playwright/test";
import { fileURLToPath } from "node:url";

const evidence =
  process.env.SERIES_TEST_EVIDENCE_DIR ||
  fileURLToPath(
    new URL(
      "../../../.validation/latest-import-current-20261113/",
      import.meta.url,
    ),
  );

async function setup(page: Page, isAdmin = true) {
  const me = {
    id: "admin",
    name: "系统管理员",
    is_admin: isAdmin,
    business_permissions: isAdmin ? undefined : {},
    active: true,
    department_id: "dep",
  };
  const series = [
    { id: "old", name: "采购周会", department_id: "dep", kind: "department" },
  ];
  const control = {
    series,
    writes: [] as any[],
    reads: [] as URL[],
    errors: [] as string[],
    fail: false,
    duplicate: false,
  };
  page.on("pageerror", (error) => control.errors.push(error.message));
  await page.addInitScript(() =>
    sessionStorage.setItem("token", "isolated-series-filters"),
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
    else if (path === "/api/meeting-people")
      body = { users: [me], departments: [{ id: "dep", name: "采购部" }] };
    else if (path === "/api/meeting-series") {
      control.reads.push(url);
      const items = series
        .filter((s) => s.name.includes(url.searchParams.get("q") || ""))
        .map((s) => ({
          key: `series:${s.id}`,
          name: s.name,
          kinds: [s.kind],
          meeting_count: s.id === "old" ? 1 : 0,
          latest_date: s.id === "old" ? "2026-11-09" : null,
        }));
      body = { items, total: items.length, meeting_total: 1 };
    } else if (path === "/api/series" && request.method() === "POST") {
      control.writes.push(request.postDataJSON());
      if (control.fail)
        return route.fulfill({
          status: 409,
          json: { detail: "同名系列已存在，请核对" },
        });
      body = control.duplicate
        ? { ...series[0], existing: true }
        : {
            id: "new",
            ...request.postDataJSON(),
            department_id: "dep",
            existing: false,
          };
      if (!control.duplicate) series.unshift(body);
    } else if (path === "/api/meetings") {
      control.reads.push(url);
      const items =
        (control.duplicate ||
          (!isAdmin && url.searchParams.get("series_key") === "series:old")) &&
        url.searchParams.get("view") === "current"
          ? [
              {
                id: "historical",
                title: "销售一部历史周例会",
                meeting_date: "2026-11-09",
                historical: true,
                published_version: 1,
                series_id: "old",
                can_request_continue: !isAdmin,
                department_id: "dep",
                host_name: "侯琰",
                item_count: 18,
              },
            ]
          : [];
      body = {
        items,
        total: items.length,
        can_start_meeting:
          isAdmin || url.searchParams.get("series_key") === "series:new",
      };
    } else if (path === "/api/meetings/historical" && !isAdmin) {
      body = {
        id: "historical",
        title: "销售一部历史周例会",
        series_id: "old",
        department_id: "dep",
        published_version: 1,
        can_request_continue: true,
        can_edit: false,
        versions: [],
        draft: { items: [], attendee_ids: [], online_attendee_ids: [] },
        attachments: [],
      };
    } else throw new Error(`Unexpected request ${request.method()} ${path}`);
    await route.fulfill({ json: body });
  });
  return control;
}

for (const viewport of [
  { width: 1366, height: 768 },
  { width: 390, height: 844 },
]) {
  test(`筛选顺序、简化状态和新建类型 ${viewport.width}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const control = await setup(page);
    await page.goto("/meetings");
    const newMeeting = page.getByRole("button", {
      name: "＋ 新建会议",
      exact: true,
    });
    await expect(newMeeting).toHaveCount(0);
    if (viewport.width < 768)
      await page.getByRole("button", { name: /筛选会议/ }).click();
    const search = page.getByRole("textbox", {
      name: "搜索会议系列名称",
      exact: true,
    });
    await expect(search).toBeVisible();
    expect(
      await search.evaluate((el) =>
        Boolean(
          el.compareDocumentPosition(
            document.querySelector('[aria-label="会议范围"]')!,
          ) & Node.DOCUMENT_POSITION_FOLLOWING,
        ),
      ),
    ).toBeTruthy();
    await expect(
      page.getByRole("combobox", { name: "发布状态", exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("combobox", { name: "上期回顾状态", exact: true }),
    ).toHaveCount(0);
    await search.fill("采购");
    await page.getByRole("button", { name: "查询", exact: true }).click();
    await expect
      .poll(() => control.reads.at(-1)?.searchParams.get("q"))
      .toBe("采购");
    expect(control.reads.at(-1)?.searchParams.has("publication")).toBeFalsy();
    expect(control.reads.at(-1)?.searchParams.has("status")).toBeFalsy();
    await page.screenshot({
      path: `${evidence}filters-${viewport.width}.png`,
      fullPage: true,
    });
    await search.clear();
    await page.getByRole("button", { name: "查询", exact: true }).click();
    await page
      .getByRole("button", { name: "＋ 新建会议系列", exact: true })
      .click();
    const dialog = page.getByRole("dialog", {
      name: "新建会议系列",
      exact: true,
    });
    await dialog.getByRole("button", { name: "确定", exact: true }).click();
    await expect(
      dialog.getByText("请输入会议系列名称", { exact: true }),
    ).toBeVisible();
    await dialog
      .getByRole("textbox", { name: "会议系列名称", exact: true })
      .fill("专项工作推进会");
    await expect(
      dialog.getByLabel("新系列所属部门", { exact: true }),
    ).toBeVisible();
    await dialog.getByText("专项会议", { exact: true }).click();
    await expect(
      dialog.getByLabel("新系列所属部门", { exact: true }),
    ).toHaveCount(0);
    await dialog.getByText("部门例会", { exact: true }).click();
    await expect(
      dialog.getByLabel("新系列所属部门", { exact: true }),
    ).toBeVisible();
    await dialog.getByText("专项会议", { exact: true }).click();
    await expect(dialog.getByText("所属部门", { exact: true })).toHaveCount(0);
    await expect(
      dialog.getByRole("radio", { name: "专项会议", exact: true }),
    ).toBeChecked();
    const bounds = await dialog.boundingBox();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(viewport.width);
    await page.screenshot({
      path: `${evidence}create-${viewport.width}.png`,
      fullPage: true,
    });
    await dialog.getByRole("button", { name: "确定", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    expect(control.writes).toEqual([
      { name: "专项工作推进会", kind: "special", department_id: "dep" },
    ]);
    await expect(page).toHaveURL(/\/meetings\?series=series(?::|%3A)new$/);
    await expect(
      page.getByRole("heading", { name: /专项工作推进会/ }),
    ).toBeVisible();
    await newMeeting.click();
    await expect(page).toHaveURL(/\/meetings\/new\?series=series(?::|%3A)new$/);
    const setupDialog = page.getByRole("dialog", {
      name: "新建会议",
      exact: true,
    });
    await expect(setupDialog).toBeVisible();
    await expect(
      setupDialog.getByText("专项会议", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("combobox", { name: "会议类型", exact: true }),
    ).toBeDisabled();
    expect(control.errors).toEqual([]);
  });
}

for (const width of [1366, 390]) {
  test(`系列详情只显示名称查询并忽略首页筛选 ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const control = await setup(page);
    await page.goto("/meetings");
    if (width < 768)
      await page.getByRole("button", { name: /筛选会议/ }).click();
    await page
      .getByRole("textbox", { name: "搜索会议系列名称", exact: true })
      .fill("采购");
    await page.getByRole("button", { name: "查询", exact: true }).click();
    const kindFilter = page.getByRole("combobox", {
      name: "会议类型",
      exact: true,
    });
    await kindFilter.focus();
    await kindFilter.press("ArrowDown");
    await page.getByRole("option", { name: "专项会议", exact: true }).click();
    const list = page.locator(
      width < 768 ? ".series-mobile-list" : ".series-archive-table",
    );
    await list.getByRole("button", { name: /^采购周会/ }).click();
    const search = page.getByRole("textbox", { name: "搜索会议", exact: true });
    await expect(search).toBeVisible();
    await expect(search).toHaveValue("");
    for (const name of ["会议范围", "部门", "会议类型"])
      await expect(
        page.getByRole("combobox", { name, exact: true }),
      ).toHaveCount(0);
    await expect(page.getByRole("button", { name: /筛选会议/ })).toHaveCount(0);
    await search.fill("具体会议名称");
    await page.getByRole("button", { name: "查询", exact: true }).click();
    await expect
      .poll(() => control.reads.at(-1)?.searchParams.get("q"))
      .toBe("具体会议名称");
    const request = control.reads.at(-1)!;
    expect(request.searchParams.get("series_key")).toBe("series:old");
    expect(request.searchParams.has("kind")).toBe(false);
    expect(request.searchParams.has("department_id")).toBe(false);
    expect(request.searchParams.get("scope")).toBe("all");
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: `${evidence}series-detail-${width}.png`,
      fullPage: true,
    });
    expect(control.errors).toEqual([]);
  });
}

test("创建失败保留名称、类型并可重试", async ({ page }) => {
  const control = await setup(page);
  await page.goto("/meetings");
  await page
    .getByRole("button", { name: "＋ 新建会议系列", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "新建会议系列",
    exact: true,
  });
  await dialog
    .getByRole("textbox", { name: "会议系列名称", exact: true })
    .fill("重试会议");
  await dialog.getByText("专项会议", { exact: true }).click();
  control.fail = true;
  await dialog.getByRole("button", { name: "确定", exact: true }).click();
  await expect(
    dialog.getByText("同名系列已存在，请核对", { exact: true }),
  ).toBeVisible();
  await expect(dialog.getByRole("textbox")).toHaveValue("重试会议");
  await expect(
    dialog.getByRole("radio", { name: "专项会议", exact: true }),
  ).toBeChecked();
  control.fail = false;
  await dialog.getByRole("button", { name: "确定", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  expect(control.errors).toEqual([]);
});

for (const width of [1366, 390]) {
  test(`已有系列最新历史导入会议显示在上方 ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const control = await setup(page);
    control.duplicate = true;
    await page.goto("/meetings");
    await page
      .getByRole("button", { name: "＋ 新建会议系列", exact: true })
      .click();
    const dialog = page.getByRole("dialog", {
      name: "新建会议系列",
      exact: true,
    });
    await dialog
      .getByRole("textbox", { name: "会议系列名称", exact: true })
      .fill("采购周会");
    await dialog.getByRole("button", { name: "确定", exact: true }).click();
    await expect(page).toHaveURL(/series=series(?::|%3A)old/);
    await expect(
      page.getByText("已进入已有会议系列", {
        exact: true,
      }),
    ).toBeVisible();
    const current = page.locator(
      width < 768 ? ".meeting-current-mobile" : ".meeting-current-table",
    );
    await expect(
      current.getByText("销售一部历史周例会", { exact: true }),
    ).toBeVisible();
    await expect(
      current.getByText("历史导入", { exact: width >= 768 }),
    ).toBeVisible();
    await page.getByRole("button", { name: "历史会议", exact: true }).click();
    const history = page.getByRole("region", {
      name: "本系列历史会议",
      exact: true,
    });
    await expect(
      history.getByText("销售一部历史周例会", { exact: true }),
    ).toHaveCount(0);
    expect(control.series).toHaveLength(1);
    expect(
      control.reads.some((u) => u.searchParams.get("view") === "history"),
    ).toBe(true);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: `${evidence}latest-import-current-${width}.png`,
      fullPage: true,
    });
    expect(control.errors).toEqual([]);
  });
}

test("同名不同部门的新系列不会被误判为已存在", async ({ page }) => {
  const control = await setup(page);
  await page.goto("/meetings");
  await page
    .getByRole("button", { name: "＋ 新建会议系列", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "新建会议系列",
    exact: true,
  });
  await dialog
    .getByRole("textbox", { name: "会议系列名称", exact: true })
    .fill("采购周会");
  await dialog.getByRole("button", { name: "确定", exact: true }).click();
  await expect(page.getByText("会议系列已建立", { exact: true })).toBeVisible();
  await expect(page).toHaveURL(/\/meetings\?series=series(?::|%3A)new$/);
  expect(control.series).toHaveLength(2);
  expect(control.errors).toEqual([]);
});

test("员工先建系列再新建首场会议，已有系列进入接续申请", async ({ page }) => {
  const control = await setup(page, false);
  await page.goto("/meetings");
  const newMeeting = page.getByRole("button", {
    name: "＋ 新建会议",
    exact: true,
  });
  await expect(newMeeting).toHaveCount(0);
  await page
    .getByRole("button", { name: "＋ 新建会议系列", exact: true })
    .click();
  const dialog = page.getByRole("dialog", { name: "新建会议系列" });
  await dialog
    .getByRole("textbox", { name: "会议系列名称" })
    .fill("员工新建周会");
  await dialog.getByRole("button", { name: "确定", exact: true }).click();
  await expect(page).toHaveURL(/\/meetings\?series=series(?::|%3A)new$/);
  await expect(
    page.getByRole("heading", { name: /员工新建周会/ }),
  ).toBeVisible();
  await newMeeting.click();
  await expect(page).toHaveURL(/\/meetings\/new\?series=series(?::|%3A)new/);

  await page.goto("/meetings?series=series%3Aold");
  await expect(newMeeting).toHaveCount(0);
  await page
    .getByRole("button", { name: "申请接续权限", exact: true })
    .filter({ visible: true })
    .first()
    .click();
  await expect(page).toHaveURL(/\/meetings\/historical.*request=continue/);
  await page.getByRole("button", { name: "申请接续权限", exact: true }).click();
  const requestDialog = page.getByRole("dialog", { name: "申请接续权限" });
  await expect(requestDialog).toBeVisible();
  await expect(requestDialog.getByText(/主持人审批通过后/)).toBeVisible();
  await expect(
    requestDialog.getByRole("button", { name: "确定申请" }),
  ).toBeVisible();
  expect(control.errors).toEqual([]);
});
