import { useState, type SubmitEvent } from 'react';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Select } from '@/components/ui/select';
import { FormField } from '@/features/auth/FormField';

import {
  describeProductError,
  DESCRIPTION_MAX,
  isRevisionStatus,
  REVISION_CODE_MAX,
  REVISION_STATUS_LABELS,
  REVISION_STATUSES,
  useCreateRevision,
  validateRevisionInput,
  type FieldErrors,
  type RevisionStatus,
} from './api';
import { DescriptionField } from './DescriptionField';

const CODE_ID = 'create-revision-code';
const DESCRIPTION_ID = 'create-revision-description';
const STATUS_ID = 'create-revision-status';

type Field = 'revisionCode' | 'description';

export interface CreateRevisionFormProps {
  productId: string;
}

/** Writers only: add a revision to a product. */
export function CreateRevisionForm({ productId }: CreateRevisionFormProps) {
  const createRevision = useCreateRevision(productId);
  const [revisionCode, setRevisionCode] = useState('');
  const [description, setDescription] = useState('');
  const [status, setStatus] = useState<RevisionStatus>('DRAFT');
  const [errors, setErrors] = useState<FieldErrors<Field>>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [created, setCreated] = useState<string | null>(null);

  async function handleSubmit(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setServerError(null);
    setCreated(null);
    const result = validateRevisionInput({ revisionCode, description, status });
    if (result.kind === 'invalid') {
      setErrors(result.errors);
      document.getElementById(result.errors.revisionCode ? CODE_ID : DESCRIPTION_ID)?.focus();
      return;
    }
    setErrors({});
    try {
      const revision = await createRevision.mutateAsync(result.input);
      setRevisionCode('');
      setDescription('');
      setStatus('DRAFT');
      setCreated(`Added revision ${revision.revision_code}.`);
    } catch (error) {
      setServerError(describeProductError(error));
    }
  }

  const pending = createRevision.isPending;
  return (
    <form
      noValidate
      aria-label="Add a revision"
      className="space-y-4"
      onSubmit={(event) => void handleSubmit(event)}
    >
      {serverError && (
        <Alert variant="destructive">
          <AlertDescription>{serverError}</AlertDescription>
        </Alert>
      )}
      {created && (
        <Alert variant="success">
          <AlertDescription>{created}</AlertDescription>
        </Alert>
      )}
      <div className="grid gap-4 sm:grid-cols-[12rem_10rem] sm:items-start">
        <FormField
          id={CODE_ID}
          label="Revision code"
          name="revision_code"
          autoComplete="off"
          required
          maxLength={REVISION_CODE_MAX}
          value={revisionCode}
          error={errors.revisionCode}
          onChange={(event) => {
            setRevisionCode(event.target.value);
          }}
        />
        <div className="space-y-2">
          <Label htmlFor={STATUS_ID}>Status</Label>
          <Select
            id={STATUS_ID}
            name="status"
            value={status}
            onChange={(event) => {
              const { value } = event.target;
              if (isRevisionStatus(value)) setStatus(value);
            }}
          >
            {REVISION_STATUSES.map((option) => (
              <option key={option} value={option}>
                {REVISION_STATUS_LABELS[option]}
              </option>
            ))}
          </Select>
        </div>
      </div>
      <DescriptionField
        id={DESCRIPTION_ID}
        value={description}
        error={errors.description}
        maxLength={DESCRIPTION_MAX}
        onChange={setDescription}
      />
      <Button type="submit" disabled={pending}>
        {pending ? 'Adding…' : 'Add revision'}
      </Button>
    </form>
  );
}
