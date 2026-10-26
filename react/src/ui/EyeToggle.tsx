import { ToggleButton } from "react-aria-components";
import styles from "./EyeToggle.module.css";

/** A toggle drawn as an eye: open while what `label` names is shown, struck through while it isn't. */
export function EyeToggle({
  label,
  isSelected,
  onChange,
}: {
  label: string; // what it shows, for a screen reader: the eye alone says nothing
  isSelected: boolean;
  onChange: (selected: boolean) => void;
}) {
  return (
    <ToggleButton aria-label={label} isSelected={isSelected} onChange={onChange} className={styles.eye}>
      <svg viewBox="0 0 16 16" aria-hidden="true" className={styles.icon}>
        <path
          d="M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8Z"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinejoin="round"
        />
        <circle cx="8" cy="8" r="2" fill="none" stroke="currentColor" strokeWidth="1.5" />
        {!isSelected && <path d="M2.5 13.5 13.5 2.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />}
      </svg>
    </ToggleButton>
  );
}
