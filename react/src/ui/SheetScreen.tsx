import type { ReactNode } from "react";
import { classes } from "./classes";
import { Sheet } from "./Sheet";
import styles from "./SheetScreen.module.css";
import { Wordmark } from "./Wordmark";

interface Props {
  title: ReactNode; // the screen's one heading, in the display size
  children: ReactNode; // what the sheet holds under its heading
  foot?: ReactNode; // a line at the window's foot, under the sheet
  sheetClassName?: string;
}

/** A screen with no document on it: the wordmark at the top, and one sheet in the middle of the ground. */
export function SheetScreen({ title, children, foot, sheetClassName }: Props) {
  return (
    <div className={styles.screen}>
      <header className={styles.top}>
        <Wordmark />
      </header>
      <main className={classes(styles.middle, foot !== undefined && styles.footed)}>
        <Sheet className={sheetClassName}>
          <h1 className={styles.title}>{title}</h1>
          {children}
        </Sheet>
        {foot}
      </main>
    </div>
  );
}
