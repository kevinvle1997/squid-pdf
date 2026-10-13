import { lazy, Suspense, useState } from "react";
import styles from "./App.module.css";
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

/** The landing, or the document open in the editor. */
type Screen = Landing | { kind: "open"; opened: Opened };

function Screens() {
  const [screen, setScreen] = useState<Screen>({ kind: "waiting" });

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

  if (screen.kind !== "open") return <Drop landing={screen} onChoose={(file) => void open(file)} />;
  const { opened } = screen;
  return (
    <Suspense fallback={null}>
      {/* Keyed: the editor takes `opened` only as it starts, so another document is another editor. */}
      <EditorShell key={opened.doc.id} file={opened.file} opened={opened.doc} onOpen={(file) => void open(file)} />
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
        <p className={styles.fine}>Reload to start again. Changes since your last export couldn't be kept.</p>
        <Button onPress={() => window.location.reload()}>Reload</Button>
      </main>
    </div>
  );
}
