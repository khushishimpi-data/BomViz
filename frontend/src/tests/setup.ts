import '@testing-library/jest-dom/vitest';
import { cleanup, configure } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, vi } from 'vitest';

import { server } from './server';

// Signed-in pages chain refresh → /auth/me → page queries; the first test in a file can exceed
// the 1 s default while the full suite runs in parallel on a constrained machine.
configure({ asyncUtilTimeout: 3000 });
// UI-driven flows (typing, multi-step sign-in) need more than the 5 s default under the same load.
vi.setConfig({ testTimeout: 15_000 });

// jsdom has no modal dialog behaviour. Polyfill the parts the app relies on: `showModal()` /
// `close()` toggling `open`, and Escape firing a cancelable `cancel` event that closes the
// dialog unless prevented — as browsers do.
if (typeof HTMLDialogElement.prototype.showModal !== 'function') {
  HTMLDialogElement.prototype.showModal = function showModal(this: HTMLDialogElement): void {
    this.setAttribute('open', '');
  };
  HTMLDialogElement.prototype.close = function close(this: HTMLDialogElement): void {
    if (!this.hasAttribute('open')) return;
    this.removeAttribute('open');
    this.dispatchEvent(new Event('close'));
  };
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    const dialogs = document.querySelectorAll<HTMLDialogElement>('dialog[open]');
    const top = dialogs[dialogs.length - 1];
    if (top === undefined) return;
    const cancel = new Event('cancel', { cancelable: true });
    if (top.dispatchEvent(cancel)) top.close();
  });
}

function readBlob(blob: Blob): Promise<ArrayBuffer> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      if (reader.result instanceof ArrayBuffer) resolve(reader.result);
      else reject(new Error('FileReader did not produce an ArrayBuffer.'));
    };
    reader.onerror = () => {
      reject(reader.error ?? new Error('FileReader failed.'));
    };
    reader.readAsArrayBuffer(blob);
  });
}

// jsdom's `Blob`/`File` lack `arrayBuffer()`, `text()` and `stream()`, so Node's `fetch` cannot
// serialize a `File` inside `FormData` (the request body never finishes). Polyfill them for
// every test; a no-op where the methods already exist.
if (!('stream' in Blob.prototype)) {
  Object.defineProperties(Blob.prototype, {
    arrayBuffer: {
      configurable: true,
      writable: true,
      value(this: Blob): Promise<ArrayBuffer> {
        return readBlob(this);
      },
    },
    text: {
      configurable: true,
      writable: true,
      async value(this: Blob): Promise<string> {
        return new TextDecoder().decode(await readBlob(this));
      },
    },
    stream: {
      configurable: true,
      writable: true,
      value(this: Blob): ReadableStream<Uint8Array> {
        return new ReadableStream<Uint8Array>({
          start: async (controller) => {
            controller.enqueue(new Uint8Array(await readBlob(this)));
            controller.close();
          },
        });
      },
    },
  });
}

beforeAll(() => {
  server.listen({ onUnhandledRequest: 'error' });
});
afterEach(() => {
  server.resetHandlers();
  cleanup();
});
afterAll(() => {
  server.close();
});
