// Which mark's note a page shows, a span's or new text's: one at a time, through the one controller they share.
// A hover waits NOTE_DELAY_MS; focus, a tap, or a hover just after another note closed show it at once.
import { NOTE_CLOSE_DELAY_MS, NOTE_DELAY_MS } from "../constants";
import { createStore, type Store } from "../store";

export type Via = "hover" | "focus" | "tap";

export interface Shown {
  readonly markId: string; // a span's id, or new text's own (`NewTextMark`)
  readonly anchor: Element; // the mark it's shown over
  readonly via: Via;
}

export interface NoteState {
  readonly shown: Shown | null;
  readonly last: Shown | null; // the one shown last, kept while it closes
}

export interface Notes {
  readonly store: Store<NoteState>;
  show(markId: string, anchor: Element, via: Via): void;
  /** Close the mark's note, or stop it opening; another mark's is left alone. */
  hide(markId: string): void;
  /** Close the mark's note after a moment, or stop it opening: the pointer may be crossing between the mark and the note. */
  hideSoon(markId: string): void;
  /** Keep the mark's note that was closing: the pointer came onto it. */
  keep(markId: string): void;
}

export function createNotes(delayMs = NOTE_DELAY_MS): Notes {
  const store = createStore<NoteState>({ shown: null, last: null });
  // One at a time, so whatever shows next stops it.
  let waiting: { markId: string; to: "open" | "close"; timer: ReturnType<typeof setTimeout> } | null = null;
  let closedAt = Number.NEGATIVE_INFINITY;
  const stopWaiting = () => {
    if (waiting !== null) clearTimeout(waiting.timer);
    waiting = null;
  };
  const open = (shown: Shown) => store.set({ shown, last: shown });
  const hide = (markId: string) => {
    if (waiting?.markId === markId) stopWaiting();
    if (store.get().shown?.markId !== markId) return;
    closedAt = Date.now();
    store.set({ shown: null });
  };
  return {
    store,
    show(markId, anchor, via) {
      stopWaiting();
      // Focus comes with a click too: it never takes over a note a hover or a tap showed.
      if (via === "focus" && store.get().shown?.markId === markId) return;
      const warm = store.get().shown !== null || Date.now() - closedAt < delayMs;
      if (via !== "hover" || warm) {
        open({ markId, anchor, via });
        return;
      }
      waiting = { markId, to: "open", timer: setTimeout(() => open({ markId, anchor, via }), delayMs) };
    },
    hide,
    hideSoon(markId) {
      // Not shown yet: it never opens.
      if (store.get().shown?.markId !== markId) {
        hide(markId);
        return;
      }
      stopWaiting();
      waiting = { markId, to: "close", timer: setTimeout(() => hide(markId), NOTE_CLOSE_DELAY_MS) };
    },
    keep(markId) {
      if (waiting?.markId === markId && waiting.to === "close") stopWaiting();
    },
  };
}
