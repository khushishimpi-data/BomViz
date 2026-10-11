import { useState, type SubmitEvent } from 'react';
import { useNavigate } from 'react-router';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { FormField } from '@/features/auth/FormField';
import { writeLastWorkspaceId } from '@/features/auth/last-workspace';
import { useAuth } from '@/features/auth/useAuth';

import { describeWorkspaceError, useCreateWorkspace, type CreateWorkspaceInput } from './api';

const NAME_MAX = 100;
const SLUG_MIN = 3;
const SLUG_MAX = 48;
const SLUG_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

const NAME_ID = 'workspace-name';
const SLUG_ID = 'workspace-slug';

interface FieldErrors {
  name?: string;
  slug?: string;
}

/** Mirrors the server's create rules so most mistakes are caught before a request. */
function validate(name: string, slug: string): FieldErrors {
  const errors: FieldErrors = {};
  if (name === '') errors.name = 'Enter a workspace name.';
  else if (name.length > NAME_MAX) errors.name = `Use at most ${NAME_MAX} characters.`;
  if (slug !== '') {
    if (slug.length < SLUG_MIN || slug.length > SLUG_MAX) {
      errors.slug = `Use ${SLUG_MIN}–${SLUG_MAX} characters, or leave it blank.`;
    } else if (!SLUG_PATTERN.test(slug)) {
      errors.slug = 'Use lowercase letters and digits, separated by single hyphens.';
    }
  }
  return errors;
}

/** Create a workspace; the creator becomes its admin and lands on it. */
export function CreateWorkspaceForm() {
  const { refreshUser } = useAuth();
  const navigate = useNavigate();
  const createWorkspace = useCreateWorkspace();
  const [name, setName] = useState('');
  const [slug, setSlug] = useState('');
  const [errors, setErrors] = useState<FieldErrors>({});
  const [serverError, setServerError] = useState<string | null>(null);

  async function handleSubmit(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const trimmedName = name.trim();
    const trimmedSlug = slug.trim();
    const nextErrors = validate(trimmedName, trimmedSlug);
    setErrors(nextErrors);
    setServerError(null);
    if (nextErrors.name) {
      document.getElementById(NAME_ID)?.focus();
      return;
    }
    if (nextErrors.slug) {
      document.getElementById(SLUG_ID)?.focus();
      return;
    }

    const input: CreateWorkspaceInput =
      trimmedSlug === '' ? { name: trimmedName } : { name: trimmedName, slug: trimmedSlug };
    try {
      const workspace = await createWorkspace.mutateAsync(input);
      // The switcher reads memberships from the profile, so reload it before moving on.
      await refreshUser().catch(() => undefined);
      writeLastWorkspaceId(workspace.id);
      void navigate(`/workspaces/${encodeURIComponent(workspace.id)}`);
    } catch (error) {
      setServerError(describeWorkspaceError(error));
    }
  }

  const pending = createWorkspace.isPending;
  return (
    <form
      noValidate
      aria-label="Create a workspace"
      className="space-y-4"
      onSubmit={(event) => void handleSubmit(event)}
    >
      {serverError && (
        <Alert variant="destructive">
          <AlertDescription>{serverError}</AlertDescription>
        </Alert>
      )}
      <FormField
        id={NAME_ID}
        label="Name"
        name="name"
        required
        maxLength={NAME_MAX}
        value={name}
        error={errors.name}
        onChange={(event) => {
          setName(event.target.value);
        }}
      />
      <FormField
        id={SLUG_ID}
        label="Slug (optional)"
        name="slug"
        hint="Used in links. Leave blank to generate one from the name."
        value={slug}
        error={errors.slug}
        onChange={(event) => {
          setSlug(event.target.value);
        }}
      />
      <Button type="submit" disabled={pending}>
        {pending ? 'Creating…' : 'Create workspace'}
      </Button>
    </form>
  );
}
