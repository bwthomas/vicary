/**
 * vicary — offline redaction of personal names in student compositions.
 *
 * The npm front door. **The detector is not ported yet**: what is here is the
 * asset layer, which loads the identical gazetteer bytes the Python package
 * loads, and the conformance harness that will score the port frame by frame
 * against `conformance/frames.json`.
 *
 * Nothing here should be pointed at student writing until
 * `npm run conformance` reports 51 of 51 frames matching the reference output
 * byte-for-byte, placeholder numbering included. The scoreboard prints the real
 * count on every run precisely so that state cannot be mistaken for readiness.
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

export { VERSION } from "./version.js";
