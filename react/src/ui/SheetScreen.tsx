import type { ReactNode } from "react";
import { Mascot } from "./Mascot";
import { ScreenTop } from "./ScreenTop";
import { Sheet } from "./Sheet";
import styles from "./SheetScreen.module.css";

interface Props {
  title: ReactNode;
  children: ReactNode;
  foot?: ReactNode;
  sheetClassName?: string;
  home?: boolean;
}

export function SheetScreen({ title, children, foot, sheetClassName, home = false }: Props) {
  return (
    <div className={styles.screen}>
      <ScreenTop home={home} />
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
