// A bug is what no code path expected, as opposed to a Problem the server answered with. It's
// logged for whoever debugs it and said to the user in plain words, never left to vanish as an
// unhandled rejection. The browser writes this sentence: the server never saw what went wrong.

export const SOMETHING_WENT_WRONG = "Something went wrong on this page. Try again, and reload if it keeps happening.";

/** Log `error` and give the words to tell the user. */
export function reportBug(error: unknown): string {
  console.error(error);
  return SOMETHING_WENT_WRONG;
}
