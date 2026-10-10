import { expect, test } from '@playwright/test';

test('application shell loads and every backend dependency is ready', async ({ page }) => {
  await page.goto('/');

  await expect(
    page.getByRole('heading', { name: 'Manufacturing BOMs, made understandable' }),
  ).toBeVisible();
  await expect(page.getByText('All systems operational')).toBeVisible();
});

test('unknown routes show the not-found page with a way home', async ({ page }) => {
  await page.goto('/definitely/not/a/page');

  await expect(page.getByRole('heading', { name: 'Page not found' })).toBeVisible();
  await page.getByRole('link', { name: 'Back to home' }).click();
  await expect(page).toHaveURL('/');
});

test('API documentation is served through the frontend proxy', async ({ request }) => {
  const response = await request.get('/api/v1/openapi.json');

  expect(response.ok()).toBe(true);
  expect(await response.json()).toMatchObject({ info: { title: 'BOMViz API' } });
});
