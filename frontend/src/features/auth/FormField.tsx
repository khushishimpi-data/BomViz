import { Input, type InputProps } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

export interface FormFieldProps extends Omit<
  InputProps,
  'id' | 'aria-invalid' | 'aria-describedby'
> {
  id: string;
  label: string;
  /** Persistent help text, linked via `aria-describedby`. */
  hint?: string | undefined;
  /** Validation message shown under the field and linked via `aria-describedby`. */
  error?: string | undefined;
}

/** Labelled input with adjacent, programmatically linked hint and error text. */
export function FormField({ id, label, hint, error, ...inputProps }: FormFieldProps) {
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const describedBy = [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(' ');
  return (
    <div className="space-y-2">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy === '' ? undefined : describedBy}
        {...inputProps}
      />
      {hint && (
        <p id={hintId} className="text-sm text-muted-foreground">
          {hint}
        </p>
      )}
      {error && (
        <p id={errorId} className="text-sm text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}
