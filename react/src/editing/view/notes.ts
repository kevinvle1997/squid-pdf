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
  /** Close the mark's note, or stop it opening, and let go of focus's hold on it; another mark's is left alone. */
  hide(markId: string): void;
  /**
   * The pointer left the mark or its note: close it after a moment, or stop it opening, since the pointer may be
   * crossing between the two. One focus holds stays, and shows again as another mark's closes, its mark still drawn.
   */
  hideSoon(markId: string): void;
  /** Keep the mark's note that was closing: the pointer came onto it. */
  keep(markId: string): void;
}

export function createNotes(delayMs = NOTE_DELAY_MS): Notes {
  const store = createStore<NoteState>({ shown: null, last: null });
  // One at a time, so whatever shows next stops it.
  let waiting: { markId: string; to: "open" | "close"; timer: ReturnType<typeof setTimeout> } | null = null;
  let closedAt = Number.NEGATIVE_INFINITY;
  let held: Shown | null = null; // the note focus showed, until focus leaves it or it's closed
  const stopWaiting = () => {
    if (waiting !== null) clearTimeout(waiting.timer);
    waiting = null;
  };
  const open = (shown: Shown) => store.set({ shown, last: shown });
  const hide = (markId: string) => {
    if (held?.markId === markId) held = null;
    if (waiting?.markId === markId) stopWaiting();
    if (store.get().shown?.markId !== markId) return;
    closedAt = Date.now();
    store.set({ shown: null });
  };
  return {
    store,
    show(markId, anchor, via) {
      stopWaiting();
      const shown = { markId, anchor, via };
      if (via === "focus") held = shown;
      // Focus comes with a click too: it never takes over a note a hover or a tap showed.
      if (via === "focus" && store.get().shown?.markId === markId) return;
      const warm = store.get().shown !== null || Date.now() - closedAt < delayMs;
      if (via !== "hover" || warm) {
        open(shown);
        return;
      }
      waiting = { markId, to: "open", timer: setTimeout(() => open(shown), delayMs) };
    },
    hide,
    hideSoon(markId) {
      if (held?.markId === markId) {
        stopWaiting();
        if (store.get().shown !== held) open(held);
        return;
      }
      // Not shown yet: it never opens.
      if (store.get().shown?.markId !== markId) {
        hide(markId);
        return;
      }
      stopWaiting();
      const close = () => {
        hide(markId);
        // Focus still holds another mark's note, which this hover's covered: it shows again, unless its mark is gone.
        if (held?.anchor.isConnected) open(held);
        else held = null;
      };
      waiting = { markId, to: "close", timer: setTimeout(close, NOTE_CLOSE_DELAY_MS) };
    },
    keep(markId) {
      if (waiting?.markId === markId && waiting.to === "close") stopWaiting();
    },
  };
}

/** Whether a note shows: one focus showed only while focus is the keyboard's, as a click's leaves it to the click. */
export function isShowing(shown: Shown | null, focusVisible: boolean): boolean {
  return shown !== null && (shown.via !== "focus" || focusVisible);
}
