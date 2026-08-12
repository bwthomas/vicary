# Changelog

## Unreleased

### Every gate is measured on a bare checkout, so "eight of nine" is retired

* **The surname table ships.** `conformance/census/` now carries US surname
  frequencies — all 162,253 rows of the 2010 release, gzipped to 787 KB — so
  bare-surname exposure is measured on any clone and in CI. It never had been:
  the file came from census.gov, and census.gov answers its own documented URL
  with a WAF rejection page under a **200** status, so the gate reported NOT
  MEASURED everywhere but on one machine holding a hand-downloaded copy.
* **Not a sample.** Only two columns are kept — the surname and its bearer count
  — but every row is, so the measured rate is identical to the upstream's: 1.199%
  of 265,667,228 bearers, 792 of 162,253 distinct surnames, in all three ports
  and against both the shipped table and an operator's own file. Truncating the
  tail would have shrunk the numerator and the denominator at once and quietly
  redefined what the 1.25% bar means.
* **The derivation is codified, not a one-off.** `tools/census_build.py` builds it
  from a local Census copy, pins that copy's sha256 in `profile.json`, and every
  port verifies the table's own digest on load — a truncated table is refused
  rather than scored, because a short read reads as a *better* number.
* **No published package grew.** `conformance/` ships in none of the three, as
  before, and the wheel, gem and npm tarball were each checked for the file
  rather than trusted to exclude it.
* **The NOT MEASURED machinery stays and is still tested.** Each port now
  measures the four gates that declare a requirement with their inputs withheld
  *on purpose* and asserts they report NOT MEASURED by name. That case used to be
  reached by the machine happening to lack a file, which meant the guard was
  untested exactly where the data was present.
* `VICARY_EVAL_CENSUS_CSV` still wins when set, for scoring against a newer
  release.

### One version to edit instead of six

* **`just version 0.3.0` writes all five restatements** from the root `VERSION`.
  The number cannot be *read* from one place at runtime — every file carrying it
  is read where the repository root is not, an installed wheel, gem or npm
  tarball, or a build backend running before any of our code does — so it is
  written to five from one. `tools/version_sync.py` does it.
* `asset/tests/test_version.py` already failed on drift and still does, with
  `typescript/src/version.ts` added to what it checks. It deliberately does not
  import the writer's table: a shared list means one wrong pattern writes a file
  and then agrees with itself about it.

### Fixed

* `just py-build` died on a fresh clone with `No module named twine` — the `dev`
  extra installed `build` but not the `twine` its own build recipe then runs.
* `gem build` warned that `homepage_uri` and `source_code_uri` carried one URI,
  which rubygems.org renders only the first of. Each front door now declares the
  same three links — repository, changelog, issue tracker — with no key repeating
  another's value. npm keeps its `repository` object, which is read structurally
  for provenance and `directory` rather than rendered as a duplicate link.

## 0.2.2 — 2026-08-12

### The reference port measures the corpus it is the reference for

* **Python measured five of nine on a bare checkout while TypeScript and Ruby
  measured eight.** The previous release's headline — "a fresh checkout measures
  eight of nine" — was true for the two ports and false for the one they check
  themselves against. `load_essays()` had been taught to resolve the shipped
  corpus, but the gate fixture still guarded on `VICARY_EVAL_CORPUS_TSV`, so it
  reported NEEDS corpus against twenty essays in the repository. The guard now
  asks the question the other two ports ask — is the corpus that *resolves* one
  this checkout can read — and all three measure eight of nine bare, nine of nine
  with the Census file.
* **The gate report names the corpus it measured.** Two gates carry a per-corpus
  bar, so `over-fire on prose 8.150 <= 8.15 PASS` was a number filed under no
  corpus at all: on ASAP-AES the same gate reads 0.609 against 0.61. All three
  report headers now print the corpus id.

### The latency gate stops flipping on unchanged code

* **At n=20, p95 across essays *is* the maximum**, so one timed sample per essay
  asked "did a pause land in any of twenty calls" and answered a `<=` gate with
  it. Five consecutive Ruby runs of unchanged code gave 13.8, 7.4, 13.1, 7.7 and
  6.8 ms against a 10 ms bar — two failures in five, bimodal at 2x rather than
  noisy.
* **Each essay is now redacted three times and the median recorded**, identically
  in all three ports, before the cross-essay p95. Over 28 runs since, Ruby ranges
  5.8–8.7 ms with a single 10.6 ms exceedance; Python 5.4–5.7; TypeScript
  1.3–2.0. A pause now has to hit the same essay twice to move the number.
* **The bar's rationale in `gates.json` states the envelope**, because the figure
  is a claim about the length of the work — warm CPU, no network, asset load
  excluded — and not a tail-latency claim about a deployed service.

### Fixed

* Ruby passed `bar:` twice to `GateMeasurement.new`, warning on every gates run
  and silently discarding the first.
* Docs across the justfile, both READMEs and two port module headers still said
  four of nine gates need unshipped data. One does: the Census file.

### `persuade-20` is the default, and a fresh checkout measures eight of nine gates

* **Five of nine became eight of nine.** With no operator setup at all, a clone
  now measures held-out recall, KEEP precision, round-trip, unaccounted
  violations, asset entries **and the three corpus gates**. Only bare-surname
  exposure still needs a file no package ships.
* **`operator_default` still points at ASAP-AES**, so a machine with
  `VICARY_EVAL_CORPUS_TSV` configured keeps measuring the corpus it has always
  measured and does not change its answer because this landed.
* **The over-fire bar is per corpus now**, because it is the only gate whose bar
  describes the *prose* rather than the detector: 8.15 spans/essay on PERSUADE
  against 0.61 on ASAP-AES set 8. One is source-based argumentative writing that
  names `Venus`, `Vauban` and `Paris` constantly; the other is a personal
  narrative whose names were already `@PERSON` tokens. A single bar loose enough
  for the first retires the gate on the second. `bar` stays as the fallback for an
  unregistered corpus and is deliberately the tighter of the two.
* **TypeScript and Ruby read a shipped corpus.** Both had the registry and could
  resolve ids, but only Python could load `essays.json`, so pointing the default
  at a shipped corpus would have left two of three ports reporting NEEDS corpus on
  the gates it was shipped to make measurable. Both now verify every essay against
  the digest the profile pins, as the reference does.
