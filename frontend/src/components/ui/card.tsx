import type * as React from 'react';

import { cn } from '@/lib/utils';

export type CardProps = React.ComponentProps<'div'>;

export function Card({ className, ...props }: CardProps) {
  return (
    <div
      data-slot="card"
      className={cn(
        'flex flex-col gap-6 rounded-lg border bg-card py-6 text-card-foreground shadow-sm',
        className,
      )}
      {...props}
    />
  );
}

export function CardHeader({ className, ...props }: React.ComponentProps<'div'>) {
  return (
    <div
      data-slot="card-header"
      className={cn('flex flex-col gap-1.5 px-6', className)}
      {...props}
    />
  );
}

export interface CardTitleProps extends React.ComponentProps<'h2'> {
  /** Heading element to render; pick the level that fits the page outline. */
  as?: 'h1' | 'h2' | 'h3' | 'h4';
}

/** Card heading. Renders a real heading (default `h2`) so it appears in the document outline. */
export function CardTitle({ as: Heading = 'h2', className, ...props }: CardTitleProps) {
  return (
    <Heading
      data-slot="card-title"
      className={cn('text-lg leading-none font-semibold', className)}
      {...props}
    />
  );
}

export function CardDescription({ className, ...props }: React.ComponentProps<'p'>) {
  return (
    <p
      data-slot="card-description"
      className={cn('text-sm text-muted-foreground', className)}
      {...props}
    />
  );
}

export function CardContent({ className, ...props }: React.ComponentProps<'div'>) {
  return <div data-slot="card-content" className={cn('px-6', className)} {...props} />;
}

export function CardFooter({ className, ...props }: React.ComponentProps<'div'>) {
  return (
    <div data-slot="card-footer" className={cn('flex items-center px-6', className)} {...props} />
  );
}
