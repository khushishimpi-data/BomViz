import { useEffect, useId, useRef, type ReactNode, type SyntheticEvent } from 'react';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  /** Explains the consequence; linked to the dialog with `aria-describedby`. */
  description: ReactNode;
  /** Extra detail below the description (e.g. what is about to be affected). */
  children?: ReactNode;
  confirmLabel: string;
  cancelLabel?: string;
  /** Pending: both buttons are disabled and Escape is ignored until the action settles. */
  pending?: boolean;
  /** Failure message shown inside the dialog; it stays open so the user can retry or cancel. */
  error?: string | null;
  destructive?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  className?: string;
}

/**
 * Modal confirmation built on the native `<dialog>` (`showModal()` provides the focus trap,
 * inert background and Escape handling). Focus starts on Cancel — the safe choice — and returns
 * to the element that opened the dialog when it closes, if that element still exists.
 */
export function ConfirmDialog({
  open,
  title,
  description,
  children,
  confirmLabel,
  cancelLabel = 'Cancel',
  pending = false,
  error = null,
  destructive = true,
  onConfirm,
  onCancel,
  className,
}: ConfirmDialogProps) {
  const titleId = useId();
  const descriptionId = useId();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!open || dialog === null) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (!dialog.open) dialog.showModal();
    cancelRef.current?.focus();
    return () => {
      if (dialog.open) dialog.close();
      if (opener?.isConnected) opener.focus();
    };
  }, [open]);

  if (!open) return null;

  function handleNativeCancel(event: SyntheticEvent<HTMLDialogElement>): void {
    // Escape: keep React in charge of closing so state and DOM never disagree.
    event.preventDefault();
    if (!pending) onCancel();
  }

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby={titleId}
      aria-describedby={descriptionId}
      onCancel={handleNativeCancel}
      className={cn(
        'm-auto w-full max-w-md rounded-lg border bg-card p-6 text-card-foreground shadow-lg backdrop:bg-black/50',
        className,
      )}
    >
      <div className="space-y-4">
        <h2 id={titleId} className="text-lg leading-none font-semibold">
          {title}
        </h2>
        <div id={descriptionId} className="text-sm text-muted-foreground">
          {description}
        </div>
        {children}
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        <div className="flex justify-end gap-2">
          <Button
            ref={cancelRef}
            type="button"
            variant="outline"
            disabled={pending}
            onClick={onCancel}
          >
            {cancelLabel}
          </Button>
          <Button
            type="button"
            variant={destructive ? 'destructive' : 'default'}
            disabled={pending}
            onClick={onConfirm}
          >
            {confirmLabel}
          </Button>
        </div>
      </div>
    </dialog>
  );
}
