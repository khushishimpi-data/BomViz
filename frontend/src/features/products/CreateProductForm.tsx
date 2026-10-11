import { useState, type SubmitEvent } from 'react';
import { useNavigate } from 'react-router';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Select } from '@/components/ui/select';
import { FormField } from '@/features/auth/FormField';

import {
  describeProductError,
  DESCRIPTION_MAX,
  isProductStatus,
  PRODUCT_CODE_MAX,
  PRODUCT_NAME_MAX,
  PRODUCT_STATUS_LABELS,
  PRODUCT_STATUSES,
  useCreateProduct,
  validateProductInput,
  type FieldErrors,
  type ProductStatus,
} from './api';
import { DescriptionField } from './DescriptionField';

const CODE_ID = 'create-product-code';
const NAME_ID = 'create-product-name';
const DESCRIPTION_ID = 'create-product-description';
const STATUS_ID = 'create-product-status';

type Field = 'productCode' | 'name' | 'description';
const FIELD_IDS: Readonly<Record<Field, string>> = {
  productCode: CODE_ID,
  name: NAME_ID,
  description: DESCRIPTION_ID,
};

export interface CreateProductFormProps {
  workspaceId: string;
}

/** Writers only: create a product, then open it. */
export function CreateProductForm({ workspaceId }: CreateProductFormProps) {
  const createProduct = useCreateProduct(workspaceId);
  const navigate = useNavigate();
  const [productCode, setProductCode] = useState('');
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [status, setStatus] = useState<ProductStatus>('DRAFT');
  const [errors, setErrors] = useState<FieldErrors<Field>>({});
  const [serverError, setServerError] = useState<string | null>(null);

  async function handleSubmit(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setServerError(null);
    const result = validateProductInput({ productCode, name, description, status });
    if (result.kind === 'invalid') {
      setErrors(result.errors);
      const first = (Object.keys(FIELD_IDS) as Field[]).find((field) => result.errors[field]);
      if (first) document.getElementById(FIELD_IDS[first])?.focus();
      return;
    }
    setErrors({});
    try {
      const product = await createProduct.mutateAsync(result.input);
      void navigate(
        `/workspaces/${encodeURIComponent(workspaceId)}/products/${encodeURIComponent(product.id)}`,
      );
    } catch (error) {
      setServerError(describeProductError(error));
    }
  }

  const pending = createProduct.isPending;
  return (
    <form
      noValidate
      aria-label="Create a product"
      className="space-y-4"
      onSubmit={(event) => void handleSubmit(event)}
    >
      {serverError && (
        <Alert variant="destructive">
          <AlertDescription>{serverError}</AlertDescription>
        </Alert>
      )}
      <div className="grid gap-4 sm:grid-cols-[12rem_1fr_10rem] sm:items-start">
        <FormField
          id={CODE_ID}
          label="Product code"
          name="product_code"
          autoComplete="off"
          required
          maxLength={PRODUCT_CODE_MAX}
          value={productCode}
          error={errors.productCode}
          onChange={(event) => {
            setProductCode(event.target.value);
          }}
        />
        <FormField
          id={NAME_ID}
          label="Name"
          name="name"
          autoComplete="off"
          required
          maxLength={PRODUCT_NAME_MAX}
          value={name}
          error={errors.name}
          onChange={(event) => {
            setName(event.target.value);
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
              if (isProductStatus(value)) setStatus(value);
            }}
          >
            {PRODUCT_STATUSES.map((option) => (
              <option key={option} value={option}>
                {PRODUCT_STATUS_LABELS[option]}
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
        {pending ? 'Creating…' : 'Create product'}
      </Button>
    </form>
  );
}
