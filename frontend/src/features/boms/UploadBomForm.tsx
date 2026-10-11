import { useState, type SubmitEvent } from 'react';
import { Link, useNavigate } from 'react-router';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select } from '@/components/ui/select';
import { FormField } from '@/features/auth/FormField';
import type { FieldErrors } from '@/features/products/api';

import {
  ACCEPTED_EXTENSIONS,
  BOM_NAME_MAX,
  defaultBomName,
  describeBomError,
  existingBomId,
  REVISION_LABEL_MAX,
  useProductOptions,
  useProductRevisionOptions,
  useUploadBom,
  validateBomFile,
  type UploadBomInput,
} from './api';

const FILE_ID = 'upload-bom-file';
const NAME_ID = 'upload-bom-name';
const REVISION_LABEL_ID = 'upload-bom-revision-label';
const PRODUCT_ID = 'upload-bom-product';
const PRODUCT_REVISION_ID = 'upload-bom-product-revision';

type Field = 'file' | 'bomName' | 'revisionLabel';
/** In form order, so the first invalid field is the one that receives focus. */
const FIELD_IDS: Readonly<Record<Field, string>> = {
  file: FILE_ID,
  bomName: NAME_ID,
  revisionLabel: REVISION_LABEL_ID,
};

const LINK_CLASS = 'font-medium underline underline-offset-4';

interface ServerError {
  readonly message: string;
  readonly existingBomId: string | null;
}

export interface UploadBomFormProps {
  workspaceId: string;
}