* **Doing that exposed a dialect divergence worth one over-fire span.**
  `SENTENCE_BREAK` matches empty at offset 0; Python's `re` then retries a
  non-empty match at that same offset before advancing, while `matchAll` and
  Ruby's scan move past it. On an essay opening `"Pedestrian, bicycle, private
  cars…` the two ports never recorded the sentence start at offset 1, `Pedestrian`
  lost the sentence-initial discount its capital is owed, and both masked it —
  164 spans against Python's 163. It had been true since the ports were written
  and was invisible for exactly as long as they could not read this corpus.

### The lowercase leak `persuade-20` found is closed, in all three ports

* **Carrier recall on `persuade-20` goes 16/19 to 19/19, with no precision cost.**
  Over-firing is unchanged at 8.15 spans/essay and every ASAP-AES figure is
  untouched, so this is recall bought with nothing.
* **Two root causes, either one sufficient on its own.** An `INCONSISTENT`
  writer's lowercase route requires corroboration — the same word written
  capitalised mid-sentence somewhere in the document — and both failures were in
  how that testimony was gathered. The gate consulted only the span's **first
  token**, so a document that capitalises the *surname* of the same person
  corroborated nothing; `corroborated` had already settled that question the
  other way and this channel never adopted it. And `_SENTENCE_BREAK` read the
  period in **"Mrs. Okonkwo"** as a sentence end, putting `Okonkwo` in
  sentence-initial position — where a capital is orthographically required and
  proves nothing — which discarded the document's only testimony about that
  surname. An honorific is the exact case where the capital that follows is most
  likely to be a name, so reading it as a sentence start inverts the signal.
* **The earlier attribution was incomplete.** One of the three misses was filed
  against the carrier-injection artifact below. It had two independent sufficient
  causes, and fixing either closes it.

### A malformed injection point is refused rather than measured

* **Every `.` is not a sentence end, and the carrier harness treated it as one.**
  A frame landed inside "U.S." and split an essay into `cars in the U.` + frame +
  `S. has gone down`. That is not a harder test of the detector, it is different
  text than the gate claims to measure. `injection_points` now steps over a
  closing quote, then refuses a period with no whitespace after it, nothing after
  it, a lowercase continuation, or a known abbreviation or initial before it —
  **165 malformed points on ASAP-AES and 69 on `persuade-20` go to zero.**
* **The guard was not loose, it was guarding the wrong number.** It required
  `per_essay + 1` usable points while drawing from `stops[1:-1]`, a population two
  shorter.
* **Two ASAP-AES essays are now declared unusable rather than silently dropped.**
  Both are written with no space after their full stops — "skateboarding.Laughed",
  "Interesting fly.It was" — and offer nowhere to cut in. `carrier.json` names
  them and why, and **all three ports now reconcile carried + named against the
  corpus supplied.** The existing check compares cases built against cases
  planned, which a plan that quietly lost ten essays satisfies perfectly; that was
  unreachable while a plan always covered its whole corpus and became reachable
  the moment a short plan was legitimate.
* **The over-fire bar moves 0.60 to 0.61, and it is arithmetic rather than
  slack.** The same detector removed the same spans from the same prose: 15 spans
  over 25 essays became 14 over 23, because the two essays that left contributed
  one span between them. Only the denominator moved. Held-out recall stays 100%
  and held-out carrier spans go 29 to 24 with the two essays.

### A corpus is now declared rather than assumed, and one of them ships

* **The three corpus gates were unmeasurable on any machine but one.** They need
  real student prose, and the prose was ASAP-AES — not redistributable, reached
  through an environment variable pointing at the operator's own copy. On every
  other checkout, CI included, four of nine gates reported NOT MEASURED, and a
  board of five greens and four blanks reads very much like a board of nine.
* **`conformance/corpora/` declares corpora; `persuade-20` ships twenty essays.**
  PERSUADE 2.0 is the closest available match — argumentative essays by US
  students in grades 6-12, anonymised upstream the same way ASAP-AES is, with
  placeholder tokens standing in for names. Its copyright holder licenses it
  CC BY 4.0. A widely-mirrored academic copy of the same data says CC BY-NC-SA
  4.0 instead; we rely on the holder's grant and the disagreement is written into
  the shipped `NOTICE` rather than resolved silently.
* **The selection rule is load-bearing, and the obvious rule was wrong.** Taking
  the first twenty essays gives a median of 1,607 characters against ASAP-AES set
  8's 3,421. Latency p95 scales with the text it walks and Ruby sits at 8.8-9.9 ms
  against a 10 ms bar, so a shorter corpus would have halved the measured latency
  and turned the one gate that constrains a port into a formality — with every
  number still reading green. The band is therefore ASAP-AES set 8's own
  first-quartile-to-maximum char range, giving a mean of 3,336 against that
  baseline's 3,291. Within the band nothing is hand-picked, and
  `tools/persuade_build.py` reproduces the selection from an upstream whose digest
  it pins.
* **What was welded in is now read off a profile:** the essay set, the row limit,
  the column names and the file encoding. `VICARY_EVAL_CORPUS` names a corpus
  outright; failing that a configured `VICARY_EVAL_CORPUS_TSV` keeps an operator
  on ASAP-AES, so the machine that has always measured it does not change its
  answer because this landed; failing both, the registry default applies.
* **`carrier.json` and `measured.json` are keyed by corpus id** (both bumped to
  `document_version` 2). Offsets are character positions into specific essays, so
  there is no such thing as one that means anything in two corpora — and two of
  the three corpus gates are properties of the prose, not the detector. An
  unkeyed block invited measuring a second corpus and reading the difference from
  the first as a port regression. Both readers refuse an unknown version rather
  than finding no `cases` and building zero carrier essays, which in a `<=` gate
  is the most comfortable pass on the board.
* **Regeneration merges rather than replaces.** A machine without the operator's
  ASAP-AES copy can rebuild the shipped corpus's plan, and must not delete the
  baseline it cannot rebuild as a side effect. Every skipped corpus says so on
  stdout.
* **The ASAP-AES path is byte-identical.** Its 25 essays load exactly as
  `load_set8` returned them, its recorded plan was carried across verbatim, and
  re-measuring reproduces all seven published figures and the carrier-text digest.

### What the new corpus found, which ASAP-AES could not

* **`persuade-20` is not yet the default, because it fails a gate.** Carrier recall
  is 16/19 (84.2%) against a 100% bar. Shipping it as the default would put a red
  gate on main, so the corpus, its plan and its measured baseline all land now —
  visible and reproducible — and the default flips once the leak is closed.
* **Two of the three misses are a real leak; one was the harness.** `build_cases`
  treats every `.` as a sentence end, so one frame was injected inside "U.S.":
  `cars in the U.` + frame + `S. has gone down`. That flaw is pre-existing and
  worse on ASAP-AES — **14 of its 75 injection points** against 4 of 60 here.
  Beside it, the guard requires `per_essay + 1` sentence ends but then samples
  from `stops[1:-1]`, which needs `+ 2`.
* **The remaining two are the all-lowercase name `terrence okonkwo`, and they are
  not the same failure.** Both corpora exercise the same 15 frames, and this one
  passes 2/2 on ASAP-AES, so it is context rather than coverage. Splitting them by
  `capitalisation_habit`: one essay reads `CONSISTENT`, which withdraws the
  permissive lowercase route by design and is the documented precision trade. The
  other reads `INCONSISTENT` — the route is available, the document contains a
  cleanly-injected capitalised `Okonkwo`, and it still leaks. That one is a defect,
  and the token rule behind it is not yet identified.
* **Over-firing is 8.15 spans/essay here against ASAP-AES's 0.60, and that is the
  prose.** The surfaces are `Venus` (11), `Vauban` (4), `Paris` (4), `Earth` (3),
  `Science Olympiad`, `Mathcounts`, `Georgia's Hands-Free Law`. PERSUADE's prompts
  are source-based, so the essays name real-world entities constantly; ASAP-AES set
  8 is a personal narrative whose names were already `@PERSON` tokens. It is the
  clearest possible case for a per-corpus bar, and setting one is part of the
  default flip rather than this change.

### All three ports measure all nine gates

* **The three corpus gates were Python's alone; now every port measures them.**
  TypeScript and Ruby had no corpus machinery at all. Both now load the operator's
  corpus, rebuild the carrier essays, and measure held-out carrier recall,
  over-firing on real prose and their own latency. With both data files
  configured every port reports **9 of 9 holding**.
* **They agree on the two gates that are properties of the detector, exactly**:
  100% carrier recall (29/29 held-out REDACT spans) and 0.60 over-fired spans per
  essay (15 across 25) in all three.
* **They differ on the one that is a property of the language, which is the point
  of porting it.** Latency p95: TypeScript 2.1–2.6 ms, Python 4.0–4.2 ms, **Ruby
  8.8–9.9 ms against a ≤ 10 ms bar**. Python's number said nothing about Ruby's,
  and Ruby turns out to run nearest the bar by a wide margin — a live constraint
  on that port rather than a formality.
* **`conformance/carrier.json` records where each frame is injected**, so three
  languages build byte-identical carrier text without three copies of MT19937.
  Each port asserts the sha256 of the 25 injected essays, which is the assertion
  that makes the agreement mean anything: injecting the same frames at different
  offsets still yields 29/29 and 15 spans, so the metrics alone would not have
  caught it. The file carries ids, digests, offsets and counts — never essay
  text — and every port verifies an essay against its digest before using an
  offset into it.
* **The one-time asset load is now excluded from latency in all three ports.**
  Loading 360,793 entries costs ~84 ms in Python and ~207 ms in Ruby, and
  whichever essay ran first paid all of it; at n=25 that one sample sits at or
  above p95 and set the gate's answer by itself. The same Python code reported
  3.1 ms or 4.0 ms depending only on whether something earlier in the process had
  touched the asset. Ruby read 14.3 ms cold against its 10 ms bar and 7.6 ms warm.
* **A corpus that matches the plan only partly is refused, not measured.** Found
  by pointing the harness at a one-essay TSV: it built zero cases and reported
  over-firing and latency as **0.0**, which in a `<=` gate is the most
  comfortable pass on the board — two gates went green on no data at all, and the
  scoreboard said "7 of 8 hold". All three ports now require every planned essay
  or none, and say which are missing.
* **The ASAP-token split in the over-fire metric is currently inert, and now says
  so.** The 25 gate essays carry 713 `@`-tokens, 28.5 per essay, and the redactor
  rewrites none of them; deleting the split changes no gate number. The docstring
  claiming the `{USERNAME}` pattern "does exactly that to every `@`-token in the
  corpus, at ~22 per essay" was stale. It stays, because the leg it guards is one
  pattern change from returning — but a green suite no longer implies it is
  load-bearing.

### All nine gates measured at once, and what the corpus three actually sit on

* **The three corpus gates now have their envelope recorded**, having been run
  against ASAP-AES set 8 rather than quoted from an earlier operator run. With
  both the corpus and the census file configured, Python measures and passes all
  nine. The sample is the first 25 essays in file order — reproducible, not
  sampled.
* **The over-fire gate passes with zero margin, which the table hid.** 15
  over-fired spans across 25 essays is exactly 0.60 against a bar of ≤ 0.60; one
  more span anywhere reads 0.64 and fails. It is deterministic across seven runs,
  so this is a knife-edge rather than noise, and worth knowing before the next
  tier lands.
* **Latency p95 is a band, not the 3.4 ms the README quoted.** 3.1–3.5 ms over
  seven warm runs and ~4.1 ms cold, against a 10 ms bar. The old figure sits
  inside the band; it just was not the whole of it.

### The census gate goes from one port's skip to three ports' number

* **The bare-surname gate had never once been measured by a run of this
  repository.** Python carried the machinery and skipped, because nothing pointed
  `VICARY_EVAL_CENSUS_CSV` anywhere; TypeScript and Ruby carried no machinery at
  all and hard-coded every `requires` gate to `NOT MEASURED` regardless of what a
  caller supplied. The 1.20% in the README came from an operator run nobody could
  reproduce from the repo. All three now measure it, and their reports are
  byte-identical — same sha256 over the rendered block: 162,253 surnames scored,
  792 matched, 1.20% population-weighted (3,185,816 of 265,667,228 bearers).
* **A satisfied requirement buys exactly one gate.** Operator-supplied values
  live in a map separate from the fixture-derived ones in all three ports, so
  nothing computed from the 54 frames can be printed under a gate that asked for
  a corpus. Supplying the census file takes the board from five of nine to six of
  nine and leaves the three corpus gates `NOT MEASURED` — asserted by a test, not
  by intent. The scoreboard reads `FROM census` where it used to read
  `NEEDS census`, so a measured gate never looks like it is still waiting.
* **The `.zip` is refused by name in the two ports that cannot read one.** Neither
  Node's nor Ruby's standard library has a zip reader, and the failure that
  matters is not the crash — it is a binary read parsed as CSV yielding zero rows,
  which is a *lower* exposure than the truth. Both also refuse a file parsing to
  under 100,000 rows, for the same one-directional reason the builder does.
