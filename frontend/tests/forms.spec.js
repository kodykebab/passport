import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const build = resolve("../apps/passport/passport/public/ui");
const manifest = JSON.parse(
  await readFile(resolve(build, "manifest.json"), "utf8")
)["src/main.js"];

async function render(page, data) {
  let submission;
  await page.route("http://passport.test/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === "POST") {
      submission = {
        path,
        fields: Object.fromEntries(new URLSearchParams(request.postData())),
      };
      return route.fulfill({
        contentType: "text/html",
        body: "<p>Submitted</p>",
      });
    }
    if (path.startsWith("/assets/")) {
      const file = path.slice("/assets/".length);
      const contentType = file.endsWith(".js")
        ? "text/javascript"
        : file.endsWith(".css")
        ? "text/css"
        : "font/woff2";
      return route.fulfill({
        contentType,
        body: await readFile(resolve(build, file)),
      });
    }
    return route.fulfill({
      contentType: "text/html",
      body: `<!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="/assets/${
        manifest.css[0]
      }"></head><body><div id="passport-ui"></div><script id="passport-data" type="application/json">${JSON.stringify(
        data
      ).replaceAll(
        "<",
        "\\u003c"
      )}</script><script type="module" src="/assets/${
        manifest.file
      }"></script></body></html>`,
    });
  });
  await page.goto("http://passport.test/account");
  return () => submission;
}

for (const [action, label] of [
  ["name", "Save display name"],
  ["device", "Log out this device"],
  ["everywhere", "Log out everywhere"],
]) {
  test(`${label} posts to account with the correct binding`, async ({
    page,
  }) => {
    const submitted = await render(page, {
      page: "account",
      profile: { email: "alice@example.com", full_name: "Alice" },
      csrf_token: "test-csrf",
      sessions: [
        { device: "test-device", user_agent: "Test browser", current: true },
      ],
    });
    const button = page.getByRole("button", { name: label, exact: true });
    const form = page.locator("form").filter({ has: button });
    await expect(page.getByLabel("Email", { exact: true })).toHaveAttribute(
      "readonly",
      ""
    );
    await form
      .getByLabel("Confirm your password", { exact: false })
      .fill("test-password");
    await form.locator("[name=password]").press("Enter");
    await expect(page.getByText("Submitted", { exact: true })).toBeVisible();
    expect(submitted().path).toBe("/account");
    expect(submitted().fields).toMatchObject({
      action,
      csrf_token: "test-csrf",
      password: "test-password",
    });
    if (action === "device")
      expect(submitted().fields.device).toBe("test-device");
  });
}

test("recovery selects HTML without an application write payload", async ({
  page,
}) => {
  const submitted = await render(page, {
    page: "session",
    title: "Validate session",
    message: "Interrupted writes will not be replayed.",
    csrf_token: "test-csrf",
    link: "/desk",
    link_label: "Return to site",
  });
  await page.getByRole("button", { name: "Retry validation" }).click();
  await expect(page.getByText("Submitted", { exact: true })).toBeVisible();
  expect(submitted()).toEqual({
    path: "/passport/retry",
    fields: { csrf_token: "test-csrf", response_format: "html" },
  });
});

test("email form preserves request binding, code generation, labels and cooldown", async ({
  page,
}) => {
  const submitted = await render(page, {
    page: "email",
    attempt: "test-attempt",
    form_token: "test-binding",
    challenge: { spent: false, generation: 2 },
    wait: 60,
    destination: "/account",
    password_url: "/login",
  });
  await expect(
    page.getByRole("button", { name: /Send another code/ })
  ).toBeDisabled();
  await page.getByLabel("Code", { exact: false }).fill("123456");
  await page.getByLabel("Code", { exact: false }).press("Enter");
  await expect(page.getByText("Submitted", { exact: true })).toBeVisible();
  expect(submitted()).toEqual({
    path: "/passport/email",
    fields: {
      action: "verify",
      attempt: "test-attempt",
      form_token: "test-binding",
      generation: "2",
      code: "123456",
    },
  });
});

test("narrow error feedback remains readable and receives focus", async ({
  page,
}) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await render(page, {
    page: "email",
    attempt: "test-attempt",
    form_token: "test-binding",
    error:
      "Incorrect code. Please check the newest message for this request and try again.",
    destination: "/account",
    password_url: "/login",
  });
  await expect(page.getByRole("alert")).toBeFocused();
  await expect(page.getByRole("alert")).toHaveCount(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(
    375
  );
  expect(
    await page
      .getByText(
        "Incorrect code. Please check the newest message for this request and try again.",
        { exact: true }
      )
      .evaluate((element) => element.scrollWidth <= element.clientWidth)
  ).toBe(true);
});