/** Writers only: upload a CSV or Excel BOM, optionally linked to a product revision. */
export function UploadBomForm({ workspaceId }: UploadBomFormProps) {
  const upload = useUploadBom();
  const products = useProductOptions(workspaceId);
  const navigate = useNavigate();
  const [file, setFile] = useState<File | null>(null);
  const [bomName, setBomName] = useState('');
  const [nameTyped, setNameTyped] = useState(false);
  const [revisionLabel, setRevisionLabel] = useState('');
  const [productId, setProductId] = useState('');
  const [productRevisionId, setProductRevisionId] = useState('');
  const [errors, setErrors] = useState<FieldErrors<Field>>({});
  const [serverError, setServerError] = useState<ServerError | null>(null);
  const product = useProductRevisionOptions(productId);
  const base = `/workspaces/${encodeURIComponent(workspaceId)}/boms`;

  function handleFileChange(chosen: File | null): void {
    setFile(chosen);
    setServerError(null);
    const fileError = chosen ? validateBomFile(chosen) : undefined;
    setErrors((previous) => {
      const next = { ...previous };
      if (fileError === undefined) delete next.file;
      else next.file = fileError;
      return next;
    });
    // Suggest a name from the file until the user types their own.
    if (chosen && (!nameTyped || bomName.trim() === '')) {
      setBomName(defaultBomName(chosen.name));
      setNameTyped(false);
    }
  }

  function validate(): FieldErrors<Field> {
    const found: FieldErrors<Field> = {};
    const fileError = validateBomFile(file);
    if (fileError) found.file = fileError;
    const name = bomName.trim();
    if (name === '') found.bomName = 'Enter a name for this BOM.';
    else if (name.length > BOM_NAME_MAX) {
      found.bomName = `Use at most ${BOM_NAME_MAX} characters.`;
    }
    if (revisionLabel.trim().length > REVISION_LABEL_MAX) {
      found.revisionLabel = `Use at most ${REVISION_LABEL_MAX} characters.`;
    }
    return found;
  }

  async function handleSubmit(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setServerError(null);
    const found = validate();
    setErrors(found);
    const first = (Object.keys(FIELD_IDS) as Field[]).find((field) => found[field]);
    if (first || file === null) {
      if (first) document.getElementById(FIELD_IDS[first])?.focus();
      return;
    }
    const label = revisionLabel.trim();
    const input: UploadBomInput = {
      workspaceId,
      file,
      bomName: bomName.trim(),
      ...(label === '' ? {} : { revisionLabel: label }),
      ...(productId === '' ? {} : { productId }),
      ...(productId === '' || productRevisionId === '' ? {} : { productRevisionId }),
    };
    try {
      const bom = await upload.mutateAsync(input);
      void navigate(`${base}/${encodeURIComponent(bom.id)}`);
    } catch (error) {
      setServerError({ message: describeBomError(error), existingBomId: existingBomId(error) });
    }
  }

  const pending = upload.isPending;
  const fileHintId = `${FILE_ID}-hint`;
  const fileErrorId = `${FILE_ID}-error`;
  const revisions = product.data?.revisions ?? [];
  return (
    <form
      noValidate
      aria-label="Upload a BOM"
      className="max-w-2xl space-y-4"
      onSubmit={(event) => void handleSubmit(event)}
    >
      {serverError && (
        <Alert variant="destructive">
          <AlertDescription>
            <p>{serverError.message}</p>
            {serverError.existingBomId !== null && (
              <Link
                to={`${base}/${encodeURIComponent(serverError.existingBomId)}`}
                className={LINK_CLASS}
              >
                Open the existing BOM
              </Link>
            )}
          </AlertDescription>
        </Alert>
      )}
      <fieldset disabled={pending} className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor={FILE_ID}>BOM file</Label>
          <Input
            id={FILE_ID}
            name="file"
            type="file"
            accept={ACCEPTED_EXTENSIONS.join(',')}
            required
            aria-invalid={errors.file ? true : undefined}
            aria-describedby={errors.file ? `${fileHintId} ${fileErrorId}` : fileHintId}
            onChange={(event) => {
              handleFileChange(event.target.files?.[0] ?? null);
            }}
          />
          <p id={fileHintId} className="text-sm text-muted-foreground">
            CSV, Excel (.xlsx) or Excel 97–2003 (.xls), up to 20 MB.
          </p>
          {errors.file && (
            <p id={fileErrorId} className="text-sm text-destructive">
              {errors.file}
            </p>
          )}
        </div>
        <div className="grid gap-4 sm:grid-cols-[1fr_12rem] sm:items-start">
          <FormField
            id={NAME_ID}
            label="BOM name"
            name="bom_name"
            autoComplete="off"
            required
            maxLength={BOM_NAME_MAX}
            value={bomName}
            error={errors.bomName}
            onChange={(event) => {
              setBomName(event.target.value);
              setNameTyped(true);
            }}
          />
          <FormField
            id={REVISION_LABEL_ID}
            label="Revision label (optional)"
            name="revision_label"
            autoComplete="off"
            maxLength={REVISION_LABEL_MAX}
            value={revisionLabel}
            error={errors.revisionLabel}
            onChange={(event) => {
              setRevisionLabel(event.target.value);
            }}
          />
        </div>
        <div className="grid gap-4 sm:grid-cols-2 sm:items-start">
          <div className="space-y-2">
            <Label htmlFor={PRODUCT_ID}>Product (optional)</Label>
            <Select
              id={PRODUCT_ID}
              name="product_id"
              value={productId}
              disabled={products.data === undefined}
              onChange={(event) => {
                setProductId(event.target.value);
                setProductRevisionId('');
              }}
            >
              <option value="">
                {products.data === undefined && products.error === null
                  ? 'Loading products…'
                  : 'No product'}
              </option>
              {products.data?.items.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.product_code} — {option.name}
                </option>
              ))}
            </Select>
            {products.error && (
              <p className="text-sm text-muted-foreground">
                Products could not be loaded; you can link the BOM later.
              </p>
            )}
          </div>
          <div className="space-y-2">
            <Label htmlFor={PRODUCT_REVISION_ID}>Product revision (optional)</Label>
            <Select
              id={PRODUCT_REVISION_ID}
              name="product_revision_id"
              value={productRevisionId}
              disabled={productId === '' || product.data === undefined}
              onChange={(event) => {
                setProductRevisionId(event.target.value);
              }}
            >
              <option value="">No revision</option>
              {revisions.map((revision) => (
                <option key={revision.id} value={revision.id}>
                  {revision.revision_code}
                </option>
              ))}
            </Select>
          </div>
        </div>
        <Button type="submit">{pending ? 'Uploading…' : 'Upload BOM'}</Button>
      </fieldset>
    </form>
  );
}
