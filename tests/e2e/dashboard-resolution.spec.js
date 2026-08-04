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
  unresolved_question_hotspots: {
    all: [
      {label: '电子税务局申报提交后状态长时间未更新，需核验受理及处理进度', value: 6},
      {label: '缴款渠道异常', value: 4},
      {label: '跨部门事项反馈进度不明确', value: 3},
      {label: '线上更正申报结果未同步', value: 2},
      {label: '历史工单处理结果待确认', value: 1},
    ],
    personal: [{label: '个人所得税专项附加扣除', value: 3}],
    enterprise: [{label: '企业申报进度查询', value: 5}],
  },
  unresolved_distributions: {
    topics: [{label: '增值税及附加税费申报办理', unresolved_rate: 64.2}],
    demands: [{label: '跨部门受理进度与办理结果查询', unresolved_rate: 51.7}],
  },
};

test('不同咨询主体解决率使用克制双柱和四行横向进度布局', async ({page}) => {
  await page.route('**/api/dashboard', route => route.fulfill({json: dashboardPayload}));
  await signInAsAdministrator(page);
  await page.locator('[data-page="dashboard"]').click();

  const chart = page.locator('#caller-resolution-chart');
  await expect(chart.getByRole('heading', {name: '一级咨询主体'})).toBeVisible();
  await expect(chart.getByRole('heading', {name: '企业二级身份'})).toBeVisible();

  const personal = chart.locator('.resolution-primary-bar', {hasText: '个人'});
  await expect(personal).toContainText('72.7%');
  await expect(personal.locator('[role="meter"]')).toHaveAttribute(
    'aria-label',
    '个人已直接解决率 72.7%',
  );
  await expect(chart.locator('.resolution-primary-bars')).toHaveCount(1);
  await expect(chart.locator('.resolution-primary-bar')).toHaveCount(2);
  await expect(chart.locator('.resolution-primary-baseline')).toBeVisible();

  const identityLabels = await chart
    .locator('.resolution-identity-bars .resolution-subject-name')
    .allTextContents();
  expect(identityLabels).toEqual(['办税人员', '法定代表人', '财务负责人', '其他身份']);
  const identityItems = chart.locator('.resolution-identity-bars .resolution-rate-item');
  const identityBoxes = await Promise.all([0, 1, 2, 3].map(index => identityItems.nth(index).boundingBox()));
  expect(identityBoxes.every(box => Math.abs(box.x - identityBoxes[0].x) <= 1)).toBe(true);
  expect(identityBoxes[1].y).toBeGreaterThan(identityBoxes[0].y);
  expect(identityBoxes[2].y).toBeGreaterThan(identityBoxes[1].y);
  expect(identityBoxes[3].y).toBeGreaterThan(identityBoxes[2].y);
  const identityColors = await identityItems.evaluateAll(items => items.map(item =>
    getComputedStyle(item.querySelector('.resolution-rate-fill')).backgroundColor,
  ));
  expect(new Set(identityColors).size).toBe(1);
  expect(await chart.locator('.resolution-primary-fill').first().evaluate(
    node => getComputedStyle(node).backgroundImage,
  )).toBe('none');
  expect(await chart.locator('.resolution-primary-value').first().evaluate(
    node => getComputedStyle(node).borderTopWidth,
  )).toBe('0px');

  const primaryItems = chart.locator('.resolution-primary-bars .resolution-primary-bar');
  const firstPrimaryBox = await primaryItems.nth(0).boundingBox();
  const secondPrimaryBox = await primaryItems.nth(1).boundingBox();
  expect(Math.abs(firstPrimaryBox.y - secondPrimaryBox.y)).toBeLessThanOrEqual(1);
  expect(secondPrimaryBox.x).toBeGreaterThan(firstPrimaryBox.x);

  const boardBox = await chart.locator('.resolution-comparison-board').boundingBox();
  expect(boardBox.height).toBeLessThanOrEqual(340);
});

test('未直接解决问题使用对齐的优先级排行并完整展示长问题名称', async ({page}) => {
  await page.route('**/api/dashboard', route => route.fulfill({json: dashboardPayload}));
  await signInAsAdministrator(page);
  await page.locator('[data-page="dashboard"]').click();

  const hotspots = page.locator('#unresolved-hotspots');
  await expect(hotspots.locator('.hotspot-priority-section')).toHaveCount(3);
  await expect(hotspots.getByRole('heading', {name: '总体热点'})).toBeVisible();
  await expect(hotspots.locator('.hotspot-priority-count')).toHaveCount(0);
  await expect(hotspots.locator('.hotspot-priority-copy').first()).toContainText(
    '电子税务局申报提交后状态长时间未更新，需核验受理及处理进度',
  );
  await expect(hotspots.locator('.hotspot-rank').first()).toHaveText('01');
  await expect(hotspots.locator('.hotspot-priority-level').first()).toHaveText('高优先');
  const overallLevels = hotspots.locator('.hotspot-priority-section').first().locator('.hotspot-priority-level');
  await expect(overallLevels).toHaveText(['高优先', '重点', '重点', '重点', '重点']);
  await expect(hotspots.locator('.hotspot-priority-item.normal')).toHaveCount(0);
  expect(await hotspots.locator('.hotspot-priority-copy strong').first().evaluate(
    node => getComputedStyle(node).whiteSpace,
  )).toBe('normal');
  const hotspotTracks = hotspots.locator('.hotspot-priority-section').first().locator('.hotspot-priority-track');
  const firstTrackBox = await hotspotTracks.nth(0).boundingBox();
  const secondTrackBox = await hotspotTracks.nth(1).boundingBox();
  expect(Math.abs(firstTrackBox.x - secondTrackBox.x)).toBeLessThanOrEqual(1);
  expect(Math.abs(firstTrackBox.width - secondTrackBox.width)).toBeLessThanOrEqual(1);
});

test('专题和需求类别以紧凑的双行信息层级突出名称', async ({page}) => {
  await page.route('**/api/dashboard', route => route.fulfill({json: dashboardPayload}));
  await signInAsAdministrator(page);
  await page.locator('[data-page="dashboard"]').click();

  const topicLabel = page.locator('#category-chart .distribution-rate-row strong');
  const demandLabel = page.locator('#demand-chart .distribution-rate-row strong');
  await expect(topicLabel).toContainText('增值税及附加税费申报办理');
  await expect(demandLabel).toContainText('跨部门受理进度与办理结果查询');
  expect(await topicLabel.evaluate(node => getComputedStyle(node).fontSize)).toBe('14px');
  expect(await topicLabel.evaluate(node => getComputedStyle(node).whiteSpace)).toBe('normal');
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
