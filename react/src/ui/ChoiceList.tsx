import type { CSSProperties, Ref } from "react";
import { Header, ListBox, ListBoxItem, ListBoxSection } from "react-aria-components";
import styles from "./ChoiceList.module.css";

/** One choice in the list, and how its name is set. */
interface Choice {
  readonly id: string;
  readonly label: string;
  readonly style?: CSSProperties;
}

/** A group of choices, under a heading shown, or named for a screen reader alone. */
interface ChoiceGroup {
  readonly name: string;
  readonly shown: boolean; // its name is shown over it, not only heard
  readonly choices: readonly Choice[];
}

interface Props {
  label: string; // what the choices are of, for a screen reader
  groups: readonly ChoiceGroup[];
  chosen: string;
  onChoose: (id: string) => void;
  onEnter: () => void; // Enter on a choice: done choosing
  onPointerChoice?: () => void; // a pointer chose: focus can go back to what it came from
  listRef?: Ref<HTMLDivElement>; // the list, for focus sent to it
}

/**
 * One choice of many, listed open, the one chosen ticked in ink and softly filled: never blue, which is for what
 * changed. The arrow keys choose as they go, as React Aria's list does; a pointer's press chooses at once.
 */
export function ChoiceList({ label, groups, chosen, onChoose, onEnter, onPointerChoice, listRef }: Props) {
  return (
    <ListBox
      ref={listRef}
      aria-label={label}
      className={styles.list}
      selectionMode="single"
      selectionBehavior="replace"
      disallowEmptySelection
      escapeKeyBehavior="none"
      // Chosen as the press lifts, not as it lands: what's chosen can change what sits round the list.
      shouldSelectOnPressUp
      selectedKeys={[chosen]}
      onSelectionChange={(keys) => {
        const [id] = [...(keys === "all" ? [] : keys)];
        if (typeof id === "string" && id !== chosen) onChoose(id);
      }}
      onAction={onEnter}
    >
      {groups.map((group) => (
        <ListBoxSection key={group.name} className={styles.group} aria-label={group.shown ? undefined : group.name}>
          {group.shown && <Header className={styles.heading}>{group.name}</Header>}
          {group.choices.map((choice) => (
            <ListBoxItem
              key={choice.id}
              id={choice.id}
              textValue={choice.label}
              className={styles.choice}
              onPress={({ pointerType }) => {
                if (pointerType !== "keyboard" && pointerType !== "virtual") onPointerChoice?.();
              }}
            >
              {({ isSelected }) => (
                <>
                  {isSelected ? <Tick /> : <i className={styles.tickSpace} aria-hidden="true" />}
                  <span style={choice.style}>{choice.label}</span>
                </>
              )}
            </ListBoxItem>
          ))}
        </ListBoxSection>
      ))}
    </ListBox>
  );
}

/** The chosen one's tick, drawn at its own size so the stroke stays 1.5px. */
function Tick() {
  return (
    <svg className={styles.tick} width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
      <path d="M2.5 6.4 5 8.8 9.5 3.4" />
    </svg>
  );
}
