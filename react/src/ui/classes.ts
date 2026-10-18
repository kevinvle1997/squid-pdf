/** A class list of `names`, in order, leaving out any not given. */
export function classes(...names: (string | false | null | undefined)[]): string {
  return names.filter(Boolean).join(" ");
}
