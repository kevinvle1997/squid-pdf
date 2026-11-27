import type {
  AccountState,
  Document,
  Edit,
  FileNoticeInfo,
  FontList,
  LinkSent,
  PaymentPage,
  Problem,
  ProblemInfo,
  Render,
  RenderBody,
  Terms,
} from "./types";

export class ProblemError extends Error {
  readonly problem: Problem;

  constructor(problem: Problem) {
    super(problem.detail);
    this.name = "ProblemError";
    this.problem = problem;
  }
}

export function isGone(error: unknown): boolean {
  return error instanceof ProblemError && error.problem.status === 404;
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
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ProblemError(UNREACHABLE);
  }
  if (!response.ok) throw new ProblemError(await problemOf(response));
  return response;
}

/** XMLHttpRequest, not fetch: only it reports bytes sent. */
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

export async function documentOf(docId: string): Promise<Document> {
  const response = await send(`/api/documents/${docId}`, { method: "GET" });
  return (await response.json()) as Document;
}

export async function stillThere(docId: string): Promise<boolean> {
  try {
    await send(`/api/documents/${docId}`, { method: "GET" });
    return true;
  } catch (error) {
    return !isGone(error);
  }
}

export async function putFont(docId: string, fontName: string, file: Blob): Promise<Document> {
  const response = await send(`/api/documents/${docId}/fonts/${encodeURIComponent(fontName)}`, {
    method: "PUT",
    body: file,
  });
  return (await response.json()) as Document;
}

export async function deleteFont(docId: string, fontName: string): Promise<Document> {
  const response = await send(`/api/documents/${docId}/fonts/${encodeURIComponent(fontName)}`, {
    method: "DELETE",
  });
  return (await response.json()) as Document;
}

export async function fontList(build: string): Promise<FontList> {
  const response = await send(`/api/fonts?${new URLSearchParams({ build })}`, { method: "GET" });
  return (await response.json()) as FontList;
}

export async function terms(): Promise<Terms | null> {
  const response = await send("/api/terms", { method: "GET" });
  return (await response.json()) as Terms | null;
}

export async function account(): Promise<AccountState> {
  const response = await send("/api/account", { method: "GET" });
  return (await response.json()) as AccountState;
}

export async function askForLink(email: string): Promise<LinkSent> {
  const response = await send("/api/account/link", json({ email }));
  return (await response.json()) as LinkSent;
}

export async function signIn(token: string): Promise<AccountState> {
  const response = await send("/api/account/sign-in", json({ token }));
  return (await response.json()) as AccountState;
}

export async function signOut(): Promise<AccountState> {
  const response = await send("/api/account/sign-out", { method: "POST" });
  return (await response.json()) as AccountState;
}

export async function checkout(): Promise<PaymentPage> {
  const response = await send("/api/account/checkout", { method: "POST" });
  return (await response.json()) as PaymentPage;
}

export async function billingPortal(): Promise<PaymentPage> {
  const response = await send("/api/account/billing-portal", { method: "POST" });
  return (await response.json()) as PaymentPage;
}

function json(body: object): RequestInit {
  return { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) };
}

/** Unrotated, `scale` pixels per point. */
export function pageUrl(doc: Document, page: number, scale: number): string {
  const query = new URLSearchParams({ scale: String(scale), build: doc.build });
  return `/api/documents/${doc.id}/pages/${page}?${query}`;
}

/** Asked again only to learn why it failed. */
export async function pageImage(doc: Document, page: number, scale: number): Promise<void> {
  await send(pageUrl(doc, page, scale), { method: "GET" });
}

export async function renderStrips(docId: string, body: RenderBody, signal: AbortSignal): Promise<Render> {
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
  skipped: number[];
  notices: FileNoticeInfo[];
}

export async function exportPdf(
  docId: string,
  edits: Edit[],
  { pages, signal }: { pages?: readonly number[]; signal?: AbortSignal } = {},
): Promise<Exported> {
  const response = await send(`/api/documents/${docId}/export`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ edits, pages }),
    signal,
  });
  const skipped = (response.headers.get("squid-skipped-edits") ?? "")
    .split(",")
    .filter((position) => position.trim() !== "")
    .map(Number);
  const notices = JSON.parse(response.headers.get("squid-notices") ?? "[]") as FileNoticeInfo[];
  return { pdf: await response.blob(), skipped, notices };
}
