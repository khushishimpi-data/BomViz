import type * as React from 'react';

import { cn } from '@/lib/utils';

export type InputProps = React.ComponentProps<'input'>;

/** Text-like input. Pair with `<Label htmlFor>`; set `aria-invalid` to show the error state. */
export function Input({ className, type = 'text', ...props }: InputProps) {
  return (
    <input
      type={type}
      data-slot="input"
      className={cn(
        'flex h-9 w-full min-w-0 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-xs transition-colors placeholder:text-muted-foreground disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive file:border-0 file:bg-transparent file:text-sm file:font-medium',
        className,
      )}
      {...props}
    />
  );
}
