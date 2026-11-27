import { ToggleButton, ToggleButtonGroup } from "react-aria-components";
import styles from "./Choices.module.css";

interface Props<T extends string> {
  label: string;
  options: readonly { id: T; label: string }[];
  chosen: T;
  onChoose: (id: T) => void;
  keepFocus?: boolean; // so a field being typed in keeps it
}

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
