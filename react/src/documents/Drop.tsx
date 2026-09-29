import { useEffect, useRef, useState } from "react";
import { Button, DropZone, FileTrigger, isFileDropItem } from "react-aria-components";
import { ProblemError, upload } from "../api/client";
import type { Document } from "../api/types";
import { Wordmark } from "../Wordmark";
import { QUIET_MS } from "./constants";
import styles from "./Drop.module.css";

export interface Opened {
  file: File;
  doc: Document;
}

type State =
  | { kind: "waiting" }
  | { kind: "opening"; name: string; sent: number; total: number; shown: boolean }
  | { kind: "failed"; detail: string };

const PERCENT = new Intl.NumberFormat("en", { style: "percent" });

/** The landing: the whole window takes a dropped PDF. No tool grid, no sign-in. */
export function Drop({ onOpened, onOpening }: { onOpened: (opened: Opened) => void; onOpening: () => void }) {
  const [state, setState] = useState<State>({ kind: "waiting" });
  const quiet = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(quiet.current), []);

  async function open(file: File) {
    onOpening();
    setState({ kind: "opening", name: file.name, sent: 0, total: file.size, shown: false });
    quiet.current = window.setTimeout(
      () => setState((now) => (now.kind === "opening" ? { ...now, shown: true } : now)),
      QUIET_MS,
    );
    try {
      const doc = await upload(file, (sent, total) =>
        setState((now) => (now.kind === "opening" ? { ...now, sent, total } : now)),
      );
      onOpened({ file, doc });
    } catch (error) {
      // Only the upload's own failures are expected here; anything else is a bug to see.
      if (!(error instanceof ProblemError)) throw error;
      setState({ kind: "failed", detail: error.problem.detail });
    } finally {
      window.clearTimeout(quiet.current);
    }
  }

  return (
    <DropZone
      className={styles.zone}
      aria-label="Drop a PDF to open it"
      onDrop={async (event) => {
        const item = event.items.find(isFileDropItem);
        if (item !== undefined) await open(await item.getFile());
      }}
    >
      <header className={styles.top}>
        <Wordmark />
      </header>
      <main className={styles.middle}>
        <h1 className={styles.title}>Drop a PDF anywhere to fix its words</h1>
        <FileTrigger acceptedFileTypes={["application/pdf"]} onSelect={(files) => files?.[0] && open(files[0])}>
          <Button className={styles.choose} isDisabled={state.kind === "opening"}>
            Choose a PDF
          </Button>
        </FileTrigger>
        <p className={styles.status} role="status">
          {state.kind === "opening" && state.shown && <Progress {...state} />}
          {state.kind === "failed" && <span className={styles.failed}>{state.detail}</span>}
        </p>
        <p className={styles.fine}>Your file is deleted an hour after you last touch it.</p>
      </main>
    </DropZone>
  );
}

function Progress({ name, sent, total }: { name: string; sent: number; total: number }) {
  // Sent, the server is reading the text: a real step, not invented progress.
  if (total > 0 && sent >= total) return <>Reading the text of {name}</>;
  return (
    <>
      Sending {name} <span className={styles.num}>{PERCENT.format(total > 0 ? sent / total : 0)}</span>
    </>
  );
}
