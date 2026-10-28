// Which span's note a page shows: one at a time, shared by its marks, so a mark mounts nothing of its own.
// A hover waits NOTE_DELAY_MS; focus, a tap, or a hover just after another note closed show it at once.
import { NOTE_CLOSE_DELAY_MS, NOTE_DELAY_MS } from "../constants";
import { createStore, type Store } from "../store";

export type Via = "hover" | "focus" | "tap";

export interface Shown {
  readonly spanId: string;
  readonly anchor: Element; // the mark it's shown over
  readonly via: Via;
}

export interface NoteState {
  readonly shown: Shown | null;
  readonly last: Shown | null; // the one shown last, kept while it closes
}

export interface Notes {
  readonly store: Store<NoteState>;
  show(spanId: string, anchor: Element, via: Via): void;
  /** Close the span's note, or stop it opening; another span's is left alone. */
  hide(spanId: string): void;
  /** Close the span's note after a moment, or stop it opening: the pointer may be crossing between its mark and the note. */
  hideSoon(spanId: string): void;
  /** Keep the span's note that was closing: the pointer came onto it. */
  keep(spanId: string): void;
}

export function createNotes(delayMs = NOTE_DELAY_MS): Notes {
  const store = createStore<NoteState>({ shown: null, last: null });
  // One at a time, so whatever shows next stops it.
  let waiting: { spanId: string; to: "open" | "close"; timer: ReturnType<typeof setTimeout> } | null = null;
  let closedAt = Number.NEGATIVE_INFINITY;
  const stopWaiting = () => {
    if (waiting !== null) clearTimeout(waiting.timer);
    waiting = null;
  };
  const open = (shown: Shown) => store.set({ shown, last: shown });
  const hide = (spanId: string) => {
    if (waiting?.spanId === spanId) stopWaiting();
    if (store.get().shown?.spanId !== spanId) return;
    closedAt = Date.now();
    store.set({ shown: null });
  };
  return {
    store,
    show(spanId, anchor, via) {
      stopWaiting();
      // Focus comes with a click too: it never takes over a note a hover or a tap showed.
      if (via === "focus" && store.get().shown?.spanId === spanId) return;
      const warm = store.get().shown !== null || Date.now() - closedAt < delayMs;
      if (via !== "hover" || warm) {
        open({ spanId, anchor, via });
        return;
      }
      waiting = { spanId, to: "open", timer: setTimeout(() => open({ spanId, anchor, via }), delayMs) };
    },
    hide,
    hideSoon(spanId) {
      // Not shown yet: it never opens.
      if (store.get().shown?.spanId !== spanId) {
        hide(spanId);
        return;
      }
      stopWaiting();
      waiting = { spanId, to: "close", timer: setTimeout(() => hide(spanId), NOTE_CLOSE_DELAY_MS) };
    },
    keep(spanId) {
      if (waiting?.spanId === spanId && waiting.to === "close") stopWaiting();
    },
  };
}
