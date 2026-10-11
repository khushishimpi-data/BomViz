import { expect, test, type Page } from '@playwright/test';

/**
 * Phase 1 journey against the real stack: account, workspace, product, revision, session restore,
 * member add/remove and sign-out.
 *
 * Registration is rate limited per IP (5/hour), so the whole journey is one test that registers
 * exactly two users. Everything it creates is uniquely named per run; Phase 1 has no endpoints to
 * delete users, workspaces or products, so that data is left behind rather than cleaned up.
 */

const PASSWORD = 'correct-horse-battery-staple';

function uniqueSuffix(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function signInForm(page: Page) {
  return page.getByRole('form', { name: 'Sign in' });
}

test('a user signs up, builds a workspace, manages members and signs out', async ({
  page,
  request,
}) => {
  // Argon2 hashing and several full page loads make this longer than a single-page check.
  test.setTimeout(120_000);

  const suffix = uniqueSuffix();
  const userA = {
    full_name: `E2E Owner ${suffix}`,
    email: `e2e-a-${suffix}@example.com`,
    password: PASSWORD,
  };
  const userB = {
    full_name: `E2E Viewer ${suffix}`,
    email: `e2e-b-${suffix}@example.com`,
    password: PASSWORD,
  };
  const workspaceName = `E2E Workspace ${suffix}`;
  const productCode = `E2E-${suffix}`.toUpperCase();
  const productName = `E2E Product ${suffix}`;
  const revisionCode = 'A';

  await test.step('register through the UI', async () => {
    await page.goto('/register');
    const form = page.getByRole('form', { name: 'Create account' });
    await form.getByLabel('Full name').fill(userA.full_name);
    await form.getByLabel('Email').fill(userA.email);
    await form.getByLabel('Password').fill(userA.password);
    await form.getByRole('button', { name: 'Create account' }).click();

    // A brand-new user has no workspace, so they land on the workspace list.
    await expect(page).toHaveURL('/workspaces');
    await expect(page.getByRole('heading', { name: 'Workspaces', level: 1 })).toBeVisible();
    await expect(page.getByText(userA.full_name, { exact: true })).toBeVisible();
  });

  await test.step('sign out and sign back in with the new password', async () => {
    await page.getByRole('button', { name: 'Sign out' }).click();
    await expect(page).toHaveURL('/');
    await page
      .getByRole('navigation', { name: 'Account' })
      .getByRole('link', { name: 'Sign in' })
      .click();

    await expect(page.getByRole('heading', { name: 'Sign in', level: 1 })).toBeVisible();
    await signInForm(page).getByLabel('Email').fill(userA.email);
    await signInForm(page).getByLabel('Password').fill(userA.password);
    await signInForm(page).getByRole('button', { name: 'Sign in' }).click();

    await expect(page).toHaveURL('/workspaces');
    await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible();
  });

  await test.step('create a workspace', async () => {
    const form = page.getByRole('form', { name: 'Create a workspace' });
    await form.getByLabel('Name').fill(workspaceName);
    await form.getByRole('button', { name: 'Create workspace' }).click();

    await expect(page).toHaveURL(/\/workspaces\/[0-9a-f-]{36}$/);
    await expect(page.getByRole('heading', { name: workspaceName, level: 1 })).toBeVisible();
    await expect(page.getByText('Your role: Admin')).toBeVisible();
  });

  const workspacePath = new URL(page.url()).pathname;

  await test.step('create a product and open it from the list', async () => {
    await page
      .getByRole('navigation', { name: 'Workspace sections' })
      .getByRole('link', { name: 'Products' })
      .click();
    await expect(page.getByRole('heading', { name: 'Products', level: 1 })).toBeVisible();
    await expect(page.getByText('No products yet. Create the first one above.')).toBeVisible();

    const form = page.getByRole('form', { name: 'Create a product' });
    await form.getByLabel('Product code').fill(productCode);
    await form.getByLabel('Name').fill(productName);
    await form.getByRole('button', { name: 'Create product' }).click();
    await expect(page.getByRole('heading', { name: productName, level: 1 })).toBeVisible();

    // Back to the list, then open the product the way a returning user would.
    await page.getByRole('main').getByRole('link', { name: 'Products', exact: true }).click();
    const products = page.getByRole('table', { name: `Products in ${workspaceName}` });
    await expect(products.getByRole('row')).toHaveCount(2); // header + the one product
    await products.getByRole('link', { name: productCode }).click();

    await expect(page).toHaveURL(new RegExp(`^.*${workspacePath}/products/[0-9a-f-]{36}$`));
    await expect(page.getByRole('heading', { name: productName, level: 1 })).toBeVisible();
    await expect(page.getByText('No revisions yet.')).toBeVisible();
  });

  const productPath = new URL(page.url()).pathname;
  const revisions = page.getByRole('table', { name: `Revisions of ${productName}, newest first` });

  await test.step('add a revision', async () => {
    const form = page.getByRole('form', { name: 'Add a revision' });
    await form.getByLabel('Revision code').fill(revisionCode);
    await form.getByRole('button', { name: 'Add revision' }).click();

    await expect(form.getByText(`Added revision ${revisionCode}.`)).toBeVisible();
    await expect(
      revisions.getByRole('rowheader', { name: revisionCode, exact: true }),
    ).toBeVisible();
    await expect(page.getByText('No revisions yet.')).toBeHidden();
  });

  await test.step('reload: the refresh cookie restores the session', async () => {
    await page.reload();

    await expect(page).toHaveURL(productPath);
    await expect(page.getByRole('heading', { name: productName, level: 1 })).toBeVisible();
    await expect(
      revisions.getByRole('rowheader', { name: revisionCode, exact: true }),
    ).toBeVisible();
    await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Sign in' })).toBeHidden();
  });

  await test.step('register a second user through the API', async () => {
    const response = await request.post('/api/v1/auth/register', { data: userB });
    expect(response.status(), await response.text()).toBe(201);
  });

  const members = page.getByRole('table', { name: `Members of ${workspaceName}` });
  const memberB = members.getByRole('rowheader', { name: userB.full_name });

  await test.step('add the second user as a viewer', async () => {
    await page.goto(`${workspacePath}/members`);
    await expect(page.getByRole('heading', { name: 'Members', level: 1 })).toBeVisible();
    await expect(memberB).toBeHidden();

    const form = page.getByRole('form', { name: 'Add a member' });
    await form.getByLabel('Email').fill(userB.email);
    await form.getByLabel('Role', { exact: true }).selectOption('VIEWER');
    await form.getByRole('button', { name: 'Add member' }).click();

    await expect(form.getByText(`Added ${userB.full_name} as Viewer.`)).toBeVisible();
    await expect(memberB).toBeVisible();
    await expect(page.getByLabel(`Role for ${userB.full_name}`)).toHaveValue('VIEWER');
  });

  await test.step('remove the second user through the confirmation dialog', async () => {
    const removeRequests: string[] = [];
    page.on('request', (req) => {
      if (req.method() === 'DELETE' && req.url().includes('/members/')) {
        removeRequests.push(req.url());
      }
    });

    const dialog = page.getByRole('dialog', { name: 'Remove member?' });
    await page.getByRole('button', { name: `Remove ${userB.full_name}` }).click();
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText(userB.full_name, { exact: true })).toBeVisible();
    await expect(dialog.getByText(userB.email, { exact: true })).toBeVisible();
    await expect(dialog.getByText('Viewer', { exact: true })).toBeVisible();
    // The dialog is about B, not the admin.
    await expect(dialog.getByText(userA.email, { exact: true })).toBeHidden();

    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(dialog).toBeHidden();
    await expect(memberB).toBeVisible();
    expect(removeRequests).toEqual([]);

    await page.getByRole('button', { name: `Remove ${userB.full_name}` }).click();
    await expect(dialog).toBeVisible();
    const [response] = await Promise.all([
      page.waitForResponse(
        (res) => res.request().method() === 'DELETE' && res.url().includes('/members/'),
      ),
      dialog.getByRole('button', { name: 'Remove member' }).click(),
    ]);
    expect(response.status()).toBe(204);

    await expect(dialog).toBeHidden();
    await expect(memberB).toBeHidden();
    await expect(members.getByRole('rowheader', { name: userA.full_name })).toBeVisible();
    expect(removeRequests).toHaveLength(1);
  });

  await test.step('sign out: protected pages send you to sign in', async () => {
    await page.getByRole('button', { name: 'Sign out' }).click();
    await expect(page).toHaveURL('/');
    await expect(page.getByRole('button', { name: 'Sign out' })).toBeHidden();

    await page.goto(productPath);
    await expect(page).toHaveURL(`/login?${new URLSearchParams({ next: productPath }).toString()}`);
    await expect(page.getByRole('heading', { name: 'Sign in', level: 1 })).toBeVisible();
    await expect(page.getByRole('heading', { name: productName })).toBeHidden();
  });
});