* **Two of three mutations moved the number; the third is inert and now known to
  be.** Dropping the demonym tier moves 1.1992% → 1.1718%, and counting the
  `ALL OTHER NAMES` aggregate row moves it → 1.0800%, identically in both new
  ports. Removing the single-token filter on `place` moves nothing at all: no
  census key contains a space, so the 25,115 multi-token place entries it excludes
  could never have matched one. The filter is defensive, not load-bearing.
* **`fetch_census_surnames()` no longer reaches census.gov.** The host now answers
  the documented URL with HTTP **200** and a WAF rejection page in the body, under
  any User-Agent — so the fallback returns 247 bytes of HTML that a status check
  reads as success. The local-copy path is unaffected, and is what the gate uses.

### Ruby measures five of the nine gates, closing the last parity gap

* **The gem printed `NOT MEASURED` for all nine gates.** TypeScript measured five
  and Ruby measured none, so "parity" held at the frame level and stopped there:
  the gem reproduced all 38 masking-required frames while saying nothing about
  recall, precision, round-trip, unaccounted violations or the asset. `Vicary::Gates`
  now measures the same five TypeScript does, and reports the same values Python
  does — 16/16 held-out REDACT spans, 21/21 KEEP spans intact, 54/54 frames
  restoring exactly, one violation and it the accounted-for `Robinson` keep, and
  360,793 asset entries.
* **Measured, not read.** The spec carries `aligns` and `mapping` per frame,
  computed by the reference, and quoting them would have made the gem's gate
  report a restatement of Python's. The span-to-placeholder mapping is recovered
  from the port's own output by chunk matching instead, so the masker is never the
  witness for its own redaction.
* **Two hazards that only exist in Ruby, both now covered by a test that fails
  without the guard.** `String#split` drops trailing empty fields where
  JavaScript's keeps them, which silently shortens the chunk list and makes a
  placeholder ending a sentence recover the wrong region — hence `split(re, -1)`.
  And Ruby's `^`/`$` match at every line boundary with no opt-out, so a
  line-anchored reconstruction reports a clean alignment while a whole line of the
  essay is missing; the pattern is anchored with `\A`/`\z` at both ends. Neither
  is reachable from the fixture, which is single-line.
* **The four gates needing an essay corpus or the Census file stay
  `NOT MEASURED`, and are asserted to stay that way** — never given a value, not
  even 0, which in a `<=` gate would read as the most comfortable pass on the
  board.
* **`just gates` now runs every port present, not only Python.** It claimed "the
  nine, in every language present" while running one. `npm run gates` and
  `rake gates` are its per-port entry points.

### The npm credential is proven before a release depends on it

* **Every run of the npm publish step had either failed or been skipped**, so the
  OIDC trusted-publishing credential the workflow now relies on had never once
  succeeded, and the first thing that would have found out is a real release.
  `release-npm.yml` takes `publish_probe=true`, which mints the ID token, exchanges
  it against the registry for a publish credential, and stops.
* **It does not use `npm publish --dry-run`,** which looks like the obvious probe
  and is a false green: npm calls the exchange unconditionally before any dry-run
  branch, but `oidc.js` is documented to never throw — every failure path logs at
  verbose and returns `undefined`, leaving the credential simply unset. The failure
  surfaces only on upload, and a dry run never uploads. The probe makes the two
  HTTP calls itself, where a refusal is a hard failure it can see.
* **Run, and the credential is confirmed good.** The exchange issued a token for
  `@bwthomas/vicary` and the registry still serves only `0.2.1`, so nothing was
  published — the trusted publisher was unverified until this run. Getting there
  cost one round trip: the endpoint answers `201`, because it *mints* a credential,
  and demanding exactly `200` reported a correct configuration as `REFUSED` while
  printing the token it had just been issued into a public log. It accepts 2xx and
  treats the token's presence as the proof, and it now redacts a `token` field
  before printing any body — including on the path that is not supposed to have
  one.

## 0.2.1 — 2026-08-11

The Ruby gem published, exercising the published artifact found an email that came
apart instead of masking, and the npm package went out scoped.

All three front doors carry this version. 0.2.0 shipped to PyPI and RubyGems with
the email defect below and **should be skipped**; there is no 0.2.0 on npm.

### An email or URL carrying the writer's own name masks whole

* **A school-issued address was shredded rather than masked.** Identity
  interpolation is a literal-name substitution and it ran ahead of every
  structured pattern, so `marguerite.delacroix-whitfield@westfieldhigh.k12.oh.us`
  came out as `{NAME_2}.{NAME_1}{USERNAME_1}.k12.oh.us` — four placeholders of
  three wrong kinds, the domain tail `.k12.oh.us` left in the clear, and a span
  the round trip could not restore. Not a leak of the name or the address, and
  that is exactly why it survived: every presence-based check passes on it.
  `first.last@district.org` is the ordinary shape of a school address, so this was
  the common case, not the corner.
* **EMAIL and URL now run before identity interpolation; everything else still
  runs after.** The asymmetry is the argument. Both patterns are anchored on
  structure a name cannot supply — EMAIL needs an `@` and a dotted TLD, URL needs
  a scheme or a `www.` — so neither can reach into prose and take a bare surname
  out of it, while ADDRESS (four capitalised words before a street suffix) plainly
  can and therefore still follows identity.
* **No existing frame's output moved.** Numbering is per kind, so `{EMAIL_1}` is
  the first email whether emails are matched before or after names; the golden
  regenerated with 53 insertions and one deletion, that deletion being the fixture
  version. The old `structured-email` frame could not have caught this — its local
  part is `m.delacroix2011`, abbreviated and digit-suffixed, so no token in it
  equals the writer's name and the ordering never mattered.
* **Two frames added, so the fixture is 54 and the masking-required bar is 38** —
  the email and the URL, both pinned in Python, TypeScript and Ruby, plus unit
  pins on both directions of the ordering. Fixture version `2026-08-11.2`.
* **The reference had it too.** Found by running the *published* gem, then
  reproduced against the Python arm before touching either port, so the fix landed
  in the reference first and the ports followed its bytes rather than each other's.

### One narrative, at the root

* **The full narrative moved from `python/README.md` up to the repository
  README**, which is what that file said would happen when the second front door
  shipped. All three package READMEs are now the same shape — install, call,
  check, and a link up — so there is one description of the detector instead of
  three that drift. PyPI renders the shorter Python front door as a result.
* **Two documented numbers were wrong and are now reconciled against
  `conformance/gates.json` rather than carried forward.** The measurement table
  cited fixture `2026-08-06.3` and an over-firing bar of ≤ 0.72 spans/essay; the
  bar has been 0.6 since the SSA births change, which the 0.2.0 notes describe
  and the table had not caught up with. The five corpus-free gates and the four
  needing operator data are now separate tables, because a single table listing
  both invites reading CI as having measured all nine.

### The npm package is published, scoped

* **`@bwthomas/vicary` on npm.** npm's typosquat filter refuses the unscoped
  `vicary` as too similar to the existing `vary`, and the name is owned by nobody,
  so a 404 lookup reads as available and is not. The scope is interim — an appeal
  for the bare name is open — and it makes npm the one front door whose import
  path differs from `vicary`.
* **No token in the repository.** The old `NPM_TOKEN` was a classic Publish token
  that demands a 2FA OTP CI cannot supply; it is deleted from npm and from this
  repository. `release-npm.yml` now authenticates by OIDC trusted publishing, on
  Node 24 with npm upgraded past 11.5.1 — npm 10.x does not attempt the OIDC
  exchange at all and fails as though the credential were wrong.
* **0.2.1 was published by hand**, because npm configures trusted publishing on a
  package's settings page and so cannot pre-authorize a package that does not yet
  exist. It therefore carries no provenance attestation; every later version does.
  The workflow now asks the registry before publishing and skips a version already
  served, which is the one seam that hand-publishing the first version creates.
* **`npm pack --json` changed shape in npm 12** — an array of one entry up to
  npm 11, an object keyed by package name from 12 on — so upgrading npm to reach
  the OIDC minimum broke the step that checks the tarball carries the gazetteer.
  It reads both shapes now and fails loudly on a third, because "no files" and
  "cannot see the files" must not reach the same conclusion when the conclusion is
  whether the asset shipped.

### The gem is published

* **`vicary 0.2.0` is on RubyGems**, via trusted publishing with no API key in the
  repository. The push probe ran first and ended in the authenticated 403 it is
  designed to provoke, which is what established that the publisher's repository,
  workflow filename, environment and audience claims all match before a real push
  depended on them.

## 0.2.0 — 2026-08-11

The build mechanism left the Python package, so the shared asset is shared rather
than borrowed; a hometown named after a park stops leaking; and two identity
patterns that had never matched anything now do.

**Breaking for anybody who imported `vicary.build` or ran `vicary-assets fetch`.**
Neither exists. Rebuilding the gazetteer is `just asset-fetch` from a checkout.

**Three deliberate changes to detector output since 0.1.1**, described below: a
span that is both a known settlement and landmark-shaped is now redacted rather
than kept; a settlement carrying an organisation suffix types `{LOCATION}` rather
than `{ORGANIZATION}`; and a sentence-initial possessive can be vouched for by the
writer's own capitalisation. Only the first changes any *verdict*. No conformance
frame's masked output moved for any of the three.

### A hometown that ends in a landmark suffix is redacted, not kept

