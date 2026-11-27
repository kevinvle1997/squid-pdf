// Once the server no longer has the document it answers 404, and this browser's file opens it again under the same span ids.
import { documentOf, isGone, stillThere, upload } from "../api/client";
import type { Document } from "../api/types";

export class Reopener {
  #doc: Document;
  #reopening: Promise<Document> | null = null;
  readonly #file: Blob;
  readonly #reopened: (doc: Document) => Promise<Document>;

  constructor(file: Blob, opened: Document, reopened: (doc: Document) => Promise<Document>) {
    this.#file = file;
    this.#doc = opened;
    this.#reopened = reopened;
  }

  get doc(): Document {
    return this.#doc;
  }

  /** Opens again once however many ask at the same moment; a failure to open again is what's thrown. */
  async withDocument<T>(use: (doc: Document) => Promise<T>): Promise<T> {
    const tried = this.#doc;
    try {
      return await use(tried);
    } catch (error) {
      if (!isGone(error)) throw error;
      const again = tried === this.#doc ? await this.#reopen() : this.#doc;
      return use(again);
    }
  }

  async readAgain(): Promise<Document> {
    const fresh = await this.withDocument((doc) => documentOf(doc.id));
    this.#doc = fresh;
    return fresh;
  }

  /** A page image can fail for any reason: only a document really gone opens again. */
  async check(): Promise<void> {
    const tried = this.#doc;
    if (await stillThere(tried.id)) return;
    if (tried === this.#doc) await this.#reopen();
  }

  #reopen(): Promise<Document> {
    // Work on a half-ready copy would draw differently.
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
