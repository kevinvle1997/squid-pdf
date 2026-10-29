import { lazy, Suspense, useEffect, useState } from "react";
import styles from "./App.module.css";
import { forgetOrSayWhy, type Persisted, type PersistedWork, persistedDocument } from "./documents/localPersistence";
import { type Landing, type Opened, openFile } from "./documents/open";
import { Drop } from "./documents/view/Drop";
import { isTermsPath, type TermsAnswer, termsAnswer, termsHrefOf } from "./terms/terms";
import { TermsScreen } from "./terms/view/TermsScreen";
import { Button } from "./ui/Button";
import { ErrorBoundary } from "./ui/ErrorBoundary";
import { SheetScreen } from "./ui/SheetScreen";

// The editor's code comes in while the file uploads, so the landing loads only what it shows.
const loadEditor = () => import("./editing/view/EditorShell");
const EditorShell = lazy(() => loadEditor().then((module) => ({ default: module.EditorShell })));

/** The landing, which takes a dropped PDF; once a file is open, the editor; and the service's terms, at their own path. */
export function App() {
  return (
    <ErrorBoundary fallback={<Crashed />}>
      <Screens />
    </ErrorBoundary>
  );
}

/** The landing, or the document open in the editor: opened again from this browser, with its work persisted. */
type Screen = Landing | { kind: "open"; opened: Opened; persisted?: PersistedWork<unknown> };

/** Deleting what this browser persisted: under way, or why it's still there. */
type Forgetting = { kind: "under way" } | { kind: "failed"; detail: string };

function Screens() {
  const [screen, setScreen] = useState<Screen>({ kind: "waiting" });
  const [persisted, setPersisted] = useState<Persisted<unknown> | null>(null);
  const [forgetting, setForgetting] = useState<Forgetting | null>(null);
  const [terms, setTerms] = useState<TermsAnswer>({ kind: "asking" });
  const [fontLoaded, setFontLoaded] = useState(false);

  // Read as the landing first shows: it's outside React, in the browser's storage, and on the server.
  useEffect(() => {
    // Refused (a private window, say): nothing persisted, so nothing to offer.
    persistedDocument().then(setPersisted, () => undefined);
    termsAnswer().then(setTerms);
    // The terms' link waits for the interface's font: in a stand-in its line can wrap, and jump back as the
    // font comes. A font that fails to load leaves the stand-in for good, so the link comes anyway.
    const loaded = () => setFontLoaded(true);
    document.fonts.load(`1em ${getComputedStyle(document.body).fontFamily}`).then(loaded, loaded);
  }, []);

  function reopen() {
    // A delete under way would take the work from under the editor.
    if (persisted === null || forgetting?.kind === "under way") return;
    void loadEditor();
    setScreen({
      kind: "open",
      opened: { file: persisted.file, doc: persisted.doc },
      persisted: { work: persisted.work, until: persisted.until },
    });
    setPersisted(null);
  }

  async function forget() {
    if (persisted === null) return;
    setForgetting({ kind: "under way" });
    const detail = await forgetOrSayWhy(persisted.file.name);
    // Still persisted, so still offered, and why.
    if (detail !== null) {
      setForgetting({ kind: "failed", detail });
      return;
    }
    setPersisted(null);
    setForgetting(null);
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
    setScreen(opening);
  }

  // A deployment with no terms has no such page: the path shows the landing, as any other does.
  if (isTermsPath(window.location.pathname) && terms.kind !== "none") return <TermsScreen answer={terms} />;
  if (screen.kind !== "open") {
    return (
      <Drop
        landing={screen}
        termsHref={fontLoaded ? termsHrefOf(terms) : null}
        persisted={persisted}
        forgetFailed={forgetting?.kind === "failed" ? forgetting.detail : null}
        onChoose={(file) => void open(file)}
        onReopen={reopen}
        onForget={() => void forget()}
      />
    );
  }
  const { opened } = screen;
  return (
    <Suspense fallback={null}>
      {/* Keyed: the editor takes `opened` only as it starts, so another document is another editor. */}
      <EditorShell
        key={opened.doc.id}
        opened={opened}
        onOpen={(file) => void open(file)}
        persisted={screen.persisted}
      />
    </Suspense>
  );
}

/** Drawing failed: say so, and the way back, on the landing's sheet rather than a blank page. */
function Crashed() {
  return (
    <SheetScreen title="Something went wrong, and this page stopped">
      <p className={styles.fine}>
        Reload to start again. Changes kept in this browser are offered there to open again.
      </p>
      <Button variant="primary" className={styles.reload} onPress={() => window.location.reload()}>
        Reload
      </Button>
    </SheetScreen>
  );
}
