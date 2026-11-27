// Said in the browser's own words, never left to vanish as an unhandled rejection.
import { ProblemError } from "./api/client";

export const SOMETHING_WENT_WRONG = "Something went wrong on this page. Try again, and reload if it keeps happening.";

function reportBug(error: unknown): string {
  console.error(error);
  return SOMETHING_WENT_WRONG;
}

export function sentenceOf(error: unknown): string {
  return error instanceof ProblemError ? error.problem.detail : reportBug(error);
}
