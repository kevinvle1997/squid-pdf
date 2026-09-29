import { Suspense, lazy, useState } from "react";
import { Drop, type Opened } from "./documents/Drop";

// The editor's code comes in while the file uploads, so the landing loads only what it shows.
const loadEditor = () => import("./editing/Editor");
const Editor = lazy(() => loadEditor().then((module) => ({ default: module.Editor })));

/** The landing is a drop target and nothing else; once a file is open, the editor. */
export function App() {
  const [opened, setOpened] = useState<Opened | null>(null);
  if (opened === null) return <Drop onOpened={setOpened} onOpening={() => void loadEditor()} />;
  return (
    <Suspense fallback={null}>
      <Editor file={opened.file} opened={opened.doc} />
    </Suspense>
  );
}