test("password errors stay inline, then Enter uses native authentication", async ({
  page,
}) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await render(page, {
    page: "password",
    password_enabled: true,
    destination: "/oidc/authorize?state=bound",
    email_url: "/passport/email",
  });
  let attempts = 0;
  await page.route("**/api/method/login", async (route) => {
    attempts++;
    expect(
      Object.fromEntries(new URLSearchParams(route.request().postData()))
    ).toEqual({
      usr: "alice@example.com",
      pwd: attempts === 1 ? "incorrect" : "correct",
    });
    await route.fulfill({
      status: attempts === 1 ? 401 : 200,
      json:
        attempts === 1
          ? { exc_type: "AuthenticationError" }
          : { message: "No App" },
    });
  });
  await expect(page.locator("input[name=usr]")).toBeFocused();
  await expect(
    page.getByRole("link", { name: "Use email code" })
  ).toHaveAttribute("target", "_self");
  await page.locator("input[name=usr]").fill("alice@example.com");
  await page.locator("input[name=pwd]").fill("incorrect");
  await page.locator("input[name=pwd]").press("Enter");
  await expect(page.getByRole("alert")).toBeFocused();
  await expect(page.getByRole("alert")).toContainText(
    "Incorrect email or password"
  );
  await expect(page.locator("input[name=pwd]")).toHaveValue("");
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(
    375
  );
  await page.locator("input[name=pwd]").fill("correct");
  await page.locator("input[name=pwd]").press("Enter");
  await expect(page).toHaveURL(
    "http://passport.test/oidc/authorize?state=bound"
  );
  expect(attempts).toBe(2);
});

test("native password policy hides password controls", async ({ page }) => {
  await render(page, {
    page: "password",
    password_enabled: false,
    email_url: "/passport/email",
  });
  await expect(page.getByRole("alert")).toContainText(
    "Password login is disabled"
  );
  await expect(page.locator("input")).toHaveCount(0);
  await page.getByRole("link", { name: "Use email code" }).click();
  await expect(page).toHaveURL("http://passport.test/passport/email");
});

for (const failure of ["throttle", "network"]) {
  test(`password ${failure} has one notice and no automatic retry`, async ({
    page,
  }) => {
    await render(page, {
      page: "password",
      password_enabled: true,
      destination: "/account",
    });
    let calls = 0;
    await page.route("**/api/method/login", async (route) => {
      calls++;
      if (failure === "network") return route.abort();
      return route.fulfill({
        status: 403,
        json: { exc_type: "SecurityException" },
      });
    });
    await page.locator("input[name=usr]").fill("alice@example.com");
    await page.locator("input[name=pwd]").fill("test-password");
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(page.getByRole("alert")).toHaveCount(1);
    await expect(page.getByRole("alert")).toContainText(
      failure === "network" ? "Unable to reach" : "Too many password attempts"
    );
    expect(calls).toBe(1);
    await expect(
      page.getByRole("button", { name: "Sign in", exact: true })
    ).toBeEnabled();
  });
}

test("signup requests a code before collecting a password", async ({
  page,
}) => {
  const submitted = await render(page, {
    page: "email",
    purpose: "signup",
    path: "/passport/signup",
    attempt: "signup-attempt",
    form_token: "browser-proof",
    destination: "/account",
    password_url: "/login",
  });
  await expect(
    page.getByRole("heading", { name: "Create your Frappe ID" })
  ).toBeVisible();
  await expect(page.getByLabel("Choose a password")).toHaveCount(0);
  await page.getByLabel(/^Email/).fill("new@example.test");
  await page.getByRole("button", { name: "Send code", exact: true }).click();
  await expect.poll(submitted).toMatchObject({
    path: "/passport/signup",
    fields: {
      action: "send",
      email: "new@example.test",
      attempt: "signup-attempt",
      form_token: "browser-proof",
    },
  });
  expect(submitted().fields).not.toHaveProperty("password");
});

test("signup verification submits credentials with its own request binding at 375px", async ({
  page,
}) => {
  await page.setViewportSize({ width: 375, height: 812 });
  const submitted = await render(page, {
    page: "email",
    purpose: "signup",
    path: "/passport/signup",
    attempt: "signup-attempt",
    form_token: "browser-proof",
    destination: "/account",
    password_url: "/login",
    challenge: { generation: 2, spent: false },
    wait: 60,
  });
  await expect(
    page.getByRole("button", { name: "Send another code" })
  ).toBeDisabled();
  await expect(
    page.getByText("This creates your Frappe ID only.", { exact: false })
  ).toBeVisible();
  await page.getByLabel(/^Code/).fill("123456");
  await page.getByLabel(/^Display name/).fill("New User");
  await page.getByLabel(/^Choose a password/).fill("signup-test-password");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth
    )
  ).toBe(true);
  await page.getByLabel(/^Choose a password/).press("Enter");
  await expect.poll(submitted).toMatchObject({
    path: "/passport/signup",
    fields: {
      action: "verify",
      attempt: "signup-attempt",
      form_token: "browser-proof",
      generation: "2",
      code: "123456",
      display_name: "New User",
      password: "signup-test-password",
    },
  });
});

