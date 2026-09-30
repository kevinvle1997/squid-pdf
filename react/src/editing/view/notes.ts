// Which span's note a page shows: one at a time, shared by the page's marks. A TooltipTrigger
// on every mark made each page that scrolled near mount dozens of them. Hover waits
// NOTE_DELAY_MS, as the trigger's delay did; focus and a tap show it at once, and so does a
// hover just after another note closed, since the reader is going from note to note. A note
// focus showed is drawn only for keyboard focus, as the trigger did: the page decides that.
import { NOTE_DELAY_MS } from "../constants";
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
}

export function createNotes(delayMs = NOTE_DELAY_MS): Notes {
  const store = createStore<NoteState>({ shown: null, last: null });
  let waiting: { spanId: string; timer: ReturnType<typeof setTimeout> } | null = null;
  let closedAt = Number.NEGATIVE_INFINITY;
  const stopWaiting = () => {
    if (waiting !== null) clearTimeout(waiting.timer);
    waiting = null;
  };
  const open = (shown: Shown) => store.set({ shown, last: shown });
  return {
    store,
    show(spanId, anchor, via) {
      // Focus comes with a click too: it never takes over a note a hover or a tap showed.
      if (via === "focus" && store.get().shown?.spanId === spanId) return;
      stopWaiting();
      const warm = store.get().shown !== null || Date.now() - closedAt < delayMs;
      if (via !== "hover" || warm) {
        open({ spanId, anchor, via });
        return;
      }
      waiting = { spanId, timer: setTimeout(() => open({ spanId, anchor, via }), delayMs) };
    },
    hide(spanId) {
      if (waiting?.spanId === spanId) stopWaiting();
      if (store.get().shown?.spanId !== spanId) return;
      closedAt = Date.now();
      store.set({ shown: null });
    },
  };
}
