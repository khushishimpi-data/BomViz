import { Label } from '@/components/ui/label';

export interface DescriptionFieldProps {
  id: string;
  value: string;
  maxLength: number;
  error: string | undefined;
  onChange: (value: string) => void;
}

/** Optional multi-line description with a linked hint and error, styled like `Input`. */
export function DescriptionField({ id, value, maxLength, error, onChange }: DescriptionFieldProps) {
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  return (
    <div className="space-y-2">
      <Label htmlFor={id}>Description (optional)</Label>
      <textarea
        id={id}
        name="description"
        rows={3}
        maxLength={maxLength}
        value={value}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${hintId} ${errorId}` : hintId}
        className="flex w-full min-w-0 rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs transition-colors placeholder:text-muted-foreground disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive"
        onChange={(event) => {
          onChange(event.target.value);
        }}
      />
      <p id={hintId} className="text-sm text-muted-foreground">
        Up to {maxLength} characters.
      </p>
      {error && (
        <p id={errorId} className="text-sm text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}
