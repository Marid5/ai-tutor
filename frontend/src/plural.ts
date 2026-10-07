// English UI copy: "1 card", "3 cards". Intl.PluralRules keeps the choice in
// one place, so another UI language only needs another rule set and word list.
const rules = new Intl.PluralRules('en');
const numbers = new Intl.NumberFormat('en');

/** The word form for `n`: `one` for exactly one, `other` (default: one + "s") otherwise. */
export function plural(n: number, one: string, other = `${one}s`): string {
  return rules.select(n) === 'one' ? one : other;
}

/** `n` followed by the right word form, e.g. `count(3, 'card')` → "3 cards". */
export function count(n: number, one: string, other?: string): string {
  return `${numbers.format(n)} ${plural(n, one, other)}`;
}