* **383 real settlements were being kept because their names end in a landmark
  suffix.** `is_public_landmark` is a pure suffix rule, and it was consulted
  *before* the settlement tier with an unconditional keep. The gazetteer's own
  settlement tier holds 391 multi-token entries whose last token is a landmark
  suffix — 8 independently notable, so 383 real towns — and a student's hometown
  leaked whenever it was named after a park, lake, valley or falls: park 94,
  lake 82, valley 60, falls 49, island 32, river 30, mountain 12, gardens 12.
  "Allen Park", "Avon Lake" and "Asbury Park" all passed through untouched while
  "Akron" in the same sentence masked correctly.
* **Classification is now a tag set plus one ordered precedence table**, rather
  than an `if` order split across two functions. A span carries every tag its
  evidence supports — `LOCATION`, `ORGANIZATION`, `LANDMARK`, `PERSON` — and the
  first matching row of the table decides both the mask/keep verdict and the
  placeholder. One principle orders it: a lookup beats a guess, and a guess that
  masks beats a guess that keeps.
* **A town whose name ends in an organisation suffix now types `{LOCATION}`, not
  `{ORGANIZATION}`.** Same principle: the settlement tier is an *exact* lookup,
  an org suffix is a guess from a word ending. Of the 16 tier entries carrying
  both, 12 are ordinary towns — Falls Church, Cut Bank, Union, Agency, College,
  Council, Mount Union, West Union, New Market, East New Market, Country Club,
  South Bank — and 4 are tier noise. Both rows mask either way, so nothing about
  *whether* a span is redacted changed; no conformance frame's output moved. It
  costs the ordinary case nothing, because "Progressive Insurance" is in nobody's
  settlement tier.
* **A landmark the tiers do not know is still kept.** The suffix rule is a
  backstop, and it is unchanged where it is the only evidence: "Lincoln Memorial"
  and "Grand Canyon" are not in the settlement tier and are notable in their own
  right.
* **Nothing changes for a caller that wires no settlement oracle.** Without one
  the `LOCATION` tag is unreachable, so the table resolves exactly as before.
* **The table ships as spec data.** `conformance/primitives.json` carries the rows
  themselves plus a `masks_with_settlement` section, because this is the one part
  of the detector a port can get wrong while passing every frame — reordering two
  rows changes which spans survive, and only a colliding span can tell. The frame
  set had no colliding span for the detector's whole life, which is exactly how
  this survived; `intersect-hometown-that-ends-in-a-landmark-suffix` is now one.
* **The two suffix lists the table reads ship as spec data too**, for the same
  reason and a measured one. `primitives.json` now carries `suffixes.organization`
  (46 entries) and `suffixes.landmark` (36) in full, and each port asserts its own
  set against them. The token lists reach only 3 of 46 organisation suffixes and 3
  of 36 landmark suffixes — `inc`, `school`, `church` and `library`, `memorial`,
  `park` — so every other entry was hand-transliterated and checked by nothing:
  deleting `hospital` or `valley` from the TypeScript set left all 179 other tests
  green, which is how it was confirmed. A port short one suffix keeps a town or
  masks a landmark in prose the fixture happens not to contain. Counts alone would
  not close it either — 46 entries with one misspelled is still a kept town. No
  detector output changed; this pins data that was already identical.

### Every hand-typed list in candidate generation is now spec data

* **`word_lists` carries the honorifics (32), particles (19) and clitics (16)**,
  and each port compares its own **in order**. The spec's inputs exercise 7, 3 and
  2 of them respectively, so most of each was checked by nothing — the same hole
  the suffix lists had, in the stoplist and heading path rather than the
  classification one.
* **Order is compared, not just membership**, because it is load-bearing here in a
  way it is not for the suffix sets: honorifics and particles are joined into
  regex alternations where branch order decides which alternative matches first,
  and `withoutClitic` strips the first clitic that matches. Swapping `"Mr"` and
  `"Mrs"` fails this assertion and nothing else, which is how that was confirmed.

### The Ruby detector is ported: 0 of 36 to 36 of 36

* **`Vicary.redact` does the deciding**, through the same two passes in the same
  order as the reference — the identity and structured pass, then candidate
  generation, through one minter. `candidates.rb`, `structured.rb` and
  `minter.rb` are new; `redact.rb` stopped raising `NotPortedError`. 52 of 52
  frames byte-identical, placeholder numbering included.
* **The restore map is checked, not just the masked bytes.** The Ruby
  conformance suite compared `masked` alone, so a port could number correctly and
  be unable to put the words back. It now asserts placeholders in the golden's
  order of first appearance, and that `Vicary.restore` returns every frame's
  original bytes.
* **`primitives_test.rb` reads `conformance/primitives.json`** — forty-odd
  primitives over the shared corpus, 2,526 assertions, with the completeness
  check that fails when the spec carries a section this port does not check.

### Three layers of checking, because the frames catch one mutation in eleven

* **Measured on the finished port rather than argued.** Eleven deliberate
  mutations to `candidates.rb`: reversed sort ties, swapped precedence rows, a
  wrong corroborating tier, a deleted organisation suffix, reordered honorifics,
  a disabled apostrophe trim, `ALLCAPS_RUN` at 99, `LOWERCASE_MIN_TOKENS` at 1,
  and three anchor changes. **The 36 conformance frames caught one.** The
  primitives spec caught seven. Three were inert — two provably so, and both are
  recorded where somebody would otherwise "fix" them.
* **The eleventh was real and both corpora were blind to it.** Writing `\z` as
  the idiomatic Ruby `$` in `RELATION_ATTACHED_BEFORE` attaches "my cousin" on
  one line to a name on the next, and leaves every frame and every primitive
  green — because both corpora are single-line and the rule only diverges across
  a newline. The same hazard sits in `ZIP`, where `$` masks any five-digit number
  ending a line of a hard-wrapped essay.
* **So `rake redaction_parity` runs both implementations over prose neither
  corpus contains and diffs the bytes**, and `test/dialect_test.rb` pins each
  Ruby-versus-Python regex difference *in both directions* — that the pattern as
  written gives the reference answer, and that the idiomatic spelling gives a
  different one. An assertion that only pinned the current answer would still
  pass once the difference evaporated, and then it would be guarding nothing.
* **One inherited comment was wrong and is corrected.** The TypeScript port
  spells its word boundaries out because *JavaScript's* `\b` is ASCII-only. Ruby's
  is Unicode-aware and already agrees with Python; carrying the rationale across
  unchecked would have stated something false about Ruby. The lookarounds stay —
  they are the form the shared spec pins — but as belt-and-braces, and the test
  says which.

### The gem's push path is proven without publishing a vicary that cannot redact

* **`release-gem.yml` takes a `push_probe` input.** It runs the same workflow
  file, in the same environment, with the same OIDC claims a release uses, mints
  a real short-lived credential, and attempts a `gem push` that rubygems.org's own
  authorization code is *required* to refuse: `Pusher#authorize` clears a
  trusted-publisher key by exactly two routes, and a gem name no pending publisher
  claims closes both. The probe asserts the refusal is the authenticated one (403
  at the ownership check) rather than the unauthenticated one (401), so a
  misconfigured publisher cannot read as a pass.
* **It cannot publish by accident for three independent reasons**: the server
  refuses it, the probe gem is built under `RUNNER_TEMP` where the publish step's
  `vicary-*.gem` glob cannot reach it, and the publish steps are skipped outright
  in probe mode.
* **The conformance publish gate left workflow shell for
  `scripts/release_gate.rb`**, reading the scoreboard object the harness returns
  instead of scraping a column out of a human-readable report — and
  `test/release_test.rb` now drives its *allow* branch, which no run in this
  repository had ever reached. A gate that has only ever refused is
  indistinguishable from one that is stuck shut.
* **The post-push registry check left it too**, for `scripts/registry_serves.rb`,
  so "the version is not published yet" and "I could not read the registry" stop
  being the same output. The shell it replaces ended in `|| true`.
* **The `v*` tag trigger is restored in `release-gem.yml`**, the deal its own
  comment described: it comes back in the commit that raises the ratchet to 36 of
  36. The gate is unchanged; the number moved.

### TypeScript measures five of the nine gates, and the tag trigger comes back

* **`gates.ts` measures every gate needing no operator-supplied data** — held-out
  recall, KEEP precision, round-trip, unaccounted violations, asset entries — and
  all five hold. Recovered from the port's own output by chunk alignment, not read
  out of the golden: the spec already carries `aligns` and `mapping`, and quoting
  those back would make the port's gate report a restatement of Python's.
* **Reconciled against `pytest tests/test_gates.py -s`**, counts and all: 16/16
  held-out spans, 21/21 KEEP spans, 52/52 frames round-tripping, 0 unaccounted
  violations, 360,793 asset entries. 100% of a wrong denominator is still 100%,
  so the denominators are asserted too.
* **The four gates that need a corpus or the Census file stay `NOT MEASURED`**,
  printed per gate and asserted to stay that way. Five of nine held is a different
  statement from nine of nine, and a gate quietly reduced out of the denominator
  is how the second becomes the first without anybody deciding it should.
* **The accepted-violation list is an exact set, and its staleness check came
  with it.** `leak NAME:Robinson` is the one documented, deliberately unpaid cost.
  A violation that stops occurring fails the build too, so a fixed defect cannot
  leave an exemption behind to shelter the next defect of the same shape.
* **The `v*` tag trigger is restored in `release-npm.yml`.** It was removed
  because the conformance gate would have refused on every tag while the port was
  unfinished, making every Python release show a permanent red check. The gate
  itself is unchanged and still reads the number off the scoreboard.
* Found while writing this: the `wrong-type` check compared `expect` to a
  re-braced kind, so **every correctly-typed span was a violation** — 41 of them,
  each reading "expected {NAME} got {NAME}". Visible only because the gate was
  measured rather than assumed to agree.

