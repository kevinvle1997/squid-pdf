import { lazy, Suspense, useEffect, useState } from "react";
import styles from "./App.module.css";
import {
  type AccountAnswer,
  accountAnswer,
  accountAtStart,
  documentsLeft,
  mustSignIn,
  signedInAs,
  signedOut,
  tokenIn,
} from "./accounts/account";
import {
  accountOncePaid,
  type BackFromPaying,
  backFrom,
  billingPortalPage,
  checkoutPage,
  type Going,
  goTo,
  isBackFromPaying,
  mayManageBilling,
  mayPay,
  paidUntil,
  paymentSaid,
} from "./accounts/billing";
import { SignInScreen } from "./accounts/view/SignInScreen";
import { forgetOrSayWhy, type Persisted, type PersistedWork, persistedDocument } from "./documents/localPersistence";
import { type Landing, type Opened, openFile, type SignInFirst } from "./documents/open";
import { Drop } from "./documents/view/Drop";
import { isTermsPath, type TermsAnswer, termsAnswer, termsHrefOf } from "./terms/terms";
import { TermsScreen } from "./terms/view/TermsScreen";
import { Button } from "./ui/Button";
import { ErrorBoundary } from "./ui/ErrorBoundary";
import { SheetScreen } from "./ui/SheetScreen";

// The editor's code comes in while the file uploads, so the landing loads only what it shows.
const loadEditor = () => import("./editing/view/EditorShell");
const EditorShell = lazy(() => loadEditor().then((module) => ({ default: module.EditorShell })));

const INVITED_ONLY = "squid-pdf is open to invited people for now. We'll email you a link that signs you in.";

export function App() {
  return (
    <ErrorBoundary fallback={<Crashed />}>
      <Screens />
    </ErrorBoundary>
  );
}

type Screen = Landing | SignInFirst | { kind: "open"; opened: Opened; persisted?: PersistedWork<unknown> };

type Forgetting = { kind: "under way" } | { kind: "failed"; detail: string };

