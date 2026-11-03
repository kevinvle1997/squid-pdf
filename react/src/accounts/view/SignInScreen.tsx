import { type FormEvent, useState } from "react";
import { Input, Label, TextField } from "react-aria-components";
import { AfterDelay } from "../../ui/AfterDelay";
import { Button } from "../../ui/Button";
import { SheetScreen } from "../../ui/SheetScreen";
import { Warn } from "../../ui/Warn";
import { linkAsked, type Signing } from "../account";
import styles from "./SignInScreen.module.css";

interface Props {
  why: string; // why this browser must sign in first, in plain words
  signingIn: boolean; // a link's token is being traded for a sign-in
  failed: string | null; // why a link or the server didn't sign this browser in, in the server's words
  home?: boolean; // the wordmark leads back to the landing, where the landing led here
}

/** Signing in on a screen of its own: an address typed, and a link sent there that signs this browser in. */
export function SignInScreen({ why, signingIn, failed, home = false }: Props) {
  const [email, setEmail] = useState("");
  const [signing, setSigning] = useState<Signing>({ kind: "idle" });

  async function ask(event: FormEvent) {
    event.preventDefault();
    // One link at a time: a second press would send a second email.
    if (signing.kind === "under way") return;
    setSigning({ kind: "under way" });
    setSigning(await linkAsked(email));
  }

  return (
    <SheetScreen title="Sign in" home={home}>
      <p className={styles.why}>{why}</p>
      <form className={styles.form} onSubmit={(event) => void ask(event)}>
        <TextField className={styles.field} type="email" value={email} onChange={setEmail} isRequired>
          <Label>Email</Label>
          <Input className={styles.input} autoComplete="email" spellCheck={false} />
        </TextField>
        <Button type="submit" variant="primary" className={styles.act} isDisabled={signing.kind === "under way"}>
          Email me a link
        </Button>
      </form>
      <p className={styles.status} role="status">
        {signingIn && <AfterDelay>Signing you in</AfterDelay>}
        {failed !== null && signing.kind === "idle" && <Warn mark>{failed}</Warn>}
        {signing.kind === "sent" && signing.sentence}
        {signing.kind === "failed" && <Warn mark>{signing.detail}</Warn>}
      </p>
    </SheetScreen>
  );
}
