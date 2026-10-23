import { test, expect } from "@playwright/test";

test("企业登录回调保留申请目标并清除授权参数（模拟提供方）", async ({
  page,
  request,
}) => {
  const login = await request.post("http://127.0.0.1:8017/api/auth/login", {
    data: { username: "browser-test", password: "local-test-only-123" },
  });
  expect(login.ok()).toBeTruthy();
  const token = (await login.json()).access_token;
  let destination = "";
  await page.route("**/api/auth/options", (route) =>
    route.fulfill({ json: { wecom: true } }),
  );
  await page.route("**/api/auth/wecom/url?*", (route) => {
    destination =
      new URL(route.request().url()).searchParams.get("return_to") || "";
    return route.fulfill({
      json: {
        url: "http://127.0.0.1:5177/tasks?code=test-code&state=test-state",
      },
    });
  });
  await page.route("**/api/auth/wecom/exchange", (route) =>
    route.fulfill({
      json: {
        access_token: token,
        token_type: "bearer",
        return_to: destination,
      },
    }),
  );
  await page.goto("/verifications?request=unavailable-request");
  await page.getByRole("button", { name: "企业微信登录", exact: true }).click();
  await expect(page).toHaveURL(/\/verifications\?request=unavailable-request$/);
  await expect(
    page.getByRole("heading", { name: "待我验证", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText(
      "消息中的申请当前不在您的验证范围，请查看当前待验证清单或联系会议负责人。",
      { exact: true },
    ),
  ).toBeVisible();
  expect(destination).toBe("/verifications?request=unavailable-request");
  expect(new URL(page.url()).searchParams.has("code")).toBeFalsy();
  expect(new URL(page.url()).searchParams.has("state")).toBeFalsy();
});
