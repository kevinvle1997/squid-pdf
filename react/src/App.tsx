import { lazy, Suspense, useState } from "react";
import styles from "./App.module.css";
import { Drop, type Opened } from "./documents/view/Drop";
import { Button } from "./ui/Button";
import { ErrorBoundary } from "./ui/ErrorBoundary";
import { Wordmark } from "./ui/Wordmark";

// The editor's code comes in while the file uploads, so the landing loads only what it shows.
const loadEditor = () => import("./editing/view/Editor");
const Editor = lazy(() => loadEditor().then((module) => ({ default: module.Editor })));

/** The landing is a drop target and nothing else; once a file is open, the editor. */
export function App() {
  return (
    <ErrorBoundary fallback={<Crashed />}>
      <Screens />
    </ErrorBoundary>
  );
}

function Screens() {
  const [opened, setOpened] = useState<Opened | null>(null);
  if (opened === null) return <Drop onOpened={setOpened} onOpening={() => void loadEditor()} />;
  return (
    <Suspense fallback={null}>
      {/* Keyed: the editor takes `opened` only as it starts, so another document is another editor. */}
      <Editor key={opened.doc.id} file={opened.file} opened={opened.doc} />
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
