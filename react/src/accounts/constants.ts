// The accounts feature's tuning.

/** Back from Stripe's checkout, the account is asked this many times for its paid month: Stripe's word reaches the server in seconds. */
export const PAID_ASKS = 10;

/** How long between those asks: ten of them wait out a slow webhook without asking the server more than it needs. */
export const PAID_ASK_EVERY_MS = 2_000;
