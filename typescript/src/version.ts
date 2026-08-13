/**
 * Single source of this package's version.
 *
 * Shared across all three front doors on purpose: one detector, one number. A
 * TypeScript 0.3.0 that corresponds to nothing on PyPI cannot be reasoned about,
 * and the parity claim is between *versions*, not between package names.
 */
export const VERSION = "0.2.3";
