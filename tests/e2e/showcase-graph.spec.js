import {expect, test} from '@playwright/test';

async function signInAsAdministrator(page) {
  await page.goto('/');
  await page.locator('#login-username').fill('admin');
  await page.locator('#login-password').fill('Admin@12366');
  await page.locator('#login-submit').click();
  await expect(page.locator('#login-screen')).toHaveClass(/hidden/);
}

for (const viewport of [
  {name: 'mobile', width: 320, height: 720},
  {name: 'tablet', width: 768, height: 900},
  {name: 'laptop', width: 1024, height: 900},
  {name: 'desktop', width: 1440, height: 960},
]) {
  test(`登录与工作台在 ${viewport.name} 视口不产生横向溢出`, async ({page}) => {
    await page.setViewportSize(viewport);
    await page.goto('/');
    await expect(page.locator('#login-screen')).toBeVisible();
    await expect(page.locator('#login-form')).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width + 1);

    await signInAsAdministrator(page);
    await expect(page.locator('#page-workbench')).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width + 1);
  });
}

test('管理员离开并再次进入后仍可操作全局图谱', async ({page}) => {
  await page.addInitScript(() => {
    const scheduleFrame = window.requestAnimationFrame.bind(window);
    window.__playwrightScheduledFrames = 0;
    window.requestAnimationFrame = callback => {
      window.__playwrightScheduledFrames += 1;
      return scheduleFrame(callback);
    };
  });
  await signInAsAdministrator(page);

  await page.locator('[data-page="showcase"]').click();
  const canvas = page.locator('#profile-knowledge-content .knowledge-graph-canvas');
  await expect(canvas).toBeVisible();
  await expect(canvas).toHaveAttribute('role', 'img');
  await expect(
    page.getByRole('heading', {name: '完整分类与判定规则'}),
  ).toBeVisible();

  await page.getByRole('button', {name: '暂停旋转'}).click();
  await expect(
    page.getByRole('button', {name: '继续旋转'}),
  ).toBeVisible();

  await page.locator('[data-page="workbench"]').click();
  await expect(page.locator('#page-workbench')).toBeVisible();
  const framesBeforeReentry = await page.evaluate(
    () => window.__playwrightScheduledFrames,
  );

  await page.locator('[data-page="showcase"]').click();
  await expect(canvas).toBeVisible();
  await expect(page.getByRole('button', {name: '暂停旋转'})).toBeVisible();
  await expect
    .poll(() => page.evaluate(() => window.__playwrightScheduledFrames))
    .toBeGreaterThan(framesBeforeReentry);
});

test('图谱工具栏可通过键盘切换旋转状态', async ({page}) => {
  await signInAsAdministrator(page);
  await page.locator('[data-page="showcase"]').click();

  const rotationButton = page.getByRole('button', {name: '暂停旋转'});
  await rotationButton.focus();
  await page.keyboard.press('Space');
  await expect(page.getByRole('button', {name: '继续旋转'})).toBeFocused();
  await page.keyboard.press('Space');
  await expect(page.getByRole('button', {name: '暂停旋转'})).toBeFocused();
});

test('减弱动效偏好下图谱默认不自动旋转', async ({page}) => {
  await page.emulateMedia({reducedMotion: 'reduce'});
  await page.goto('/');
  expect(
    await page.evaluate(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches),
  ).toBe(true);
  await page.locator('#login-username').fill('admin');
  await page.locator('#login-password').fill('Admin@12366');
  await page.locator('#login-submit').click();
  await page.locator('[data-page="showcase"]').click();
  await expect(page.getByRole('button', {name: '继续旋转'})).toBeVisible();
});

test('三个画像类别共享相近空间区域且类别内部保持错落', async ({page}) => {
  const categoryLabels = [
    '专业', '了解', '小白',
    '平稳', '焦虑', '不满',
    '对坐席不满', '历史工单', '存在联系相关部门或人',
    '异常中断', '等待推诿', '近五个工作日未命中',
  ];
  await page.addInitScript((labels) => {
    window.__graphCategoryPoints = {};
    const contextPrototype = window.CanvasRenderingContext2D.prototype;
    const originalArc = contextPrototype.arc;
    const originalFillText = contextPrototype.fillText;
    let lastArc = null;
    contextPrototype.arc = function(x, y, radius, ...rest) {
      lastArc = {x, y, radius};
      return originalArc.call(this, x, y, radius, ...rest);
    };
    contextPrototype.fillText = function(value, ...rest) {
      const label = String(value);
      if (lastArc && labels.includes(label)) {
        window.__graphCategoryPoints[label] = {...lastArc};
      }
      return originalFillText.call(this, value, ...rest);
    };
  }, categoryLabels);
  await page.emulateMedia({reducedMotion: 'reduce'});
  await signInAsAdministrator(page);
  await page.locator('[data-page="showcase"]').click();
  await expect(page.locator('.knowledge-graph-canvas')).toBeVisible();

  await expect.poll(() => page.evaluate(
    labels => labels.every(label => window.__graphCategoryPoints[label]),
    categoryLabels,
  )).toBe(true);
  const points = await page.evaluate(() => window.__graphCategoryPoints);
  const groups = [
    ['专业', '了解', '小白'],
    ['平稳', '焦虑', '不满'],
    ['对坐席不满', '历史工单', '存在联系相关部门或人', '异常中断', '等待推诿', '近五个工作日未命中'],
  ];
  const regions = groups.map(labels => {
    const groupPoints = labels.map(label => points[label]);
    const xs = groupPoints.map(point => point.x);
    const ys = groupPoints.map(point => point.y).sort((a, b) => a - b);
    const yGaps = ys.slice(1).map((value, index) => value - ys[index]);
    expect(Math.min(...yGaps)).toBeGreaterThan(16);
    return {
      centerX: (Math.min(...xs) + Math.max(...xs)) / 2,
      width: Math.max(...xs) - Math.min(...xs),
    };
  });
  expect(Math.max(...regions.map(region => region.centerX))
    - Math.min(...regions.map(region => region.centerX))).toBeLessThan(45);
  expect(Math.max(...regions.map(region => region.width))
    - Math.min(...regions.map(region => region.width))).toBeLessThan(20);
});
