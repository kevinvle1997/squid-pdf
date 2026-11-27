import { NOTE_CLOSE_DELAY_MS, NOTE_DELAY_MS } from "../constants";
import { createStore, type Store } from "../store";

export type Via = "hover" | "focus" | "tap";

export interface Shown {
  readonly markId: string;
  readonly anchor: Element;
  readonly via: Via;
}

export interface NoteState {
  readonly shown: Shown | null;
  readonly last: Shown | null;
}

export interface Notes {
  readonly store: Store<NoteState>;
  show(markId: string, anchor: Element, via: Via): void;
  hide(markId: string): void;
  hideSoon(markId: string): void;
  keep(markId: string): void;
}

export function createNotes(delayMs = NOTE_DELAY_MS): Notes {
  const store = createStore<NoteState>({ shown: null, last: null });
  let waiting: { markId: string; to: "open" | "close"; timer: ReturnType<typeof setTimeout> } | null = null;
  let closedAt = Number.NEGATIVE_INFINITY;
  let held: Shown | null = null;
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
      if (store.get().shown?.markId !== markId) {
        hide(markId);
        return;
      }
      stopWaiting();
      const close = () => {
        hide(markId);
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

export function isShowing(shown: Shown | null, focusVisible: boolean): boolean {
  return shown !== null && (shown.via !== "focus" || focusVisible);
}
