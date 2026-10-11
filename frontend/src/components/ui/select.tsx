import { ChevronDown } from 'lucide-react';
import type * as React from 'react';

import { cn } from '@/lib/utils';

export type SelectProps = React.ComponentProps<'select'>;

/**
 * Native `<select>` styled to match `Input`. Native keeps full keyboard and screen-reader
 * support without extra dependencies. Pass `<option>` elements as children.
 */
export function Select({ className, ...props }: SelectProps) {
  return (
    <div data-slot="select-wrapper" className="relative w-full">
      <select
        data-slot="select"
        className={cn(
          'flex h-9 w-full min-w-0 appearance-none rounded-md border border-input bg-transparent py-1 pr-9 pl-3 text-sm shadow-xs transition-colors disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive',
          className,
        )}
        {...props}
      />
      <ChevronDown
        aria-hidden="true"
        className="pointer-events-none absolute top-1/2 right-3 size-4 -translate-y-1/2 text-muted-foreground"
      />
    </div>
  );
}