### The TypeScript detector is wired end to end: 8 of 36 to 36 of 36

* **`redact` calls the whole detector** — the identity and structured pass, then
  candidate generation, through **one minter**, in that order. Generation runs
  last for the reference's reason: a broad capitalised-word match run early
  swallows the first token of an address or the local part of an email, and a name
  half-eaten by another pattern leaks the remainder. The arm is
  `local-gazetteer-lowercase`, which is the arm the golden was produced by.
* **36 of 36 masking-required frames, 52 of 52 overall, numbering included.**
  The ratchet moves to 36 and the completeness item stops being a `todo`: it was
  reported-not-failing while the gap was open, and a closed gap should fail the
  build if it reopens rather than go back to being a note.
* **Verified against Python on text the fixture does not contain** — 15 held-out
  compositions (lowercase writing, all-caps, a heading, a work title beside a
  relative, a hometown chain, a shared-surname pair), **15 of 15 byte-identical**.
  52 matching strings can be fitted; held-out agreement is the claim the product
  actually makes.
* **Two structured tests now name the level they were pinning.** "a Grace period"
  and "Okonkwoville" survive the identity arm and are masked by the shippable
  one — both readings are the reference's, checked against Python at both levels.
  They asserted the identity arm's behaviour through a call that no longer runs
  only the identity arm, so they now pass `names: NAMES_IDENTITY` and assert the
  full arm's output alongside, rather than being loosened.
* **Two contracts the byte comparison only covered implicitly are now asserted
  directly**: placeholders appear in the reference's order (which is order of
  first appearance *in the text*, not mint order — generation discovers right to
  left, so `{NAME_2}` legitimately precedes `{NAME_1}`), and every frame's
  restore map reproduces the original bytes.
* **`redact`, `redactWithReport` and `restore` are exported from `index.ts`**,
  with the three detection levels and the `Identity` type. They were deliberately
  absent while the detector was partial. The nine gates remain **unmeasured** by
  this port, four of them needing data no package ships, and the scoreboard still
  prints `NOT MEASURED` beside each — a green suite means the frames match, not
  that the gate set is clear.

### Masking ports, and four more rules that nothing was checking

* **`maskCandidates` is in TypeScript** — the four keep gates in order, the
  right-to-left rewrite, and the minter. **The ratchet does not move: it is still
  8 of 36.** Nothing calls it yet; `redact.ts` still raises. Wiring the identity
  pass, the structured pass and the gazetteer-backed notability oracle is what
  turns this into a score, and that is the next step, not this one.
* **Four masking arms ship as spec data** — full wiring, unnumbered, with a
  `keep` set, and without a notability oracle. This is the only place placeholder
  *numbers* appear in the spec, and numbering is what ports diverge on first
  because indices follow discovery order. `possessive` pins it end to end: three
  spans, minted right to left, so the leftmost is `{NAME_3}`.
* **Four more rules had nothing separating them from their absence**, found the
  same way: the precedence table's keep verdict (no corpus text contained a
  landmark), the overridable-tier check on the relation refusal, the
  corroboration keep, and the sentence-level refusal of it. Four corpus entries
  and an `iconic_short` tier in the stub oracle close them. Controls now catch
  **24 of 24**.

### Candidate generation ports, and six rules that nothing was checking

* **`findCandidates` and the surname machinery are in TypeScript** — both routes
  (the capitalised scan and the lowercase given-name seed), the title protection
  and its relation refusal, and `surnameTokens` / `bareSurnameKey` /
  `surnameForms` / `corroboratedSurnames` / `establishedNameTokens`. No Python
  behaviour changed; `frames.json` and `gates.json` are byte-identical, and every
  primitive case that existed before this change still holds its old value.
* **`primitives.json` gained a `name_forms` input group and eight sections**, the
  largest of which are three `find_candidates` arms: without oracles, with them,
  and with a given-name oracle that accepts everything. Three arms because they
  are three different detectors — without oracles the corroboration guard is
  unreachable by construction, and a port that wired the oracles into one arm
  only would pass the other.
* **`corroboration.tier` ships as data.** A port comparing against `"person"`
  instead of `"full_name"` corroborates nothing and passes every behavioural
  case, because the spans involved were being masked either way — the failure is
  invisible in output and visible only against the declared string.
* **Six rules had no input in the corpus that separated them from their absence.**
  Found by mutating each one in the TypeScript source and confirming the suite
  still went green: the determiner guard, the two-token minimum, the particle
  trim, the trailing-apostrophe trim, the two-route overlap guard, and the
  title-relation refusal. Seven corpus entries close them, each named for the
  rule it pins. The controls now catch 15 of 15; `typescript/scripts/negative-control.mjs`
  is the harness, and it asserts each mutation actually landed before it reads a
  verdict — a control whose edit matched nothing is reported as a broken control,
  not as a pass.
* **The overlap guard is unreachable under any realistic oracle, and stays.** It
  drops a lowercase span that collides with one the capitalised route already
  claimed. A capitalised span contains a lowercase token in exactly two places: a
  name particle, which cannot reach the two-token minimum because a span may not
  end on one, and the `s` a possessive leaves dangling after an apostrophe. So it
  fires only where the given-name tier seeds on `s`, which the shipped tier does
  not — the tier is data, the next one may, and `possessive_lowercase_habit` is
  the case that makes deleting the guard fail. It needs both halves: the
  possessive, and a `lowercase` habit to drop the corroboration requirement that
  would otherwise reject the seed first.

### The relation override ports, with its word lists as spec data

* **The four relation predicates are in TypeScript**: the window scan for a bare
  surname the document established (`namesSomeoneInTheWritersLife`), the strict
  attached-phrase test for a title-tier hit (`namesSomeoneTheWriterKnows`), and
  the two that read the writer's own capitals inside a relation-led span. No
  Python behaviour changed — this is a port, and it was diffed against the
  reference over 45 span cases before a line of it was checked in.
* **`primitives.json` gained a `span_cases` input group and four sections**, plus
  a `relation` block carrying the 37 cues, 13 proximity phrases, 6 first-person
  pronouns and the three overridable tiers, and `relation_window` in `constants`.
  `overridable_tiers` is the policy half: a port that let the override reach
  `place` would redact a town the tier deliberately keeps, and no behavioural case
  would say so.
* **The 90-character window is pinned behaviourally, not just declared.** A cue 80
  characters after the span is inside it; the same cue 99 characters after it is
  not. Both are cases, so a port with an 80-character window fails rather than
  passing on a corpus that never separates the two.
* **A second documentation defect, measured and not fixed.** The modifier class in
  the relation patterns is commented "lower-case only, so a capitalised name
  cannot be swallowed as a modifier". It does no such thing: every caller folds
  its window with `.lower()` before matching, so no capital ever reaches the
  `[a-z]` class. "My Old soccer coach Deshawn" is accepted exactly as the
  lower-case spelling is. Identical in both languages, so the comment is wrong and
  the behaviour is not; pinned by a test rather than changed during a port.

### The lookups are the build mechanism's, not the Python package's

* **`vicary.build` moved out of the package to `asset/vicary_build/`.** It fetched
  from Wikidata, the US Census and SSA — so `pip install vicary` shipped a SPARQL
  client to every host running a library whose whole claim is "no model, no network,
  no per-request cost".
* **The tracked gazetteer moved to `asset/data/`, and every front door now vendors a
  gitignored copy** — Python included. It used to be the tracked original that the
  npm package and the gem copied from, which made one of three peers the structural
  owner of the shared input. Parity between peers is checkable; parity with an
  original is just copying.
* **`vicary-assets fetch` is gone; `show` and `verify` remain.** Writing the manifest
  moved to the build mechanism with everything else. The manifest is how three
  implementations prove they loaded the same bytes, and a library that could rewrite
  it could paper over a mismatch it caused — the check would become a check of the
  library against itself.
* **`VICARY_BUILD_SSA_NAMES_ZIP` is no longer declared by `vicary.config`.** Same
  variable, same name, read by the build tool. It was on the surface a host
  integrating the library reads, where it looked like something that mattered at
  request time.
* **A manifest refresh no longer raises an untouched asset's `min_package_version`.**
  It is preserved unless that asset was rebuilt or its format changed. Stamping the
  current version into every entry would make older installs refuse a file they can
  read perfectly well — the check failing closed against its own user, for a reason
  no error message explains. Recorded upstream sources are preserved for the same
  reason.

### A capital the writer chose now vouches for its own possessive

* **"Terrence's" at a sentence start is no longer suppressed** when the document
  capitalises "Terrence" elsewhere. The sentence-initial guard asks two channels
  whether anything beyond the capital vouches for a span, and one of them — the
  document's own mid-sentence capitalisation — had no possessive normalisation,
  so it could not vouch for its own possessive.
* **Nothing changed on the shipped gazetteer arm**, which is why this went
  unnoticed: `is_common_given_name` folds possessives itself, so the *other*
  channel was covering the gap. The fold now happens in the guard, so the rule no
  longer depends on an oracle contract that was never written down — a host
  passing a plain set membership function as `given_name` got the suppression.
* Strictly additive: it can only reduce suppression, never increase it. A word
  with no clitic to remove is unaffected, so the guard's purpose is intact.

### The stoplist is data in all three languages, not a literal in each

* **The 421-word stoplist is now `asset/lexicon/stop_words.txt`**, vendored beside
  the gazetteer and read at import. A word list transliterated by hand into a second
  language diverges silently, and the divergence shows up as prose corruption in one
  language and not the others — which no parity check on *masked output* would catch,
  because a missing stop word changes what gets masked in essays nobody put in a
  fixture.
