/**
 * vicary — offline redaction of personal names in student compositions.
 *
 * The npm front door. `redact` is the one call most hosts want: hand it a
 * composition and the student's own identity, get the masked text back.
 * `redactWithReport` returns the same bytes plus the map `restore` needs to put
 * the originals back.
 *
 * **What the surface is claiming.** `redact` is exported because the port now
 * reproduces all 52 fixture frames byte-for-byte against the Python reference,
 * placeholder numbering included — the arm being `local-gazetteer-lowercase`.
 * It was deliberately absent before that, because a partially ported redactor is
 * a reasonable thing to measure and an unreasonable thing to hand a host: it
 * would mask a phone number, miss every name in the essay, and give the caller
 * no way to tell. `npm run conformance` prints the real count on every run so
 * that number is never somebody's recollection.
 */

export {
  ASSET_FILENAME,
  ASSET_PATH_ENV_VAR,
  MANIFEST_FILENAME,
  SUPPORTED_FORMAT,
  assetSearchPath,
  loadGazetteer,
  parseAsset,
  resetGazetteerCache,
} from "./asset.js";
export type { AssetMeta, Gazetteer } from "./asset.js";

export {
  DEMONYM,
  FULL_NAME,
  GazetteerAssetError,
  GazetteerIndex,
  ICONIC_SHORT,
  NOT_NOTABLE,
  PARTICLES,
  PLACE,
  ROLE_TITLES,
  TIER_NAMES,
  TITLE,
  isCommonGivenName,
  isNotable,
  isSettlement,
  isTitle,
  isTitleHead,
  isTitlePrefix,
  load,
  maxTitleTokens,
  normalize,
  notability,
  resetCache,
} from "./gazetteer.js";
export type { Notability, TierName } from "./gazetteer.js";

/** The student the detector is told about — `redact`'s second argument. Declared
 * in the conformance module because the spec is what defines it: every reference
 * arm interpolates these three strings, so a caller that omits them is measuring
 * a different system. */
export type { Identity } from "./conformance.js";

export { PlaceholderMinter } from "./minter.js";

export {
  DEFAULT_NAME_DETECTION,
  DETECTS_NAMES,
  NAMES_GAZETTEER,
  NAMES_IDENTITY,
  NAMES_LOWERCASE,
  NAME_DETECTION_ENV_VAR,
  NotPortedError,
  gazetteerOracles,
  nameDetection,
  redact,
  redactWithReport,
  restore,
} from "./redact.js";
export type { Oracles, RedactOptions, RedactionResult } from "./redact.js";

export { VERSION } from "./version.js";
