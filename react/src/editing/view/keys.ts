// The key a shortcut is held with, as this platform's keyboards name it.
const APPLE = /Mac|iPhone|iPad/.test(navigator.userAgent);
export const COMMAND = APPLE ? "⌘" : "Ctrl ";
