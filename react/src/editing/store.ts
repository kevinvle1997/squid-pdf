// State that lives outside React. A handler reads it the moment it changes, with no ref kept
// beside a copy in state; what's drawn subscribes to the part it shows, so a change to one
// span redraws that span, not the document.

export interface Store<S> {
  get(): S;
  /** Merge `patch` in and tell every subscriber, at once. */
  set(patch: Partial<S>): void;
  /** Hear of every change; the function returned stops it. */
  subscribe(listener: () => void): () => void;
}

export function createStore<S extends object>(initial: S): Store<S> {
  let state = initial;
  const listeners = new Set<() => void>();
  return {
    get: () => state,
    set(patch) {
      state = { ...state, ...patch };
      for (const listener of listeners) listener();
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}
