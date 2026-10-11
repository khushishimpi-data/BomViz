import type * as React from 'react';

import { cn } from '@/lib/utils';

export type LabelProps = React.ComponentProps<'label'>;

/** Visible form label. Always bind it to its control with `htmlFor` (or wrap the control). */
export function Label({ className, ...props }: LabelProps) {
  return (
    <label
      data-slot="label"
      className={cn(
        'flex items-center gap-2 text-sm leading-none font-medium select-none peer-disabled:cursor-not-allowed peer-disabled:opacity-50',
        className,
      )}
      {...props}
    />
  );
}
