import { expect, test } from "@playwright/test";

test("AI 可从通讯录候选中识别错字姓名并由管理员采用", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill("browser-test");
  await page.getByLabel("密码", { exact: true }).fill("local-test-only-123");
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(
    page.getByRole("button", { name: "浏览器验收例会" }),
  ).toBeVisible();

  const token = await page.evaluate(() => sessionStorage.getItem("token"));
  const response = await page.request.get(
    "http://127.0.0.1:8017/api/directory",
    {
      headers: { Authorization: `Bearer ${token}` },
    },
  );
  expect(response.ok()).toBeTruthy();
  const directory = await response.json();
  const suggested = directory.users.find(
    (user: any) => user.name === "通讯录成员000",
  );
  expect(suggested).toBeTruthy();

  await page.route("**/api/validation/config", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ ai_configured: true, ai_model: "browser-mock" }),
    }),
  );
  await page.route("**/api/validation/*/ai-suggestions", async (route) => {
    const request = route.request().postDataJSON();
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        model: "browser-mock",
        suggestions: request.item_ids.map((id: string) => ({
          id,
          confidence: 0.93,
          reason: "姓名相近且属于同一部门",
          owner_matches: [
            {
              original_name: "通讯录成原000",
              user_id: suggested.id,
              name: suggested.name,
              department: "浏览器验收部",
              confidence: 0.93,
              reason: "原文存在一个同音错字",
            },
          ],
        })),
      }),
    });
  });

  await page.getByRole("link", { name: "导入历史会议", exact: true }).click();
  await page.getByRole("button", { name: "核对并归档" }).click();
  const suggestion = page.locator(".smart-person-suggestion").first();
  await expect(suggestion).toContainText(
    "AI 建议：通讯录成原000 → 通讯录成员000",
  );
  await expect(suggestion).toContainText("置信度 93%");
  await suggestion.getByRole("button", { name: "采用" }).click();
  await expect(page.getByText("验收管理员、通讯录成员000")).toBeVisible();
  await expect(page.locator(".smart-person-suggestion")).toHaveCount(0);
});
