import {expect, test} from '@playwright/test';

async function signInAsAdministrator(page) {
  await page.goto('/');
  await page.locator('#login-username').fill('admin');
  await page.locator('#login-password').fill('Admin@12366');
  await page.locator('#login-submit').click();
  await expect(page.locator('#login-screen')).toHaveClass(/hidden/);
}

const dashboardPayload = {
  overview: {
    total_calls: 22,
    resolved_rate: 78.9,
    data_date_range: '2026-07-01 至 2026-07-31',
  },
  daily_calls: [],
  caller_types: [],
  historical_facts: [],
  registration_unit_resolution: [],
  unresolved_question_hotspots: {},
  question_categories: [],
  demand_categories: [],
  caller_resolution_rates: [
    {
      label: '个人', resolved: 8, unresolved: 3, unknown: 2,
      eligible_total: 11, resolved_rate: 72.7,
    },
    {
      label: '企业', resolved: 7, unresolved: 1, unknown: 1,
      eligible_total: 8, resolved_rate: 87.5,
    },
  ],
  enterprise_identity_resolution_rates: [
    {
      label: '办税人员', resolved: 6, unresolved: 0, unknown: 1,
      eligible_total: 6, resolved_rate: 100,
    },
    {
      label: '法定代表人', resolved: 4, unresolved: 1, unknown: 0,
      eligible_total: 5, resolved_rate: 80,
    },
    {
      label: '财务负责人', resolved: 1, unresolved: 1, unknown: 0,
      eligible_total: 2, resolved_rate: 50,
    },
    {
      label: '其他身份', resolved: 0, unresolved: 1, unknown: 0,
      eligible_total: 1, resolved_rate: 0,
    },
  ],
};

test('不同咨询主体解决率只展示比率并采用紧凑的两列布局', async ({page}) => {
  await page.route('**/api/dashboard', route => route.fulfill({json: dashboardPayload}));
  await signInAsAdministrator(page);
  await page.locator('[data-page="dashboard"]').click();

  const chart = page.locator('#caller-resolution-chart');
  await expect(chart.getByRole('heading', {name: '一级咨询主体'})).toBeVisible();
  await expect(chart.getByRole('heading', {name: '企业二级身份'})).toBeVisible();

  const personal = chart.locator('.resolution-rate-item', {hasText: '个人'});
  await expect(personal).toContainText('72.7%');
  await expect(personal).not.toContainText('已解决 8');
  await expect(personal).not.toContainText('未直接解决 3');
  await expect(personal).not.toContainText('待判断 2');
  await expect(personal).not.toContainText('总计 13');
  await expect(personal.locator('[role="meter"]')).toHaveAttribute(
    'aria-label',
    '个人已直接解决率 72.7%',
  );

  const identityLabels = await chart
    .locator('.resolution-identity-grid .resolution-subject-name')
    .allTextContents();
  expect(identityLabels).toEqual(['办税人员', '法定代表人', '财务负责人', '其他身份']);

  const primaryItems = chart.locator('.resolution-primary-grid .resolution-rate-item');
  const firstPrimaryBox = await primaryItems.nth(0).boundingBox();
  const secondPrimaryBox = await primaryItems.nth(1).boundingBox();
  expect(Math.abs(firstPrimaryBox.y - secondPrimaryBox.y)).toBeLessThanOrEqual(1);
  expect(secondPrimaryBox.x).toBeGreaterThan(firstPrimaryBox.x);

  const boardBox = await chart.locator('.resolution-comparison-board').boundingBox();
  expect(boardBox.height).toBeLessThanOrEqual(300);
});

for (const viewport of [
  {name: 'mobile', width: 320, height: 720},
  {name: 'tablet', width: 768, height: 900},
  {name: 'laptop', width: 1024, height: 900},
  {name: 'desktop', width: 1440, height: 960},
]) {
  test(`不同咨询主体解决率在 ${viewport.name} 视口不产生横向溢出`, async ({page}) => {
    await page.setViewportSize(viewport);
    await page.route('**/api/dashboard', route => route.fulfill({json: dashboardPayload}));
    await signInAsAdministrator(page);
    await page.locator('[data-page="dashboard"]').click();
    await expect(page.locator('.resolution-comparison-board')).toBeVisible();

    expect(await page.evaluate(() => document.documentElement.scrollWidth))
      .toBeLessThanOrEqual(viewport.width + 1);
  });
}
