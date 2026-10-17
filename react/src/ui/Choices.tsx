import { ToggleButton, ToggleButtonGroup } from "react-aria-components";
import styles from "./Choices.module.css";

interface Props<T extends string> {
  label: string; // what the choices are of, for a screen reader
  options: readonly { id: T; label: string }[];
  chosen: T;
  onChoose: (id: T) => void;
  keepFocus?: boolean; // a press leaves focus where it is, so a field being typed in keeps it
}

/** One of a few choices, side by side, the one chosen in ink: never blue, which is for what changed. */
export function Choices<T extends string>({ label, options, chosen, onChoose, keepFocus = false }: Props<T>) {
  return (
    <ToggleButtonGroup
      aria-label={label}
      selectionMode="single"
      disallowEmptySelection
      selectedKeys={[chosen]}
      onSelectionChange={(keys) => {
        const picked = options.find((option) => keys.has(option.id));
        if (picked !== undefined) onChoose(picked.id);
      }}
      className={styles.choices}
    >
      {options.map((option) => (
        <ToggleButton
          key={option.id}
          id={option.id}
          className={styles.choice}
          preventFocusOnPress={keepFocus}
          excludeFromTabOrder={keepFocus}
        >
          {option.label}
        </ToggleButton>
      ))}
    </ToggleButtonGroup>
  );
}