test("linking requires a site password and preserves CSRF binding", async ({
  page,
}) => {
  await render(page, {
    page: "link",
    title: "Link your Frappe ID",
    user: "local@example.test",
    csrf_token: "link-csrf",
    linked: false,
  });
  await expect(
    page.getByLabel("Site account", { exact: true })
  ).toHaveAttribute("readonly", "");
  await page.getByLabel(/^Confirm your site password/).fill("fixture-password");
  const request = page.waitForRequest((r) => r.method() === "POST");
  await page.getByLabel(/^Confirm your site password/).press("Enter");
  const sent = await request;
  expect(new URL(sent.url()).pathname).toBe("/passport/link");
  const fields = new URLSearchParams(sent.postData());
  expect(fields.get("csrf_token")).toBe("link-csrf");
  expect(fields.get("password")).toBe("fixture-password");
});

test("link consent shows the site and identity and submits explicit cancellation", async ({
  page,
}) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await render(page, {
    page: "consent",
    title: "Link your Frappe ID",
    client_title: "Example Site",
    origin: "https://site.example",
    profile: { email: "issuer@example.test", full_name: "Issuer Name" },
    csrf_token: "consent-csrf",
    request_id: "request",
  });
  await expect(page.getByLabel("Site", { exact: true })).toHaveValue(
    "https://site.example"
  );
  await expect(page.getByLabel("Frappe ID email", { exact: true })).toHaveValue(
    "issuer@example.test"
  );
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(
    375
  );
  const request = page.waitForRequest((r) => r.method() === "POST");
  await page
    .getByRole("button", { name: "Cancel linking", exact: true })
    .click();
  const fields = new URLSearchParams((await request).postData());
  expect(fields.get("decision")).toBe("cancel");
  expect(fields.get("request_id")).toBe("request");
  expect(fields.get("csrf_token")).toBe("consent-csrf");
});

for (const revoked of [false, true]) {
  test(`connected site ${
    revoked ? "allow" : "revoke"
  } submits with password and CSRF at 375px`, async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    const submitted = await render(page, {
      page: "account",
      profile: { email: "alice@example.com", full_name: "Alice" },
      csrf_token: "csrf",
      sessions: [],
      connections: [
        {
          client: "site-one",
          title: "Site One",
          origin: "https://site.example",
          revoked,
        },
      ],
    });
    const label = revoked ? "Allow future sign-ins" : "Revoke site access";
    const form = page
      .locator("form")
      .filter({ has: page.getByRole("button", { name: label, exact: true }) });
    await expect(form.getByRole("heading", { name: "Site One" })).toBeVisible();
    if (revoked) await expect(form).toContainText("Access revoked.");
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth
      )
    ).toBe(true);
    await form
      .getByLabel("Confirm your password", { exact: false })
      .fill("fresh-password");
    await form.locator("[name=password]").press("Enter");
    await expect.poll(submitted).toEqual({
      path: "/account",
      fields: {
        action: revoked ? "allow_site" : "revoke_site",
        csrf_token: "csrf",
        client: "site-one",
        password: "fresh-password",
      },
    });
  });
}

test("site picker filters known sites and never links revoked sites", async ({
  page,
}) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await render(page, {
    page: "sites",
    title: "Your sites",
    sites: [
      {
        client: "one",
        title: "First site",
        origin: "https://first.example",
        login_url: "https://first.example/passport/login",
        revoked: false,
      },
      {
        client: "two",
        title: "Revoked site",
        origin: "https://second.example",
        login_url: null,
        revoked: true,
      },
    ],
  });
  await expect(
    page.getByRole("link", { name: "Open site", exact: true })
  ).toHaveAttribute("href", "https://first.example/passport/login");
  await expect(
    page.getByRole("region", { name: "Revoked site" })
  ).toContainText("Access revoked");
  await expect(
    page.getByRole("region", { name: "Revoked site" }).getByRole("link")
  ).toHaveCount(0);
  await page.getByLabel("Find a site").fill("Revoked");
  await expect(page.getByRole("heading", { name: "First site" })).toHaveCount(
    0
  );
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth
    )
  ).toBe(true);
  await page.getByLabel("Find a site").fill("missing");
  await expect(
    page.getByText("No matching sites.", { exact: true })
  ).toBeVisible();
});
