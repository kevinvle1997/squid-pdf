import { lazy, Suspense, useEffect, useState } from "react";
import styles from "./App.module.css";
import { forgetKept, type Kept, keptDocument } from "./documents/kept";
import { type Landing, type Opened, openFile } from "./documents/open";
import { Drop } from "./documents/view/Drop";
import { Button } from "./ui/Button";
import { ErrorBoundary } from "./ui/ErrorBoundary";
import { Wordmark } from "./ui/Wordmark";

// The editor's code comes in while the file uploads, so the landing loads only what it shows.
const loadEditor = () => import("./editing/view/EditorShell");
const EditorShell = lazy(() => loadEditor().then((module) => ({ default: module.EditorShell })));

/** The landing is a drop target and nothing else; once a file is open, the editor. */
export function App() {
  return (
    <ErrorBoundary fallback={<Crashed />}>
      <Screens />
    </ErrorBoundary>
  );
}

/** The landing, or the document open in the editor: opened again from this browser, with its work kept. */
type Screen = Landing | { kind: "open"; opened: Opened; kept?: { work: unknown; until: number } };

function Screens() {
  const [screen, setScreen] = useState<Screen>({ kind: "waiting" });
  const [kept, setKept] = useState<Kept<unknown> | null>(null);

  // What this browser kept, read as the landing first shows: it's outside React, in the browser's storage.
  useEffect(() => {
    // Refused (a private window, say): nothing kept, so nothing to offer.
    keptDocument().then(setKept, () => undefined);
  }, []);

  function reopen() {
    if (kept === null) return;
    void loadEditor();
    setScreen({
      kind: "open",
      opened: { file: kept.file, doc: kept.doc },
      kept: { work: kept.work, until: kept.until },
    });
    setKept(null);
  }

  function forget() {
    setKept(null);
    // Refused, there was nothing to delete.
    forgetKept().catch(() => undefined);
  }

  // From the landing, or a PDF dropped on the editor in place of the open one.
  async function open(file: File) {
    // One file at a time: a second drop mid-upload would race the first to the editor.
    if (screen.kind === "opening") return;
    void loadEditor();
    setScreen({ kind: "opening", name: file.name, sent: 0, total: file.size });
    const opening = await openFile(file, (sent, total) =>
      setScreen((now) => (now.kind === "opening" ? { ...now, sent, total } : now)),
    );
    setScreen(
      "opened" in opening ? { kind: "open", opened: opening.opened } : { kind: "failed", detail: opening.failed },
    );
  }

  if (screen.kind !== "open") {
    const offered =
      screen.kind === "waiting" && kept !== null ? { name: kept.file.name, until: kept.until } : undefined;
    return (
      <Drop
        landing={screen.kind === "waiting" ? { kind: "waiting", kept: offered } : screen}
        onChoose={(file) => void open(file)}
        onReopen={reopen}
        onForget={forget}
      />
    );
  }
  const { opened } = screen;
  return (
    <Suspense fallback={null}>
      {/* Keyed: the editor takes `opened` only as it starts, so another document is another editor. */}
      <EditorShell
        key={opened.doc.id}
        file={opened.file}
        opened={opened.doc}
        onOpen={(file) => void open(file)}
        kept={screen.kept}
      />
    </Suspense>
  );
}

/** Drawing failed: say so, and the way back, rather than leave a blank page. */
function Crashed() {
  return (
    <div className={styles.crashed}>
      <header className={styles.top}>
        <Wordmark />
      </header>
      <main className={styles.middle}>
        <h1 className={styles.title}>Something went wrong, and this page stopped</h1>
        <p className={styles.fine}>
          Reload to start again. Changes kept in this browser are offered there to open again.
        </p>
        <Button onPress={() => window.location.reload()}>Reload</Button>
      </main>
    </div>
  );
}
