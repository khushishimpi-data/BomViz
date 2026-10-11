import { cva, type VariantProps } from 'class-variance-authority';
import type * as React from 'react';

import { cn } from '@/lib/utils';

export const alertVariants = cva(
  'relative grid w-full grid-cols-[0_1fr] items-start gap-y-0.5 rounded-lg border px-4 py-3 text-sm has-[>svg]:grid-cols-[1rem_1fr] has-[>svg]:gap-x-3 [&>svg]:size-4 [&>svg]:translate-y-0.5',
  {
    variants: {
      variant: {
        default: 'bg-card text-card-foreground',
        destructive: 'border-destructive/30 bg-destructive/10 text-destructive',
        success: 'border-success/30 bg-success/10 text-success',
        warning: 'border-warning/30 bg-warning/10 text-warning',
        info: 'border-info/30 bg-info/10 text-info',
      },
    },
    defaultVariants: { variant: 'default' },
  },
);

export interface AlertProps
  extends React.ComponentProps<'div'>, VariantProps<typeof alertVariants> {}

/**
 * Inline message. Errors (`destructive`) use `role="alert"` so they are announced immediately;
 * other variants use the polite `role="status"`. Pass `role` explicitly to override.
 */
export function Alert({ className, variant, role, ...props }: AlertProps) {
  return (
    <div
      data-slot="alert"
      role={role ?? (variant === 'destructive' ? 'alert' : 'status')}
      className={cn(alertVariants({ variant }), className)}
      {...props}
    />
  );
}

export function AlertTitle({ className, ...props }: React.ComponentProps<'p'>) {
  return (
    <p
      data-slot="alert-title"
      className={cn('col-start-2 font-medium tracking-tight', className)}
      {...props}
    />
  );
}

export function AlertDescription({ className, ...props }: React.ComponentProps<'div'>) {
  return (
    <div
      data-slot="alert-description"
      className={cn('col-start-2 text-sm [&_p]:leading-relaxed', className)}
      {...props}
    />
  );
}
