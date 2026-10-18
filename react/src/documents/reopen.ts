// Once the server no longer has the document (its hour ran out, or a deploy retired it), it
// answers 404, and this browser's file opens it again under the same span ids.
import { isGone, stillThere, upload } from "../api/client";
import type { Document } from "../api/types";

export class Reopener {
  #doc: Document;
  #reopening: Promise<Document> | null = null;
  readonly #file: Blob;
  readonly #reopened: (doc: Document) => Promise<Document>;

  /** `reopened` readies each new copy, as the old one had it; what it gives back replaces the old. */
  constructor(file: Blob, opened: Document, reopened: (doc: Document) => Promise<Document>) {
    this.#file = file;
    this.#doc = opened;
    this.#reopened = reopened;
  }

  /** The server's copy now: the one opened, or the last opened since. */
  get doc(): Document {
    return this.#doc;
  }

  /**
   * `use` on the server's copy. If the server has lost it, it opens again, once however many
   * ask at the same moment, and `use` runs on the new copy. When opening again fails, that
   * failure is what's thrown; any other failure is `use`'s own.
   */
  async withDocument<T>(use: (doc: Document) => Promise<T>): Promise<T> {
    const tried = this.#doc;
    try {
      return await use(tried);
    } catch (error) {
      if (!isGone(error)) throw error;
      // Another caller may have opened it again already: then only this one tries again.
      const again = tried === this.#doc ? await this.#reopen() : this.#doc;
      return use(again);
    }
  }

  /** A page image failed, which it can for any reason: only a document really gone opens again. */
  async check(): Promise<void> {
    const tried = this.#doc;
    if (await stillThere(tried.id)) return;
    // Opened again while we asked: the new copy is there.
    if (tried === this.#doc) await this.#reopen();
  }

  #reopen(): Promise<Document> {
    // Nothing runs on the new copy until it's ready: work on a half-ready one would draw differently.
    this.#reopening ??= upload(this.#file, () => undefined)
      .then(this.#reopened)
      .then((ready) => {
        this.#doc = ready;
        return ready;
      })
      .finally(() => {
        this.#reopening = null;
      });
    return this.#reopening;
  }
}