function Screens() {
  const [screen, setScreen] = useState<Screen>({ kind: "waiting" });
  const [persisted, setPersisted] = useState<Persisted<unknown> | null>(null);
  const [forgetting, setForgetting] = useState<Forgetting | null>(null);
  const [terms, setTerms] = useState<TermsAnswer>({ kind: "asking" });
  const [fontLoaded, setFontLoaded] = useState(false);
  const [account, setAccount] = useState<AccountAnswer>({ kind: "asking" });
  const [linkToken] = useState(() => tokenIn(window.location.pathname, window.location.hash));
  const [linkFailed, setLinkFailed] = useState<string | null>(null);
  const [backFromPaying] = useState(() => isBackFromPaying(window.location.search));
  const [back, setBack] = useState<BackFromPaying | null>(backFromPaying ? { kind: "waiting" } : null);
  const [going, setGoing] = useState<Going>({ kind: "idle" });

  useEffect(() => {
    persistedDocument().then(setPersisted, () => undefined);
    termsAnswer().then(setTerms);
    accountAtStart(linkToken).then((started) => {
      if (linkToken !== null || backFromPaying) window.history.replaceState(null, "", "/");
      setLinkFailed(started.linkFailed);
      setAccount(started.account);
      if (!backFromPaying) return;
      accountOncePaid(accountAnswer).then((paid) => {
        setAccount(paid);
        setBack(backFrom(paid));
      });
    });
    // In a stand-in font the link's line can wrap, and jump back as the font comes.
    const loaded = () => setFontLoaded(true);
    document.fonts.load(`1em ${getComputedStyle(document.body).fontFamily}`).then(loaded, loaded);
  }, [linkToken, backFromPaying]);

  useEffect(() => {
    if (persisted === null) return;
    const forgetting = setTimeout(
      () => {
        setPersisted(null);
        void forgetOrSayWhy(persisted.file.name);
      },
      Math.max(0, persisted.until - Date.now()),
    );
    return () => clearTimeout(forgetting);
  }, [persisted]);

  async function signOut() {
    const answer = await signedOut();
    if (answer.kind === "failed") {
      setScreen({ kind: "failed", detail: answer.detail });
      return;
    }
    setAccount(answer);
    await forget();
  }

  async function goToStripe(page: () => ReturnType<typeof checkoutPage>) {
    // A second press would ask Stripe for a second page.
    if (going.kind === "under way") return;
    setGoing({ kind: "under way" });
    const went = await page();
    if (went.kind === "failed") {
      setGoing(went);
      return;
    }
    goTo(went.url);
  }

  function reopen() {
    // A delete under way would take the work from under the editor.
    if (persisted === null || forgetting?.kind === "under way") return;
    void loadEditor();
    setScreen({
      kind: "open",
      opened: { file: persisted.file, doc: persisted.doc },
      persisted: { work: persisted.work, until: persisted.until },
    });
    setPersisted(null);
  }

  async function forget() {
    if (persisted === null) return;
    setForgetting({ kind: "under way" });
    const detail = await forgetOrSayWhy(persisted.file.name);
    if (detail !== null) {
      setForgetting({ kind: "failed", detail });
      return;
    }
    setPersisted(null);
    setForgetting(null);
  }

  async function open(file: File) {
    // A second drop mid-upload would race the first to the editor.
    if (screen.kind === "opening") return;
    void loadEditor();
    setScreen({ kind: "opening", name: file.name, sent: 0, total: file.size });
    const opening = await openFile(file, (sent, total) =>
      setScreen((now) => (now.kind === "opening" ? { ...now, sent, total } : now)),
    );
    setScreen(opening);
    if (documentsLeft(account) === null) return;
    const asked = await accountAnswer();
    if (asked.kind === "read") setAccount(asked);
  }

  if (isTermsPath(window.location.pathname) && terms.kind !== "none") return <TermsScreen answer={terms} />;
  if (linkToken !== null && account.kind === "asking") {
    return <SignInScreen why={INVITED_ONLY} signingIn failed={null} />;
  }
  if (mustSignIn(account)) return <SignInScreen why={INVITED_ONLY} signingIn={false} failed={linkFailed} />;
  if (screen.kind === "sign in") return <SignInScreen why={screen.detail} signingIn={false} failed={null} home />;
  if (screen.kind !== "open") {
    const landing =
      screen.kind === "waiting" && linkFailed !== null ? { kind: "failed" as const, detail: linkFailed } : screen;
    return (
      <Drop
        landing={landing}
        signedInAs={signedInAs(account)}
        documentsLeft={documentsLeft(account)}
        paidUntil={paidUntil(account)}
        payment={paymentSaid(going, back)}
        goingToStripe={going.kind === "under way"}
        onPay={mayPay(account) ? () => void goToStripe(checkoutPage) : null}
        onManageBilling={mayManageBilling(account) ? () => void goToStripe(billingPortalPage) : null}
        onSignOut={() => void signOut()}
        termsHref={fontLoaded ? termsHrefOf(terms) : null}
        persisted={persisted}
        forgetFailed={forgetting?.kind === "failed" ? forgetting.detail : null}
        onChoose={(file) => void open(file)}
        onReopen={reopen}
        onForget={() => void forget()}
      />
    );
  }
  const { opened } = screen;
  return (
    <Suspense fallback={null}>
      {/* Keyed: the editor takes `opened` only as it starts, so another document is another editor. */}
      <EditorShell
        key={opened.doc.id}
        opened={opened}
        onOpen={(file) => void open(file)}
        persisted={screen.persisted}
      />
    </Suspense>
  );
}

function Crashed() {
  return (
    <SheetScreen title="Something went wrong, and this page stopped">
      <p className={styles.fine}>
        Reload to start again. Changes kept in this browser are offered there to open again.
      </p>
      <Button variant="primary" className={styles.reload} onPress={() => window.location.reload()}>
        Reload
      </Button>
    </SheetScreen>
  );
}