* **Each lexicon declares its own distinct-word count, and every reader asserts it.**
  Same discipline as the gazetteer's per-tier counts, for the same reason running the
  other way: a short read here makes the redactor *more* aggressive, which looks
  privacy-safe and passes any check that only asks whether something was masked.
* **This is why the version had to move.** The published 0.1.1 wheel carries no
  lexicon, and shipping different package contents under one number is not a thing
  to do quietly.

### Two identity patterns that silently matched nothing now match

* **A curly possessive is masked with the name.** `_word_pattern`'s second
  alternative was a byte-for-byte repeat of its first rather than the curly form it
  resembled, so `Marguerite’s` — what a word processor actually emits — missed while
  `Marguerite's` worked.
* **A surname ending in punctuation is masked.** The pattern closed with `\b`, which
  cannot hold after a `)` or a `.`, so a caller passing suffixed roster data
  (`O'Brien (Jr.)`) got no masking for it at all while looking configured. The
  boundary is now asserted only on the side that has a word character to assert
  against, which is identical to `\b` for every literal that does not end in
  punctuation.
* **The plural-family tail (`s'`) works.** It was in the pattern all along and the
  old trailing boundary made it unreachable.
* No golden byte moved: no conformance frame contains a curly apostrophe or a
  punctuation-edged identity literal. Each fix was verified red against the defect it
  covers, and the fourth guard was verified red against the naive version of the
  second fix.

### One detector, one number, asserted

* **`VERSION` at the repository root is the single source.** `asset/tests/test_version.py`
  fails when any of the four manifests disagrees with it, and the release workflow
  runs it. The per-package tag checks catch a mistyped tag; they cannot catch three
  packages that each agree with their own tag and disagree with each other.

## 0.1.1 — 2026-08-10

Nothing about the detector changed. This release exists because a package's
rendered project page is built from the README at build time, so a corrected
README does not reach PyPI without a version to carry it.

### The package no longer implies it holds a licence to the corpus it was measured against

* **The README's "the licensed ASAP essay corpus" is gone.** It read as a licence
  *this project holds and passes on*, which is not true of anything here. It now
  says no essay corpus ships or is redistributed, that the operator supplies data
  they are entitled to use, and that this project makes no claim to those terms
  and grants no rights in that data. The same phrasing was corrected in `ci.yml`.
* **This was a wording defect, not a packaging one.** Verified against the
  published artifacts rather than the build config: the 0.1.0 sdist and wheel
  carry exactly two data files, `vicary/data/notability.txt.gz` and
  `MANIFEST.json`. No essay text, no excerpt, no TSV. The only route to a corpus
  is an environment variable pointing at a file the operator supplies.
