// Every call the browser makes. Routes are in decisions/api.md; shapes come from schema.d.ts.
import type { Document, Edit, FileNoticeInfo, Problem, ProblemInfo, Render, RenderBody } from "./types";

/** What the server said went wrong. `detail` is shown to the user as it is. */
export class ProblemError extends Error {
  readonly problem: Problem;

  constructor(problem: Problem) {
    super(problem.detail);
    this.name = "ProblemError";
    this.problem = problem;
  }
}

// When nothing answers (no network, or the proxy failed), there's no Problem, so the browser says it.
const UNREACHABLE: Problem = {
  type: "unreachable",
  status: 0,
  detail: "Couldn't reach the server. Check your connection and try again.",
  code: "unreachable",
  params: {},
};

const PROBLEM_TYPES = ["application/problem+json", "application/json"];

async function problemOf(response: Response): Promise<Problem> {
  const type = response.headers.get("content-type") ?? "";
  if (PROBLEM_TYPES.some((json) => type.startsWith(json))) {
    return (await response.json()) as ProblemInfo;
  }
  return { ...UNREACHABLE, status: response.status };
}

async function send(url: string, init: RequestInit): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(url, init);
  } catch (error) {
    // fetch rejects only when no reply came: aborted by us, or the network is down.
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ProblemError(UNREACHABLE);
  }
  if (!response.ok) throw new ProblemError(await problemOf(response));
  return response;
}

/** Upload a PDF as the raw body. XMLHttpRequest, not fetch: only it reports bytes sent. */
export function upload(file: Blob, onProgress: (sent: number, total: number) => void): Promise<Document> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", "/api/documents");
    request.setRequestHeader("content-type", "application/pdf");
    request.responseType = "json";
    request.upload.onprogress = (event) => onProgress(event.loaded, event.total);
    request.onload = () => {
      if (request.status === 201) return resolve(request.response as Document);
      const problem = (request.response as ProblemInfo | null) ?? { ...UNREACHABLE, status: request.status };
      reject(new ProblemError(problem));
    };
    request.onerror = () => reject(new ProblemError(UNREACHABLE));
    request.send(file);
  });
}

/** Whether the server still has the document: false once its hour has run out. */
export async function stillThere(docId: string): Promise<boolean> {
  try {
    await send(`/api/documents/${docId}`, { method: "GET" });
    return true;
  } catch (error) {
    // Only a 404 says it's gone; any other failure says nothing either way.
    if (error instanceof ProblemError && error.problem.status === 404) return false;
    return true;
  }
}

/** A page of the original, unrotated, `scale` pixels per point. Cached for the hour. */
export function pageUrl(doc: Document, page: number, scale: number): string {
  const query = new URLSearchParams({ scale: String(scale), build: doc.build });
  return `/api/documents/${doc.id}/pages/${page}?${query}`;
}

/** The edits drawn on these strips of their pages, and what the server makes of each. */
export async function render(docId: string, body: RenderBody, signal: AbortSignal): Promise<Render> {
  const response = await send(`/api/documents/${docId}/render`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  return (await response.json()) as Render;
}

export interface Exported {
  pdf: Blob;
  skipped: number[]; // positions in the edit list the server left out
  notices: FileNoticeInfo[]; // what saving did to the whole file other than asked
}

/** The edited document as a PDF. The body is the file, so the rest comes in headers. */
export async function exportPdf(docId: string, edits: Edit[]): Promise<Exported> {
  const response = await send(`/api/documents/${docId}/export`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ edits }),
  });
  const skipped = (response.headers.get("squid-skipped-edits") ?? "")
    .split(",")
    .filter((position) => position.trim() !== "")
    .map(Number);
  const notices = JSON.parse(response.headers.get("squid-notices") ?? "[]") as FileNoticeInfo[];
  return { pdf: await response.blob(), skipped, notices };
}
