import { useState, type SubmitEvent } from 'react';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Select } from '@/components/ui/select';
import { FormField } from '@/features/auth/FormField';

import {
  describeWorkspaceError,
  isWorkspaceRole,
  ROLE_LABELS,
  useAddMember,
  WORKSPACE_ROLES,
  type WorkspaceRole,
} from './api';

const EMAIL_ID = 'add-member-email';
const ROLE_ID = 'add-member-role';
/** Least privilege by default; the admin raises it deliberately. */
const DEFAULT_ROLE: WorkspaceRole = 'VIEWER';

export interface AddMemberFormProps {
  workspaceId: string;
}

/** Admin-only: add an existing BOMViz user to the workspace by email. */
export function AddMemberForm({ workspaceId }: AddMemberFormProps) {
  const addMember = useAddMember(workspaceId);
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<WorkspaceRole>(DEFAULT_ROLE);
  const [emailError, setEmailError] = useState<string | undefined>(undefined);
  const [serverError, setServerError] = useState<string | null>(null);
  const [added, setAdded] = useState<string | null>(null);

  async function handleSubmit(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const trimmed = email.trim();
    setServerError(null);
    setAdded(null);
    if (!trimmed.includes('@')) {
      setEmailError('Enter the email address the person registered with.');
      document.getElementById(EMAIL_ID)?.focus();
      return;
    }
    setEmailError(undefined);
    try {
      const member = await addMember.mutateAsync({ email: trimmed, role });
      setEmail('');
      setAdded(`Added ${member.full_name} as ${ROLE_LABELS[member.role]}.`);
    } catch (error) {
      setServerError(describeWorkspaceError(error));
    }
  }

  const pending = addMember.isPending;
  return (
    <form
      noValidate
      aria-label="Add a member"
      className="space-y-4"
      onSubmit={(event) => void handleSubmit(event)}
    >
      {serverError && (
        <Alert variant="destructive">
          <AlertDescription>{serverError}</AlertDescription>
        </Alert>
      )}
      {added && (
        <Alert variant="success">
          <AlertDescription>{added}</AlertDescription>
        </Alert>
      )}
      <div className="grid gap-4 sm:grid-cols-[1fr_12rem_auto] sm:items-start">
        <FormField
          id={EMAIL_ID}
          label="Email"
          type="email"
          name="email"
          autoComplete="off"
          required
          value={email}
          error={emailError}
          onChange={(event) => {
            setEmail(event.target.value);
          }}
        />
        <div className="space-y-2">
          <Label htmlFor={ROLE_ID}>Role</Label>
          <Select
            id={ROLE_ID}
            name="role"
            value={role}
            onChange={(event) => {
              const { value } = event.target;
              if (isWorkspaceRole(value)) setRole(value);
            }}
          >
            {WORKSPACE_ROLES.map((option) => (
              <option key={option} value={option}>
                {ROLE_LABELS[option]}
              </option>
            ))}
          </Select>
        </div>
        <Button type="submit" className="sm:mt-6" disabled={pending}>
          {pending ? 'Adding…' : 'Add member'}
        </Button>
      </div>
    </form>
  );
}