* Measurement claims derived from a corpus ("25 essays", "2,108 `@PERSON`
  tokens") stay, as does the *pattern* the corpus authors' own anonymization
  tokens match. Statistics about a corpus are not redistribution of it.

### The eval corpus is found by scanning, so no dataset's filename is baked in

* **`VICARY_EVAL_CORPUS_DIR` no longer joins a fixed `training_set_rel3.tsv`.**
  One corpus's filename in a general-purpose library both tied the library to
  that corpus and named a specific third-party dataset in a published artifact.
  The default is now the generic `corpus.tsv`, and a directory holding a single
  `.tsv` under any other name resolves too — so a directory laid out under 0.1.0
  keeps working with nothing renamed.
* **An ambiguous directory raises `CorpusDirectoryError` rather than resolving to
  the empty string.** Every caller reads `""` as "no corpus configured, skip this
  gate", so a mis-named corpus would have printed a gate pass on no data. Zero
  TSVs and two TSVs are operator errors and now say so, naming both the variable
  and the escape hatch. The eval CLI surfaces that message from its `--tsv`
  check instead of as a traceback out of an argparse default.
* Gates re-measured after the change: **9 of 9 measured, 9 of 9 PASS**, every
  value unchanged, 720 tests green.

## 0.1.0 — 2026-08-10

First published release. The three sections below are the cuts it is made of, in
reverse order; the last of them is the extraction that started the package.

### The given-name tier is built from US births, not from famous people's first names

* **`given` is rebuilt from the SSA baby-names archive** — per-name US birth
  counts, 1880–2025, all years and both sexes, at **1,800 births or more**. Asset
  format 4 → 5. It was the first tokens of the `full` tier at ≥3 distinct bearers,
  which answers *"was a famous person called this"*, not *"is this a name a US
  child is given"*.
* **This was an equity defect, not a tuning miss.** Measured on the old tier:
  `Deshawn`, `Ayaan` and `Meisha` absent while `Marguerite`, `Terrence`, `Priya`,
  `Marisol` and `Vinny` were present — **the misses skewed toward Black and South
  Asian given names**. `Deshawn` was the arm's one visible-recall miss and a
  standing entry in `ACCEPTED_VIOLATIONS`.
* **It improves both legs at once**, which is rare enough to be worth stating
  plainly: visible recall **96.2% → 100.0%** (the `Deshawn` leak is gone, and
  `intersect-school-and-friend` goes 50% → 100%) *and* over-firing **0.72 → 0.60**
  spans/essay. The over-firing ceiling follows the measurement down to 0.60 by the
  rule already written on it — a ceiling above the measured value is a comment,
  not a gate.
* **Why births succeed where the bearer floor could not.** The bearer floor was
  measured first and rejected: floor 2 bought nothing, floor 1 reached `Deshawn`
  but admitted 39,830 tokens for +7.9% over-firing and turned "Breeds I Like" into
  "Breeds I {NAME}", because some notable label leads with "Like". Births are a
  **dense** signal where a bearer count is sparse — `Like`, `Pride` and `Recess`
  have **no birth record at any threshold** — so the tier is larger where it
  matters and cleaner where it hurt. That frame is now clean.
* **1,800 is the measured knee, not a round number.** Over-firing by floor against
  the 25-essay gate corpus: 1,000 → 0.80, 1,200 → 0.76, 1,400 → 0.76, 1,600 →
  0.72, **1,800 → 0.60**, 2,000 → 0.60. 1,600 would pass the old ceiling with zero
  headroom; 1,800 buys 0.12 spans/essay for 635 of the rarest names in the band.
  All-years rather than a recent window because the tail is where the misses live.
* **What it still does not reach, named:** `Meisha`, at 1,048 births since 1880.
  Reaching her needs a floor ≤1,048, which measures 0.80 spans/essay and fails the
  over-firing gate. The trade is left unmade rather than unnoticed.
* **The instrument is not blind to this path.** Rebuilding at floor 1 (tier
  105,966) drove over-firing to 2.24, KEEP precision to 95% and added a new
  invariant violation — three gates red — so the 0.60 is a measurement, not a
  harness that cannot see the change.
* `ACCEPTED_VIOLATIONS` is down to **one**: `Robinson`, the documented
  deliberately-unpaid cost. Both removals this week were surfaced by
  `test_the_accepted_violations_still_happen` going red.
* **The build now requires the archive and refuses to guess.** `ssa.gov` returns
  an Akamai HTTP 403 to some networks on every path including the site root
  (verified with a plain UA, a browser UA, a cookie jar and a referer, while
  `www2.census.gov` and `query.wikidata.org` answered normally in the same run),
  so there is no download path to fall back on. Point
  `VICARY_BUILD_SSA_NAMES_ZIP` at a hand-downloaded `names.zip`. A missing archive
  raises; a truncated parse raises — a short read here makes the redactor *less*
  aggressive, quietly restoring the leak the tier exists to close.
* On the 27 un-scrubbed student documents the masked-span count moves 114 → 108.
  Reported as a configuration delta only: real names are present in that corpus,
  so an absolute count there is not an over-firing figure.

### A redacted town is typed `{LOCATION}`, not `{NAME}`

* **New `settlement` tier, and the asset format goes 3 → 4.** `Akron` was always
  masked — this was never a leak — but it was masked `{NAME}`, so a host that
  reads the placeholder back writes "great job describing your trip to {NAME}".
  It needed a rebuild rather than a patch: settlements are excluded from the
  `place` tier *in SPARQL* (`FILTER NOT EXISTS ... Q486972`), because a town name
  is where a student lives, so the signal needed to type it was never fetched.
  The new tier inverts that filter to `EXISTS` and keeps the school exclusion.
  23,277 entries.
* **It is the second tier that grants no keep**, after `given`, and the one way
  this change could have done damage was to become a keep by accident:
  `notability()`'s contract is `verdict != NOT_NOTABLE => KEEP`, so a settlement
  answered there would turn every student's hometown into a keep — readmitting
  through the back door exactly the PII the place tier's exclusion removes. It
  gets its own boolean oracle instead, and a test that goes red if anyone wires
  it into `notability()` (verified red, not assumed).
* **Half of American town names are somebody's surname**, because the towns were
  named after the people. Typing on tier membership alone would relabel a
  classmate named Jackson as `{LOCATION}` — the Akron defect with the sign
  flipped, on a commoner population and pointing the worse way. Settlements that
  are also common given names or surnames borne by 10,000+ Americans are dropped
  and fall back to `{NAME}`: `Jackson`, `Austin`, `Houston`, `Cleveland`,
  `Madison`, `Brooklyn`, `Aurora`. `Akron`, `Westfield`, `Springfield` and
  `Phoenix` survive.
* **The sitelink floor is 30, and the higher floor was measured and rejected.**
  `Akron` carries 94 sitelinks, so the place tier's single-token floor of 150
  misses it and 90 clears it by four against a moving upstream. The floor was
  then tested as a *purity* lever and provably does not separate: measured
  against `/usr/share/dict/words`, ordinary English words are 7.2% of
  single-token keys at floor 30 and **16.5% at 150**, because what a high floor
  keeps is world capitals that are themselves dictionary words (Rome, Moscow,
  Berlin, Venice). A higher floor shrinks the tier and enriches the noise.
* **All 9 gates re-measured, all PASS, every number unchanged** — held-out recall
  100% on both arms, KEEP precision 100%, round-trip 100%, over-firing 0.72
  spans/essay, Census exposure 1.20%, latency p95 3.50 ms, asset entries 360,790.
  That identity is the predicted result rather than a blind instrument: typing
  relabels a span and moves none, and the same harness *did* move where it should
  — the `Akron` `wrong-type` violation is gone, and
  `test_the_accepted_violations_still_happen` is what surfaced it.
* **`ACCEPTED_VIOLATIONS` is down to two**, both leaks. `Deshawn` is the
  remaining one worth losing.
* Measured on the 27 un-scrubbed student documents: 114 masked spans in both arms
  — the no-op-on-verdicts property at corpus scale — and 4 retyped, all of them
  `Christmas` in one document. `Christmas` is a US town at exactly the floor. Both
  labels are wrong and the span is a pre-existing over-fire, so the tier relabels
  a defect rather than creating one; no ordinary-word subtraction was added,
  because it would buy nothing on a span that is wrong either way and would cost
  the student who really is from Normal, Illinois.
* `vicary-assets fetch` now forwards `--cache-dir` to the builder. Without it the
  documented rebuild command re-ran every SPARQL query against donated
  infrastructure on each attempt, which made a threshold sweep — the reason the
  cache exists — cost ~30 queries per step instead of one fetch and N offline
  re-folds.

### The capitalisation verdict is four states, not two booleans

* `document_capitalises_names` and `writes_without_standard_capitals` are replaced
  by `CapitalisationHabit` and `capitalisation_habit()`. The two booleans
  **contradicted each other on 7 of 27 un-scrubbed student documents**, so which
  treatment a document got depended on which predicate a call site read. All three
  consumers now read one verdict computed once.
* `INCONSISTENT` names the writer who marks some proper nouns and drops others —
  4 of the 27. It gets no document-level answer on purpose and falls through to
  per-token evidence (`_mid_sentence_capitals`), which is the right granularity and
  already existed.
* The presence counter now excludes heading spans, bringing it into line with the
  per-token channel the inconsistent band falls through to; the two were reading
  the same evidence through different rules. Measured effect on the 27 documents:
  **none** — it lowers five counts (`horses` 52 → 27 is the largest) and none
  crosses the floor. A precision repair, not a fix.
* Two rate proposals were measured. On the **presence** side a rate is rejected:
  it re-orders the deciding band instead of separating it, and inverts the two
  closest cases (`141-693` "Powerball" ×2 at 0.58/1k is a real capitaliser;
  `141-433` "The"/"There" at 1.75/1k are artefacts). On the **absence** side it is
  kept only above the floor, where a presence signal exists to weigh it against —
  applied below the floor it demoted a genuine lower-case-writing carrier essay at
  a 1.7% drop rate and **leaked a held-out name** (28/28 → 27/28) to buy one span
  of over-firing.
* **New gate: held-out recall in a carrier essay.** The existing recall gate reads
  the frames arm only, where each frame is scored alone; every document-level
  signal the detector weighs sees a different input once a frame is injected into
  3,000 characters of real prose. The rejected rate variant above scored 15/15 on
  frames and 27/28 in carriers and **printed 8/8 PASS**. That failing case is now a
  gate, and it goes red on the variant while every other gate stays green.
* Every other gate is unchanged: held-out recall 100% on both the frames and
  essay-carrier arms, KEEP precision 100%, round-trip 100%, over-firing 0.72
  spans/essay, Census exposure 1.20%, 114 spans on the 27-document student-prose
  leg. The instrument is not blind to this path — the rejected variant above moved
  held-out recall and over-firing in the same harness.

### Release readiness

* **CI exists.** `test_gates.py` has always said "a test is something CI runs
  whether anybody remembers or not"; nothing ran them but a human at a terminal.
  `.github/workflows/ci.yml` runs lint, mypy, the unit tests, the gates and
  `vicary-assets verify` on 3.11 and 3.13, plus a packaging job that builds,
  `twine check`s, and **asserts the wheel actually contains the gazetteer** — an
  artifact that builds cleanly and ships no asset loads empty, which means
  redacting every public figure in every essay.
* MIT licence, `LICENSE` file and `license`/`license-files`/`authors` metadata,
  verified on the built wheel rather than in the source: `License-Expression: MIT`,
  `License-File: LICENSE`.
* `pytest --gate-report` was documented in `test_gates.py` and **does not exist** —
  it errors out. The report is a test that prints under `-s`. Docstring corrected
  rather than the flag added, since the mechanism was already there.
* The README now says why the package is called vicary.

## The 2026-08-06 cut

### Two build defects that made every tier change invisible

* **`vicary-assets fetch` was a silent no-op.** The builder resolved its output
  against its own directory, writing `src/vicary/build/data/notability.txt.gz`,
  which nothing loads — the runtime and `MANIFEST.json` both read
  `src/vicary/data/`. So a full Wikidata rebuild fetched everything, wrote to a
  dead path, rewrote the manifest by checksumming the *old* asset, verified that
  same old asset against its own fresh checksum, and printed a pass. Pinned by
  `test_the_builder_writes_where_the_runtime_reads`.
* **`write_asset` iterated a hardcoded five-tier list.** A tier added to
  `build_tiers` and not to that list built cleanly, reported its count in the
  build log, and read back empty — which for a KEEP tier means redacting
  everything it was built to protect. It now writes every tier the fold
  produced, and the round-trip is asserted against the fold's own output so the
  *next* tier is covered without anybody remembering.
* **The shipped asset's tier counts are now reconciled with the manifest's**, on
  the frozensets a loaded process actually holds rather than on the build log —
  the log said 1,044 demonyms while the running gazetteer held 0. The reader's
  tier names live in one place (`gazetteer.TIER_NAMES`), the parser and the
  dataclass are asserted against it, and `load()` logs every tier rather than a
  hand-written five, which is where an operator would have seen `demonym=0`.

### New `demonym` tier — asset format 2 → 3

* 1,044 English demonyms from Wikidata `P1549`. Closes `Cuban` in "your Cuban
  heritage", 1 of the 3 residual over-fires on real generated feedback.
* Subtracted by the `given` tier and by any surname with 10,000+ US bearers —
  2.5× stricter than the short tier, because a demonym is the only keep with no
  notability evidence behind it. `Horner` forced the number: a demonym of Horn
  and 23,881 Americans' surname.
* Reachable by the first-person relation override, so "my coach Cornish"
  redacts while "my Cuban heritage" keeps.

### Carrying a keep across the two passes

* `redact_inbound` records the bare tokens of the notable full names it kept;
  `redact_outbound` treats them as topical. Closes `Narciso` and `Narciso's`,
  the other 2 residual over-fires — a case no lookup can reach, because the
  `given` tier is built *from* the first tokens of the full tier.
* Sound because outbound text is generated from inbound-redacted input, so a
  masked name never reached the model. Off with `carry_notable_keeps=False` for
  a host whose outbound text has another source.
* Refused for the student's own name and for a token a second, private full name
  in the same essay also claims.
* A keep now folds the possessive, so a `keep` entry for "Narciso" also covers
  "Narciso's". This reaches the `prompt_context` leg too.

### Census exposure 1.47% → 1.20%

* `PLACE_MIN_SITELINKS_SINGLE_TOKEN` 100 → 150, with held-out figure recall
  **unchanged** at 60.3% and Washington/Delaware/Jordan all surviving.
* The obvious lever was measured and rejected: reaching 1.4% via the short
  tier's Census bar costs **Poe, Milton, Swift, Dahl and Thurman**, and leaves
  `saavedra` and `hathaway` — the entries the regression was attributed to —
  untouched, because they sit below the cut. The place tier had never been
  Census-examined at all.
* The 100–149 band is mostly *foreign* first-level subdivisions arriving through
  `Q10864048`: French départements, Spanish and Italian provinces, Brazilian and
  Mexican states. A Spanish province is named for its capital, so the
  administrative reading readmits the settlement names the settlement exclusion
  removes. Cost: bare `Auschwitz`, `Alsace`, `Burgundy`, `Bohemia`, `Anatolia`.
* The Census control now counts the demonym tier (0.025pp) — a keep on a bare
  token is exactly what it measures, and omitting it would make a new tier look
  free. Gate ceiling 1.5 → 1.25.

### Build tooling

* `--cache-dir` persists the raw SPARQL rows, so a threshold sweep re-folds them
  offline instead of re-issuing ~30 queries per candidate value against donated
  infrastructure. Delete the directory after changing a query.
* `vicary.eval.overfire.measure` takes `sources=`, running the two-leg shape a
  host runs — inbound on the essay, then outbound. Run cold it cannot see a
  cross-pass fix at all, which is the same defect as scoring fields unbatched,
  one leg further out.

## The extraction from the essay-scoring pipeline

The code previously lived inside an essay-scoring pipeline; extracting it changed
the following, and nothing else.

### Configuration

* Every environment variable moved from the host application's `GRADER_*`
  namespace into `VICARY_*`. **The old names are still read**, at lower
  precedence, each logging a one-time deprecation warning naming its
  replacement. `ENVIRONMENT` is unchanged and not deprecated — it is a host
  convention, not a name this library owns.
* All resolution now lives in `vicary.config`, with one precedence order tested
  in one place.
* The originating deployment's own environment name, which shipped as a literal,
  no longer counts as a production environment name. The generic `prod` /
  `production` / `live` still do, and a host registers its own with
  `config.add_production_alias()`.
* `guardrail` mode no longer falls back to a hardcoded AWS region. Set
  `VICARY_BEDROCK_GUARDRAIL_REGION`, or let boto3's own chain resolve it; an
  unset region raises `NoRegionError` rather than silently querying the wrong
  region, which used to present as a missing Guardrail.

### API

* `PIIRedactor` → `Redactor`; `LocalPIIClassifier` → `LocalNameClassifier`.
* `vicary/__init__.py` exports the small surface a host needs: `Redactor`,
  `StudentIdentity`, `build_redactor_if_enabled`, `redaction_mode`, and the mode
  constants.

### The data asset

* Ships as package data in the wheel rather than reaching deployment through a
  build-time file-copy allowlist. That allowlist had already silently dropped a
  runtime file once, producing an image that built clean and failed at request
  time.
* New `data/MANIFEST.json`: SHA-256, byte count, per-tier counts, cut date,
  upstream sources, and `min_package_version` per asset.
* `load()` now verifies the bundled asset against the manifest and refuses an
  asset that requires a newer release than the installed one. The pre-existing
  format-header and tier-count checks are unchanged.
* New `VICARY_ASSET_PATH` override, accepting a file or a directory. An
  overridden asset is checked against its own header, not the bundled manifest —
  pointing elsewhere is the point of the override.
* New `vicary-assets` CLI: `show`, `verify`, `fetch`.
* The gazetteer builder now identifies itself to Wikidata and the Census as
  `vicary-gazetteer/<version>`, with `USER_AGENT_SUFFIX` for a deployment's own
  contact address.

### Detection

* **Absence of capitals stopped counting as evidence of a writer who drops
  them.** `document_capitalises_names` answers "did this document capitalise its
  proper nouns", and a **no** has two causes that were being treated as one: the
  writer drops capitals — the case the lowercase route exists for — or the text
  contains no proper nouns at all. Only the first should reach the permissive
  path.

  The second is what the outbound pass sees. Its inputs are single feedback
  fields of 108-290 characters, ordinary prose about a student's essay; every one
  scored zero mid-sentence capitals, every one was read as lower-case writing,
  and the route ran with no corroboration required. "tone toward", "line makes",
  "line circles" and "line loops" masked as names in text a student reads. Both
  `tone` and `line` are genuine given names, so the seed was legitimate and the
  absent guard was the whole defect.

  New `writes_without_standard_capitals` requires a *positive* tell — a sentence
  opening in lower case, or a bare lower-case "i" — rather than inferring one
  from silence. Held-out recall, KEEP precision and the leak count are all
  unchanged; over-firing falls **1.20 → 0.72 spans/essay inbound** and **0.57 →
  0.29 spans/response outbound**.

* **An opening quote now counts as a sentence start.** The pattern knew about a
  *closing* quote after terminal punctuation and not about an opening one, so
  "vivid words like 'Giggles filled the school'" read that capital as the
  writer's choice. Quoting the student's own words is how feedback refers to
  them. Outbound over-firing **0.29 → 0.21**.

  Two things this surfaced, both older than it. `_corroborated` stripped
  punctuation for the capitalisation channel and not before the given-name tier,
  so `Terrence'` — the candidate pattern keeps the apostrophe so O'Brien survives
  — was asked about verbatim and answered no; a quoted first name would have
  started leaking. And a trailing apostrophe rode into the masked span, so
  masking ate the closing quote: "Words like '{NAME_1} stand out". Both fixed,
  both pinned.

* **The over-fire harness now batches the way the host does.** `measure()` took
  groups, documented at length why grouping matters, and then masked each field
  separately — the one shape no host runs. `redact_outbound_batch` joins every
  field of a response into ONE pass, and two document-level signals are computed
  over whatever arrives, so a 191-character field and the 1,656-character
  response it belongs to are different inputs. Pinned by a test where it changes
  the answer: "Narciso Rodriguez" in one field and a bare "Rodriguez" in the next
  over-fires scored apart and does not scored together.

* **The two directions are separately configurable**, via
  `VICARY_NAME_DETECTION_OUTBOUND`. Unset it inherits the inbound level, so this
  changes no deployment; set, it builds a second classifier for the outbound
  pass only. It exists because the error costs are not the same and one setting
  could not express both: inbound an over-redaction is a placeholder in text the
  model already reads as `@PERSON1`, outbound it is a hole in feedback a student
  reads, and the residual there is real at 0.57 spans per response.

  Two things this needed that were not obvious. The direction was **not
  represented anywhere in the call** — both passes invoke `ApplyGuardrail` with
  `source="OUTPUT"`, because `ANONYMIZE` only masks there — so `_apply` takes an
  explicit `outbound` flag rather than inferring one. And equal levels share one
  classifier rather than building two identical ones, because two objects that
  agree today can stop agreeing and the outbound number would then move with
  nothing in the configuration to explain it.

  Which level outbound *should* be is a measurement, not a default, and it has
  not been made yet. The dial had to exist before the question could be asked.

* A **first-person relation attached to a name now overrides a `title`-tier
  keep**: "My neighbor Alice Adams" redacts, where before the 1921 novel of that
  name kept it. 578 keys in the tier are a common given name beside an ordinary
  US surname, and every one of them sheltered whichever private individual
  carries it — measured through `redact_inbound`, 578 of 578 leaked. The
  override recovers 508; the remaining 70 resolve in another tier and are
  untouched by it.

  No threshold could have fixed this. The distributions interleave — Atticus
  Finch is at 17 sitelinks, the hole "Alice Adams" at 24 — so raising the floor
  to 25 costs Atticus Finch, Peter Parker and Clark Kent to close a quarter of
  the holes. The evidence had to come from the sentence instead.

  The rule is deliberately stricter than the bare-surname refusal it is modelled
  on: it requires a **first-person** relation, and requires it **attached** to
  the name. Reusing the surname rule unchanged refuses six of the seven
  curriculum characters, because characters are described *by* their relations
  ("Atticus Finch is the kind of father who…"); dropping attachment redacts a
  book whenever a student says who they read it with ("I read Harry Potter with
  my little brother"). Both frames are in the fixture, held out, in both
  directions.

  On 27 un-scrubbed student documents the override fires 0 times, which is why
  over-firing on prose is unchanged at 1.20 — an explained equality, not a
  coincidental one. Off with `title_relation_refusal=False`, or
  `vicary-eval --no-title-relation-refusal` for the control arm.

* **The override reaches the `full` tier too, on a measurement that retracts the
  reason it did not.** The scoping was justified in writing by "overriding
  `full` would also redact 'my hero Abraham Lincoln'". It does not: *hero*,
  *muse*, *inspiration* and *role model* are admiration invocations, they attach
  to a public figure as readily as to a relative, and none of them is a relation
  cue. The hole they were protecting is **57× the title one** — 33,682
  name-shaped keys in `full`, of which 33,269 shelter a private individual, and
  the override recovers 33,182. Of the eight probes that would have to break for
  the original claim to hold, seven are unchanged and the eighth is "my coach
  Steve Kerr", which redacts and should. Pinned by two held-out frames,
  `private-person-shadowed-by-a-real-public-figure` and
  `admiration-invocation-before-a-public-figure`, both verified to flip when the
  override is scoped back to `title`.

* **A relation-led title no longer shelters an actual relative.** 41 keys lead
  with a first-person relation — "My Cousin Vinny", "My Sister Eileen", "My
  Brother Nikhil" — and the tier matches case-insensitively, so "My cousin Vinny
  Delgado came over that summer" matched the 1992 film, blocked generation across
  the whole phrase, and shipped the name. That is the fixture's commonest frame
  (`kinship-possessive`) wearing a film's title. The writer supplies the
  discriminator: a title is title-cased and a sentence about a relative is not,
  the same orthographic evidence the heading rule reads, gated on the document
  capitalising at all. Measured across all 41: **41/41 survive when written as a
  title, 31/31 of the name-bearing ones are removed when written as prose.**

* `Span.redacted_by` distinguishes a span the gazetteer has never heard of
  (`"absence"`) from one it vouches for that the sentence overrides
  (`"context"`). Both directions are asserted: an `"absence"` span that resolves
  notable is an asset defect, and a `"context"` span that does *not* is a frame
  measuring nothing.

### Measurement

* The eval harness ships with the library. Its gates are pytest tests in
  `tests/test_gates.py`, marked `gates`, each printing its measured number.
* Corpus-dependent gates skip visibly when no corpus is configured, rather than
  passing on no data.
* `vicary-eval` no longer defaults to paths inside the former host repository.
* Deleted: the test asserting a host repository's build recipe copied the asset.
  Packaging removes that hazard; a test that reaches into another repo's build
  script cannot survive extraction. Replaced by a test that every file in
  `data/` is matched by a `package-data` glob, which is the equivalent hazard
  packaging *does* have.

### Gates, and one correction they forced

Measured at fixture `2026-08-06.3`, the arm that clears the gate set is
`local-gazetteer-lowercase` — candidate generation, the notability oracle, **and**
the case-insensitive route. The route was previously left off by default on the
belief that it cost over-firing. It does not, on this fixture: it *halves* it.

| axis | with the route | without |
|---|---|---|
| held-out recall | 100% | 90.5% |
| KEEP precision | 100% | 93.5% |
| over-firing on prose | 1.20 spans/essay | 3.36 |

Over-firing is essay-selection dependent — the same code on a different 25 essays
reads 1.16 rather than 1.20 — so the gate pins the selection along with the bar.
