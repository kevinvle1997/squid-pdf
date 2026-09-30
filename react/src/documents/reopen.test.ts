import { beforeEach, describe, expect, test, vi } from "vitest";
import { ProblemError, stillThere, upload } from "../api/client";
import type { Document } from "../api/types";
import { aDoc, aProblem } from "../fixtures";
import { Reopener } from "./reopen";

vi.mock(import("../api/client"), async (original) => ({ ...(await original()), upload: vi.fn(), stillThere: vi.fn() }));

const docOf = (id: string) => aDoc({ id });
const problem = (status: number, detail = "said"): ProblemError => new ProblemError(aProblem(status, detail));
const FILE = new Blob(["%PDF-"]);

let reopened: Document[];
let reopener: Reopener;

beforeEach(() => {
  vi.mocked(upload).mockReset().mockResolvedValue(docOf("again"));
  vi.mocked(stillThere).mockReset();
  reopened = [];
  reopener = new Reopener(FILE, docOf("first"), (doc) => reopened.push(doc));
});

describe("the document on the server", () => {
  test("while the server has it, nothing opens again", async () => {
    const used = await reopener.withDocument(async (doc) => doc.id);
    expect(used).toBe("first");
    expect(upload).not.toHaveBeenCalled();
  });

  test("gone, it opens again from this browser's file, and the work runs on the new copy", async () => {
    const tried: string[] = [];
    const used = await reopener.withDocument(async (doc) => {
      tried.push(doc.id);
      if (doc.id === "first") throw problem(404);
      return doc.id;
    });
    expect(tried).toEqual(["first", "again"]);
    expect(used).toBe("again");
    expect(upload).toHaveBeenCalledWith(FILE, expect.any(Function));
    expect(reopened.map((doc) => doc.id)).toEqual(["again"]);
    expect(reopener.doc.id).toBe("again");
  });

  test("however many find it gone at once, it opens again once", async () => {
    const gone = async (doc: Document) => {
      if (doc.id === "first") throw problem(404);
      return doc.id;
    };
    const both = await Promise.all([reopener.withDocument(gone), reopener.withDocument(gone)]);
    expect(both).toEqual(["again", "again"]);
    expect(upload).toHaveBeenCalledTimes(1);
  });

  test("any failure but gone is the work's own, and isn't tried again", async () => {
    const work = vi.fn(async () => {
      throw problem(422);
    });
    await expect(reopener.withDocument(work)).rejects.toMatchObject({ problem: { status: 422 } });
    expect(work).toHaveBeenCalledTimes(1);
    expect(upload).not.toHaveBeenCalled();
  });

  test("when opening again fails, that failure is what's said", async () => {
    vi.mocked(upload).mockRejectedValue(problem(413, "too large now"));
    const work = async () => {
      throw problem(404);
    };
    await expect(reopener.withDocument(work)).rejects.toMatchObject({ problem: { detail: "too large now" } });
    expect(reopener.doc.id).toBe("first");
  });

  test("a page image that fails opens it again only if the server has really lost it", async () => {
    vi.mocked(stillThere).mockResolvedValue(true);
    await reopener.check();
    expect(upload).not.toHaveBeenCalled();

    vi.mocked(stillThere).mockResolvedValue(false);
    await reopener.check();
    expect(reopener.doc.id).toBe("again");
  });
});
