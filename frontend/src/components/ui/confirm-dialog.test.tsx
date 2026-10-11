import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { ConfirmDialog } from './confirm-dialog';

interface HarnessProps {
  onConfirm?: () => void;
  error?: string | null;
}

function Harness({ onConfirm = () => undefined, error = null }: HarnessProps) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        onClick={() => {
          setOpen(true);
        }}
      >
        Delete thing
      </button>
      <ConfirmDialog
        open={open}
        title="Delete thing?"
        description="This cannot be undone."
        confirmLabel="Delete"
        error={error}
        onConfirm={onConfirm}
        onCancel={() => {
          setOpen(false);
        }}
      >
        <p>Thing #42</p>
      </ConfirmDialog>
    </>
  );
}

describe('ConfirmDialog', () => {
  it('renders nothing while closed', () => {
    render(<Harness />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('opens a labelled, described modal and moves focus to Cancel', async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(screen.getByRole('button', { name: 'Delete thing' }));

    const dialog = screen.getByRole('dialog', { name: 'Delete thing?' });
    expect(dialog).toHaveAccessibleDescription('This cannot be undone.');
    expect(dialog).toHaveAttribute('open');
    expect(screen.getByText('Thing #42')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus();
  });

  it('closes on Cancel and returns focus to the trigger without confirming', async () => {
    const onConfirm = vi.fn();
    const user = userEvent.setup();
    render(<Harness onConfirm={onConfirm} />);
    const trigger = screen.getByRole('button', { name: 'Delete thing' });

    await user.click(trigger);
    await user.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('closes on Escape and returns focus to the trigger without confirming', async () => {
    const onConfirm = vi.fn();
    const user = userEvent.setup();
    render(<Harness onConfirm={onConfirm} />);
    const trigger = screen.getByRole('button', { name: 'Delete thing' });

    await user.click(trigger);
    await user.keyboard('{Escape}');

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('calls onConfirm from the explicit confirm button and shows an error inside', async () => {
    const onConfirm = vi.fn();
    const user = userEvent.setup();
    render(<Harness onConfirm={onConfirm} error="It is still in use." />);

    await user.click(screen.getByRole('button', { name: 'Delete thing' }));
    await user.click(screen.getByRole('button', { name: 'Delete' }));

    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('alert')).toHaveTextContent('It is still in use.');
  });
});
