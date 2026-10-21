import type { ReactNode } from "react";
import mascot from "./mascot.svg";
import { Sheet } from "./Sheet";
import styles from "./SheetScreen.module.css";
import { Wordmark } from "./Wordmark";

interface Props {
  title: ReactNode; // the screen's one heading, in the display size
  children: ReactNode; // what the sheet holds under its heading
  says?: string; // what the mascot says beside it, in its word bubble
  foot?: ReactNode; // what sits under the sheet
  sheetClassName?: string;
}

/** A screen with no document on it: the wordmark at the top, and the mascot over one sheet in the middle. */
export function SheetScreen({ title, children, says, foot, sheetClassName }: Props) {
  return (
    <div className={styles.screen}>
      <header className={styles.top}>
        <Wordmark />
      </header>
      <main className={styles.middle}>
        <div className={styles.stage}>
          <img className={styles.mascot} src={mascot} alt="" width={150} height={150} />
          {says !== undefined && <p className={styles.says}>{says}</p>}
          <Sheet className={sheetClassName}>
            <h1 className={styles.title}>{title}</h1>
            {children}
          </Sheet>
        </div>
        {foot}
      </main>
    </div>
  );
}
