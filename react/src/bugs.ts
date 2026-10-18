// A bug is what no code path expected, unlike a Problem the server answered with: logged for whoever
// debugs it, and said to the user in the browser's own words, never left to vanish as an unhandled rejection.
import { ProblemError } from "./api/client";

export const SOMETHING_WENT_WRONG = "Something went wrong on this page. Try again, and reload if it keeps happening.";

/** Log `error` and give the words to tell the user. */
function reportBug(error: unknown): string {
  console.error(error);
  return SOMETHING_WENT_WRONG;
}

/** What to tell the user of a failure: a Problem's own sentence, else a bug's, logged. */
export function sentenceOf(error: unknown): string {
  return error instanceof ProblemError ? error.problem.detail : reportBug(error);
}
