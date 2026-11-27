import type { CSSProperties, Ref } from "react";
import { Header, ListBox, ListBoxItem, ListBoxSection } from "react-aria-components";
import styles from "./ChoiceList.module.css";

interface Choice {
  readonly id: string;
  readonly label: string;
  readonly style?: CSSProperties;
}

interface ChoiceGroup {
  readonly name: string;
  readonly shown: boolean;
  readonly choices: readonly Choice[];
}

interface Props {
  label: string;
  groups: readonly ChoiceGroup[];
  chosen: string;
  onChoose: (id: string) => void;
  onEnter: () => void;
  onPointerChoice?: () => void;
  listRef?: Ref<HTMLDivElement>;
}

/** Enter is heard as the choice's own press, not as React Aria's action, which a tap would be. */
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
      // As the press lifts, not as it lands: what's chosen can change what sits round the list.
      shouldSelectOnPressUp
      selectedKeys={[chosen]}
      onSelectionChange={(keys) => {
        const [id] = [...(keys === "all" ? [] : keys)];
        if (typeof id === "string" && id !== chosen) onChoose(id);
      }}
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
              onPress={({ pointerType, key }) => {
                if (key === "Enter") {
                  onEnter();
                  return;
                }
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

function Tick() {
  return (
    <svg className={styles.tick} width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
      <path d="M2.5 6.4 5 8.8 9.5 3.4" />
    </svg>
  );
}
