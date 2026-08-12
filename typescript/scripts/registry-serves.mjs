#!/usr/bin/env node
// Ask npm whether it is serving a version, rather than believing `npm publish`.
//
// `npm publish` printing `+ @bwthomas/vicary@0.2.1` is the system under
// measurement describing itself. The claim worth making is "the registry serves
// it", and only the registry can make it.
//
// **Why this is a script and not a shell loop in the workflow.** The check it
// replaces was `npm view "$name@$version" version >/dev/null 2>&1` — a command
// whose non-zero exit means "not published", "package does not exist", "network
// refused", "registry 503" and "you are not authenticated", all collapsed into one
// bit with both streams sent to /dev/null. Every one of those is a place where
// "the version is not there yet" and "I could not tell" produce the same output,
// and the second must never read as the first, because the whole point of the step
// is to disbelieve a success message.
//
// Here the two are separate return values — `versions: null` is unknown,
// `versions: []` is a package the registry has never heard of — and
// `typescript/test/packaging.test.ts` drives both without a network.
//
// The counterpart of `ruby/scripts/registry_serves.rb`.
//
// Usage:
//   node scripts/registry-serves.mjs @bwthomas/vicary 0.2.1
//   node scripts/registry-serves.mjs --attempts 12 --interval 10 @bwthomas/vicary 0.2.1

export const DEFAULT_ATTEMPTS = 12;
export const DEFAULT_INTERVAL = 10;
export const HOST = "https://registry.npmjs.org";

/**
 * What one lookup found.
 *
 * `versions` is null when the lookup itself failed, which is a different fact from
 * "the package exists and this version is not among them" and must never collapse
 * into it.
 *
 * @typedef {object} Answer
 * @property {string[] | null} versions
 * @property {string | null} error
 */

/**
 * @param {Answer} answer
 * @param {string} version
 */
export function serving(answer, version) {
  return answer.versions !== null && answer.versions.includes(version);
}

/** @param {Answer} answer */
export function unknown(answer) {
  return answer.versions === null;
}

/**
 * Parse a packument. Separate from the fetch so the shape this depends on — a JSON
 * object carrying `versions` keyed by version string — is checked by a test rather
 * than by a release.
 *
 * @param {string} body
 * @returns {Answer}
 */
export function parse(body) {
  let parsed;
  try {
    parsed = JSON.parse(body);
  } catch (error) {
    return {
      versions: null,
      error: `the registry did not return JSON: ${
        error instanceof Error ? error.message : String(error)
      }`,
    };
  }

  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    return {
      versions: null,
      error: `expected a JSON object packument from the registry, got ${
        Array.isArray(parsed) ? "array" : typeof parsed
      }`,
    };
  }

  if (!parsed.versions || typeof parsed.versions !== "object") {
    return {
      versions: null,
      error:
        'the packument carried no "versions" object — the payload shape moved',
    };
  }

  return { versions: Object.keys(parsed.versions), error: null };
}

/**
 * One lookup against the registry.
 *
 * @param {string} packageName
 * @param {{host?: string, get?: (url: string) => Promise<Response>}} [options]
 * @returns {Promise<Answer>}
 */
export async function fetchVersions(packageName, options = {}) {
  const host = options.host ?? HOST;
  // A scoped name's `/` must be encoded or the registry reads it as a path.
  const url = `${host}/${packageName.replace("/", "%2f")}`;
  const get = options.get ?? ((target) => fetch(target));

  try {
    const response = await get(url);
    if (response.status === 404) {
      // The package itself is unknown to the registry. Not an error: it is
      // exactly what a first release looks like right up until the moment it
      // is not.
      return { versions: [], error: null };
    }
    if (!response.ok) {
      return {
        versions: null,
        error: `${url} returned ${response.status} ${response.statusText}`,
      };
    }
    return parse(await response.text());
  } catch (error) {
    return {
      versions: null,
      error: `${url} could not be reached: ${
        error instanceof Error ? `${error.name}: ${error.message}` : String(error)
      }`,
    };
  }
}

/**
 * Poll until the registry serves `version`, or give up out loud.
 *
 * `sleeper` and `fetcher` are injected so the test can run the real control flow —
 * including the give-up path — in microseconds and with no network.
 *
 * @returns {Promise<number>} process exit code
 */
export async function waitFor(packageName, version, options = {}) {
  const attempts = options.attempts ?? DEFAULT_ATTEMPTS;
  const interval = options.interval ?? DEFAULT_INTERVAL;
  const out = options.out ?? console;
  const fetcher = options.fetcher ?? ((name) => fetchVersions(name));
  const sleeper =
    options.sleeper ?? ((seconds) => new Promise((r) => setTimeout(r, seconds * 1000)));

  for (let i = 0; i < attempts; i += 1) {
    const answer = await fetcher(packageName);

    if (serving(answer, version)) {
      out.log(`registry.npmjs.org is serving ${packageName} ${version}`);
      return 0;
    }

    if (unknown(answer)) {
      out.log(`attempt ${i + 1}: could not read the registry — ${answer.error}`);
    } else {
      out.log(
        `attempt ${i + 1}: registry.npmjs.org lists [${answer.versions.join(", ")}], ` +
          `not ${version}`,
      );
    }

    if (i !== attempts - 1) await sleeper(interval);
  }

  out.log("");
  out.log(
    `published ${packageName} ${version} but registry.npmjs.org never served it ` +
      `across ${attempts} attempts.`,
  );
  out.log("The publish reported success; the registry disagrees. Do not treat this");
  out.log("release as published until that is reconciled.");
  return 1;
}

async function main(argv, out = console) {
  let attempts = DEFAULT_ATTEMPTS;
  let interval = DEFAULT_INTERVAL;
  const rest = [];

  while (argv.length > 0) {
    const arg = argv.shift();
    if (arg === "--attempts") attempts = Number(argv.shift());
    else if (arg === "--interval") interval = Number(argv.shift());
    else rest.push(arg);
  }

  const [packageName, version] = rest;
  if (!packageName || !version) {
    out.log("usage: registry-serves.mjs [--attempts N] [--interval S] NAME VERSION");
    return 2;
  }

  return waitFor(packageName, version, { attempts, interval, out });
}

if (import.meta.url === `file://${process.argv[1]}`) {
  process.exit(await main(process.argv.slice(2)));
}
