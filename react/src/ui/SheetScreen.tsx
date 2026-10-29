import type { ReactNode } from "react";
import { Mascot } from "./Mascot";
import { ScreenTop } from "./ScreenTop";
import { Sheet } from "./Sheet";
import styles from "./SheetScreen.module.css";

interface Props {
  title: ReactNode; // the screen's one heading, in the display size
  children: ReactNode; // what the sheet holds under its heading
  foot?: ReactNode; // what sits under the sheet
  sheetClassName?: string;
}

/** A screen with no document on it: the wordmark at the top, and the mascot over one sheet in the middle. */
export function SheetScreen({ title, children, foot, sheetClassName }: Props) {
  return (
    <div className={styles.screen}>
      <ScreenTop />
      <main className={styles.middle}>
        <div className={styles.stage}>
          <Mascot className={styles.mascot} />
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
