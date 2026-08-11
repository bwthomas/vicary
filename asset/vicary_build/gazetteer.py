"""Build the offline notability gazetteer from Wikidata.

Run this to regenerate ``asset/data/notability.txt.gz``, the one tracked copy all
three front doors vendor from. It is a build-time tool, never imported on any
request path — the runtime side is each front door's own gazetteer module, which
only reads the asset this writes.

    python -m vicary_build fetch            # fetch + write
    python -m vicary_build fetch --stats    # report, write nothing

Why Wikidata, and why by sitelink count
---------------------------------------
The gazetteer answers one question: *is this name a public figure or a public
place, or is it somebody in this student's life?* There is no syntactic answer —
``My cousin Terrence Okonkwo`` and ``My inspiration, Vincent van Gogh`` are the
same sentence shape — so the discriminator has to be a lookup, and the lookup
needs a defensible definition of "public". Sitelink count (how many Wikipedia
language editions carry an article about the entity) is that definition: it is
an external, reproducible measure of cross-cultural notability that nobody on
this project gets to tune per-name.

Three tiers, because one threshold cannot serve both surface forms
-----------------------------------------------------------------
Students write partial names — ``Lincoln``, ``Thoreau``, ``van Gogh`` — and they
also write ``Coach Bramwell`` and ``Mrs. Okonkwo``. Bare surnames are therefore
both the form we most need to resolve *and* the form most likely to collide with
a private person, because a surname is shared by thousands of people. So:

``full``
    Multi-token labels of humans with ``>= FULL_MIN_SITELINKS`` sitelinks. Broad,
    because a two-or-more-token exact match is already strong evidence: the
    student wrote the whole name of somebody who has articles in ten languages.

``short``
    Single-token surface forms (bare surnames, and mononyms like ``Plato``), plus
    particle-led surnames (``van gogh``, ``de gaulle``), derived only from humans
    with ``>= SHORT_MIN_SITELINKS`` sitelinks — an order of magnitude stricter.
    A bare surname resolves notable only when some bearer of it is globally
    iconic.

``place``
    Public geography and landmarks: non-settlement geographical features,
    architectural structures, first-level administrative subdivisions, countries,
    and named memorials / parks / museums. **Human settlements are deliberately
    excluded** — a town name is where a student lives, which is exactly the PII
    we are trying to remove, while a state, a river or a national monument is
    essay subject matter.

The settlement exclusion is the one rule that has to be stated in both
directions, because it decides the fixture's adversarial pair on its own:
``Delaware`` (a U.S. state) is kept, ``Akron`` (a city) is redacted, and they
appear in the same corpus. Named landmarks are re-included even when Wikidata
*also* types them a settlement — Yellowstone National Park is P31
``unincorporated community`` as well as ``national park``, and the park reading
is the one a student essay means.

Transport
---------
QLever (``qlever.cs.uni-freiburg.de``) rather than the official WDQS endpoint.
Not a preference — WDQS times out at 60 s on ``COUNT`` over ``P31 wd:Q5``, which
is the first line of every query here; QLever answers the same query in under
two seconds. Both serve the same Wikidata triples. No credentials, no cost.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from collections.abc import Callable
from datetime import date
from pathlib import Path

from vicary_build import config, lexicon

#: Ordinary words that must not be treated as a work title. The same 421 words the
#: detectors use, read from the language-neutral lexicon rather than imported from
#: one of the three front doors — a build tool that imports one of its own
#: consumers is not shared, whatever directory it sits in.
_TITLE_ORDINARY_WORDS = lexicon.load("stop_words")

#: The version stamped into the User-Agent and the asset's metadata. One number
#: for all three front doors, read from the repository's ``VERSION``.
__version__ = config.version()

#: The asset's filename inside :data:`vicary_build.config.DATA_DIR`.
#:
#: An earlier arrangement resolved this against the *module* directory while the
#: runtime resolved it against the package's ``data/``, and the two disagreed by
#: one level. That was a live silent no-op until 2026-08-06: ``vicary-assets
#: fetch`` spent the whole Wikidata rebuild, wrote the new asset somewhere
#: unreferenced, rewrote the manifest by checksumming the OLD asset, verified that
#: same old asset and printed a pass. A rebuild that changes nothing and reports
#: success is worse than one that fails.
#:
#: There is now exactly one directory in the repository this can mean, which is
#: most of why the asset moved out of the Python package. The tier counts are
#: still asserted against the *loaded* gazetteer by a unit test rather than
#: trusted from the build log — ``test_the_manifest_tier_counts_match_the_loaded_gazetteer``.
ASSET_NAME = "notability.txt.gz"


def default_out() -> Path:
    """Absolute path the build writes when ``--out`` is not given."""
    return config.DATA_DIR / ASSET_NAME

#: On-disk format version. Bump when the tier semantics change, so a stale
#: asset fails loudly rather than answering differently.
#:
#: 3 (2026-08-06): added the ``demonym`` tier. A format-2 asset has no demonym
#: set, so a reader that expected one would silently redact every nationality
#: adjective — answering plausibly rather than erroring, which is the failure
#: this counter exists to prevent.
#:
#: 4 (2026-08-07): added the ``settlement`` tier. A format-3 asset has no
#: settlement set, and the degraded answer is quieter than the demonym one — every
#: town types ``{NAME}`` instead of ``{LOCATION}``, which is not a leak and would
#: never surface as a test failure. Quiet is the argument for the bump, not
#: against it.
#: 5 (2026-08-07): the ``given`` tier changes *population*, not membership. It
#: was the first tokens of notable people's names; it is now US birth counts. A
#: format-4 asset answers the same shape of question with a list that misses
#: Deshawn, Ayaan and Meisha, which is the quietest failure in this file — a
#: recall gap that presents as nothing at all.
ASSET_FORMAT = 5

SPARQL_ENDPOINT = "https://qlever.dev/api/wikidata"

#: Contact string in the User-Agent, per Wikimedia/QLever etiquette. Identifies
#: the tool and its version rather than a person, because this is a checked-in
#: file. A deployment running large rebuilds should append its own contact via
#: :data:`USER_AGENT_SUFFIX` — the endpoints are donated infrastructure and an
#: operator they cannot reach is an operator they can only block.
USER_AGENT = f"vicary-gazetteer/{__version__} (+https://pypi.org/project/vicary/)"

#: Appended to :data:`USER_AGENT` when set, for exactly that purpose.
USER_AGENT_SUFFIX = ""


def user_agent() -> str:
    """The User-Agent this build sends, suffix included."""
    suffix = USER_AGENT_SUFFIX.strip()
    return f"{USER_AGENT} {suffix}" if suffix else USER_AGENT


# --- thresholds -------------------------------------------------------------
# Each is a policy choice about who counts as public. They are recorded in the
# asset's metadata header so any coverage number can be traced to the cut that
# produced it.

#: Multi-token full names. 10 sitelinks ~= "has a Wikipedia article in ten
#: languages", which no private individual does.
FULL_MIN_SITELINKS = 10

#: Single-token / particle-led surface forms. Deliberately ~10x stricter than
#: FULL_MIN_SITELINKS. See the module docstring: a bare surname is the highest
#: collision-risk surface form there is.
#:
#: PROVENANCE OF THIS NUMBER, stated because it matters: 100 was chosen with
#: knowledge of the redact literals in src/vicary/eval/fixture.py. The highest
#: sitelink count reached by any private-name surname in that fixture is 84
#: ("Bell", via Alexander Graham Bell); the lowest reached by a surname the
#: fixture requires us to keep is 145 ("Thoreau"). 100 sits in that gap. That
#: makes it tuned on the visible set, not held out, and the margin is only ~1.7x.
#:
#: THE HELD-OUT TEST HAS NOW BEEN RUN, and it says what the margin implied.
#: Against :mod:`vicary.eval.held_out_figures` — 58 figures a school essay names,
#: chosen gazetteer-blind — the bare surname resolves for **58.6%** of them.
#: Fitzgerald, Hurston, Baldwin, Ellison, Dickinson, Frost, Emerson, Jefferson,
#: Kennedy, Madison, Hamilton, Franklin, Newton, Douglass, Keller, Anthony, Owens,
#: Ali and Armstrong all redact. The fixture reported 100% KEEP precision over the
#: same tier because it exercises exactly two bare surnames, Lincoln and
#: Washington, and both clear.
#:
#: The number is NOT a bug in this constant, which is the important part: a famous
#: person with a common American surname cannot clear this floor and
#: SHORT_MAX_US_SURNAME_POPULATION at the same time, by construction, so no value
#: here fixes it. Raising it loses more figures; lowering it keeps Smith. The
#: recovery has to come from evidence this tier does not have, which is what
#: same-document corroboration is — see
#: :func:`vicary.name_candidates.corroborated_surnames`. 23 of the 24
#: destroyed surnames have their FULL name in the full tier, so a document that
#: writes the first name once recovers them at no threshold cost: measured, 30 of
#: 174 masked spans on 27 real un-scrubbed student essays.
SHORT_MIN_SITELINKS = 100

#: Floor for a *derived* full-name form — first-and-last with the middle dropped
#: (see :func:`first_and_last_form`). Held to the same bar as the short tier
#: rather than to FULL_MIN_SITELINKS, and the reason is the same one: a form
#: nobody attached to the person is our invention, so it needs more evidence
#: behind it than a label Wikidata actually stores. Dropping middle names from
#: every full-tier label unconditionally would manufacture ~32,000 two-token
#: names — "george darwin" out of "George Howard Darwin" — each of which is a
#: plausible private full name and none of which anybody writes. At 100 sitelinks
#: the population is the one whose short form genuinely is how they are written.
FULL_DERIVED_MIN_SITELINKS = 100

#: Second gate on the short tier, and the one that does the real work.
#:
#: Sitelink count alone cannot make the bare-surname tier safe, and this is a
#: structural fact rather than a tuning problem: the surname of a famous person
#: is very often also an ordinary American surname. Measured against the U.S.
#: Census 2010 surname file, a short tier gated on sitelinks *only* resolves
#: "notable" for 16.2% of bare surnames written by a US population — 0.50% of
#: distinct surnames, but the hits are Smith, Johnson, Williams, Brown, King,
#: Lee. No sitelink threshold fixes that, because Adam Smith outranks Thoreau.
#:
#: So the short tier additionally drops any surname borne by more than this many
#: Americans. The argument is a privacy argument, independent of any fixture: a
#: name 25,000 Americans answer to is not safe to treat as iconic when it appears
#: with no first name and no title in a fourteen-year-old's essay. Measured
#: effect: 16.2% -> 0.9% false-positive rate, 211 of 1,357 short entries dropped.
#:
#: What it costs, named: bare "Washington", "Morrison", "Parks" and "King" stop
#: resolving via the short tier. Washington still resolves as a place; the other
#: three still resolve in their full-name forms ("Toni Morrison", "Rosa Parks",
#: "Martin Luther King Jr."), which is how essays overwhelmingly write them.
#: Where it fails, it fails toward over-redaction, which the inbound policy can
#: afford. The binding case is "Lincoln" at 16,477 Census bearers — it survives
#: with a 1.5x margin, and that margin is the reason this number is 25,000
#: rather than 10,000, so treat the choice as fixture-informed too.
SHORT_MAX_US_SURNAME_POPULATION = 25_000

#: Places and landmarks. Higher than FULL because place names are short, common,
#: and reused (there is a Delaware, Ohio as well as a Delaware the state), so a
#: low bar here buys collisions rather than coverage.
PLACE_MIN_SITELINKS = 30

#: Single-token place names, held to the same bar as single-token person names
#: and for the identical reason: a one-word proper noun is the highest-collision
#: surface form there is, whoever it belongs to.
#:
#: This was not a design guess. A unit test asserting the Census exclusion caught
#: "Lee" resolving notable via the *place* tier — some geographic feature named
#: Lee with 38 sitelinks — which showed the place tier is an independent
#: false-positive channel that the short tier's Census subtraction does nothing
#: about. Measured over the Census surname file: single-token places at the
#: 30-sitelink bar expose 7.91% of US surname-bearers; at 100 they expose 0.51%,
#: and 53,521 single-token entries become 698. Delaware (220) and Washington
#: (223) both survive comfortably, so nothing in the fixture pays for it.
#:
#: Deliberately NOT also Census-subtracted: that would drop Washington
#: (177,386 bearers), and Washington-the-state is genuinely public. Judged as a
#: place, a place is allowed to be a common surname; the sitelink bar is the
#: control that keeps that from being a licence.
#:
#: RAISED 100 -> 150 on 2026-08-06, and this is where the Census exposure
#: regression was actually paid back. The 2026-08-05 rebuild took the
#: bare-surname exposure 1.4% -> 1.5%, and the obvious lever — lower the short
#: tier's Census bar, since that is where the regression came from — was measured
#: and rejected: reaching 1.4% that way costs `SHORT_MAX_US_SURNAME_POPULATION`
#: 25,000 -> 20,000, which drops **Poe, Milton, Swift, Dahl and Thurman** while
#: leaving `saavedra` (18,834) and `hathaway` (18,401) — the two entries the
#: regression was attributed to — untouched, because they sit below 20,000. The
#: lever and the diagnosis did not line up.
#:
#: This tier was never Census-examined at all, and it was the cheaper place by
#: every measure taken. MEASURED over the 2026-08-06 cut: 1.495% -> 1.196%
#: overall (place leg 0.580% -> 0.280%), **held-out figure recall unchanged at
#: 60.3%**, and Washington, Delaware and Jordan all survive — the fixture's
#: adversarial place pair is untouched, with a 1.67x margin to 250 where it would
#: break. 407 single-token entries go, 117 of them ordinary American surnames
#: worth 797,163 bearers: `mcdonald`, `guerrero`, `rhodes`, `leon`, `kent`,
#: `lugo`, `burgos`, `hidalgo`, `toledo`.
#:
#: The bulk of the rest is why this is cheap: the 100-149 band is almost entirely
#: FOREIGN first-level subdivisions arriving through ``Q10864048`` — French
#: departements (Aisne, Calvados, Gard, Nord, Rhone), Spanish and Italian
#: provinces (Toledo, Oviedo, Burgos, Agrigento, Campania), Brazilian and Mexican
#: states (Alagoas, Amapa, Campeche). A Spanish province is named for its capital
#: city, so the administrative reading readmits exactly the settlement names the
#: settlement exclusion exists to remove.
#:
#: What it costs, named: bare `Auschwitz`, `Alsace`, `Burgundy`, `Bohemia`,
#: `Anatolia` and `Azores` now over-fire. Multi-token forms are unaffected —
#: they keep the 30-sitelink floor — so "Auschwitz concentration camp" still
#: resolves, and a bare one over-redacts, which is the direction this tier is
#: allowed to fail in.
PLACE_MIN_SITELINKS_SINGLE_TOKEN = 150

#: Minimum total US births before a token counts as a common given name.
#:
#: This tier is the INVERSE of the keep tiers: a hit is evidence a candidate is a
#: *person*, which on the inbound path means redact. It exists because
#: capitalisation-based candidate generation scores zero by construction on the
#: fixture's ``lowercase-writing`` and ``allcaps-writing`` frames ("then terrence
#: okonkwo showed up", "MY BEST FRIEND DESHAWN PRITCHARD"), and a
#: case-insensitive scan needs a given-name list to have anything to scan for.
#:
#: **It was built from the wrong population until 2026-08-07**, and that was an
#: equity defect rather than a tuning miss. The old tier took the first tokens of
#: the `full` tier — notable people — at >= 3 distinct bearers, which answers
#: "was a famous person called this", not "is this a name a US child is given".
#: Measured: `Deshawn`, `Ayaan` and `Meisha` absent while `Marguerite`,
#: `Terrence`, `Priya`, `Marisol` and `Vinny` were present. **The misses skewed
#: toward Black and South Asian given names.**
#:
#: The bearer floor was measured as the lever first and rejected. Floor 2 bought
#: nothing (Deshawn still absent) for +2.6% over-fire; floor 1 reached Deshawn and
#: Ayaan but admitted 39,830 tokens for **+7.9% over-fire**, still could not reach
#: Meisha at any floor, and regressed the `heading-capital-on-an-ordinary-word`
#: frame — "Breeds I Like" -> "Breeds I {NAME}", because some notable label leads
#: with "Like".
#:
#: SSA births are a **dense** signal where a bearer count is sparse, so the tier
#: can be simultaneously larger in the right places and cleaner. Confirmed rather
#: than assumed: `Like` and `Pride` — the two tokens that broke the floor-1 arm —
#: **have no birth record at any threshold**, so the mechanism that regressed that
#: frame does not exist here.
#:
#: 1,800 IS THE MEASURED KNEE, not a round number. Against the 25-essay gate
#: corpus, over-firing by floor: 1,000 -> 0.80, 1,200 -> 0.76, 1,400 -> 0.76,
#: 1,600 -> 0.72, **1,800 -> 0.60**, 2,000 -> 0.60. The ceiling is 0.72 and the
#: previous tier sat exactly on it, so 1,600 would pass with no headroom while
#: 1,800 buys 0.12 spans/essay for 635 of the rarest names in the band. Every
#: floor tested closes `Deshawn` and takes visible recall 96.2% -> 100.0%.
#:
#: What it still does not reach, named: `Meisha`, at 1,048 births since 1880.
#: Reaching her needs a floor <= 1,048, which measures 0.80 spans/essay and fails
#: the over-firing gate. That is a real trade rather than an oversight — it buys
#: the rarest tail at the cost of the tightest gate — and it is left unmade.
GIVEN_NAME_MIN_BIRTHS = 1_800

#: Census bar for the demonym tier — 2.5x stricter than the short tier's, and the
#: asymmetry is the point rather than an accident of tuning.
#:
#: Every other keep tier is earned: a `short` entry carries >= 100 sitelinks of
#: external notability behind it, a `full` entry >= 10. A demonym carries **none**.
#: It is a blanket keep granted to a bare single token for being a word, with no
#: evidence about the entity in front of us at all, so it has to be the cheapest
#: keep to lose and the hardest to earn.
#:
#: MEASURED, on the 2026-08-06 cut: 1,057 normalised English demonyms, of which
#: only **60 appear in the Census surname file at all**. At this bar the tier
#: drops four — `english` (46,393 bearers), `welsh` (30,153), `horner` (23,881)
#: and `thai` (11,644) — plus the nine that are already common given names
#: (`dane`, `danish`, `dutch`, `finn`, `french`, `german`, `lao`, `philippine`,
#: `roman`). 13 of 1,057. `horner` is the case that forced the number below the
#: short tier's 25,000: it is a demonym of Horn and an ordinary American surname,
#: and keeping it would mean a coach named Horner stops redacting.
#:
#: What it costs, named: bare `English`, `Welsh`, `Thai`, `French`, `German`,
#: `Dutch`, `Danish` and `Roman` still over-fire when a student capitalises them
#: mid-sentence. That is over-redaction, which the inbound policy absorbs, and it
#: is the direction this tier is allowed to fail in. `Irish` (7,336), `Cornish`
#: (8,050) and `Catalan` (6,899) survive.
DEMONYM_MAX_US_SURNAME_POPULATION = 10_000

#: Sitelink floor for the settlement tier — the only tier that is neither a keep
#: nor a redact signal. It decides the *placeholder type* of a span that is
#: already going to be masked, so it cannot change what is redacted, only what
#: the student reads back: `{LOCATION}` instead of "your trip to {NAME}".
#:
#: A settlement is deliberately NOT keepable — a town name is where a student
#: lives, which is why :data:`SETTLEMENT_ROOT` is subtracted from the place tier
#: in SPARQL. This tier fetches the names that exclusion throws away, for typing
#: alone. That asymmetry is why the floor is an order of magnitude below
#: :data:`PLACE_MIN_SITELINKS_SINGLE_TOKEN`: there, a single-token entry buys a
#: permanent KEEP for an ordinary word and has to be expensive; here the worst a
#: wrong entry can do is mistype a span that was masked either way.
#:
#: MEASURED (2026-08-07 cut), which is why it is not 150. The population is much
#: steeper than the place tier's: >=20 gives 46,558 keys, >=30 gives 25,408,
#: >=50 gives 5,692, >=90 gives 1,339, >=150 only 356. `Akron` — the frame that
#: motivated the tier — carries **94**, so any floor at or above 100 misses it
#: and a floor of 90 clears it by four sitelinks against a moving upstream, which
#: is not a margin. 30 keeps the fixture's towns (Akron 94, Cleveland 138, Dayton
#: 89, Westfield 52, Brooklyn 128) with room, and the collision cost it admits is
#: bounded by the two subtractions below rather than by the floor.
#:
#: **The floor was tested as a purity lever and rejected, because it does not
#: separate.** The channel it would have to close is the ordinary English word
#: that is also a US town — `Christmas` (30 sitelinks), `Spring`, `Friendship`,
#: `Liberty`, `Union`, `Paradise`, `Sandwich`, `Normal`, `Peculiar`,
#: `Independence`. Raising the floor makes that channel *worse*: measured against
#: `/usr/share/dict/words`, ordinary words are 7.2% of single-token keys at 30 and
#: **16.5% at 150**, because the entries a high floor keeps are world capitals
#: that are themselves dictionary words — Rome, Moscow, Berlin, Vienna, Venice.
#: A higher floor shrinks the tier and enriches the noise. So the floor buys
#: coverage only, and the collision cost is paid by the subtractions instead.
#:
#: What that leaves unclosed, named: on the 27 un-scrubbed student documents the
#: tier's ONLY effect is to retype 4 spans of `Christmas` in one document from
#: `{NAME}` to `{LOCATION}`. Both labels are wrong and the span is a pre-existing
#: over-fire — the word should not have been masked at all — so the tier
#: relabels a defect rather than creating one, which is why no ordinary-word
#: subtraction was added: it would buy nothing on a span that is wrong either
#: way, and would cost the student who really is from Normal, Illinois.
SETTLEMENT_MIN_SITELINKS = 30

#: Census bar for the settlement tier, and the reason a low floor is affordable.
#:
#: Half of American town names are somebody's surname — Jackson, Madison,
#: Houston, Alexandria, Florence — because the towns were named after the people.
#: Typing a span by settlement membership alone would relabel a classmate named
#: Jackson as `{LOCATION}`, which is the same defect as `Akron` -> `{NAME}` with
#: the sign flipped, and on a far commoner population.
#:
#: So a settlement name that is also a well-borne US surname is dropped from the
#: tier and falls back to `{NAME}` — the *conservative* type, because a person
#: mistyped as a place is worse than a place mistyped as a person: a host that
#: reads the type back writes "your friend {LOCATION}". Set to the demonym bar
#: rather than the short tier's 25,000 for the same reason the demonym bar is
#: stricter than the short tier's — the tier carries no evidence about the entity
#: in front of us, only about the string.
#:
#: The `given` tier is subtracted alongside it, on the rule the demonym tier
#: already follows: a common given name is evidence of a *person*, and here that
#: decides which of two masks a student reads back.
#:
#: MEASURED at the chosen floor: of 20,182 single-token settlement keys, 941 are
#: already common given names and 1,135 clear this bar (386 are both); **18,492
#: survive**, `akron` among them — 0 US bearers, no given-name hit, which is why
#: the frame that motivated the tier clears with nothing tuned for it.
#:
#: What the two subtractions cost, named rather than left as a caveat. By the
#: Census bar: `jackson` (708,099 bearers), `austin` (119,706), `houston`
#: (56,900), `cleveland` (31,123), `madison` (28,411). By the given-name rule
#: alone: `brooklyn` (123), `alexandria` (289), `aurora` (548), `dayton` (8,494),
#: `salem` (8,404). Each of those towns still redacts; it types `{NAME}`. Note the
#: coupling: widening `given` to the right population (SSA births) would drop
#: *more* settlements, and that is the correct direction — every one of those
#: tokens really is a name a US child is given.
SETTLEMENT_MAX_US_SURNAME_POPULATION = 10_000

# --- Wikidata classes -------------------------------------------------------

#: Roots for the place tier, matched through ``wdt:P31/wdt:P279*``.
PLACE_ROOTS = (
    "Q618123",    # geographical feature
    "Q811979",    # architectural structure
    "Q10864048",  # first-level administrative country subdivision
    "Q6256",      # country
    "Q473972",    # protected area
    "Q22698",     # park
)

#: Landmark roots re-included even when the entity is also typed a settlement.
LANDMARK_ROOTS = (
    "Q46169",    # national park
    "Q473972",   # protected area
    "Q5003624",  # memorial
    "Q4989906",  # monument
    "Q33506",    # museum
    "Q839954",   # archaeological site
    "Q570116",   # tourist attraction
)

#: Roots for the title tier: the works and characters a student writes *about*.
#: The full tier is ``P31 wd:Q5`` — human — so before this tier existed every
#: work title and every fictional character resolved not-notable and was
#: redacted: "Harry Potter taught me about friendship" became "{NAME} taught me
#: about friendship". Writing about a book or a film is one of the commonest
#: things a school essay does, which made this the largest precision defect in
#: the gazetteer, and it is not reachable from the detector.
TITLE_ROOTS = (
    "Q95074",     # fictional character
    "Q11424",     # film
    "Q7725634",   # literary work
    "Q5398426",   # television series
    "Q25379",     # play
    "Q1667921",   # novel series
)

#: Minimum sitelinks for the title tier. Same bar as the full tier: a title is
#: judged on being *published*, and unlike a bare surname it cannot collide with
#: a private individual — the tier is multi-token only (see below).
TITLE_MIN_SITELINKS = 10

#: Single-token titles are **excluded by construction**, and this is the one
#: decision that makes the tier safe rather than catastrophic. "It", "Up", "Her",
#: "Room", "Brave", "Big" and "Cats" are all films; admitting them would make
#: those ordinary words permanently notable, and notable means KEEP, so the cost
#: lands on *recall* — the leg being closed. A multi-token title cannot do that,
#: because it is matched whole.
TITLE_MIN_TOKENS = 2

#: Titles composed *entirely* of ordinary words are dropped. "My Best Friend" is a
#: film, and admitting it made "MY BEST FRIEND DESHAWN PRITCHARD" keep whole —
#: recall on the allcaps frame went 100% to 0%. A title carries evidence only when
#: at least one of its tokens is not a word every essay already contains, so the
#: filter reuses the candidate generator's stoplist rather than inventing a second
#: one. "The Lion King", "To Kill a Mockingbird" and "My Cousin Vinny" all survive
#: it; "The Man", "My Best Friend" and "The Way We Were" do not.
TITLE_REQUIRES_A_DISTINCTIVE_TOKEN = True

#: Excluded from the place tier. A settlement name is a student's hometown.
SETTLEMENT_ROOT = "Q486972"       # human settlement

#: Excluded everywhere. A school name is PII and is already covered by identity
#: interpolation; a gazetteer hit here would override that.
SCHOOL_ROOT = "Q2385804"          # educational institution

#: Name particles. A surname is stored with its particle attached as well as
#: bare, so "van Gogh" resolves without "Gogh" alone having to.
PARTICLES = frozenset(
    {
        "van", "von", "de", "del", "della", "di", "da", "du", "la", "le",
        "les", "der", "den", "ten", "ter", "dos", "das", "al", "bin", "ibn",
        "mac", "mc", "st", "saint", "san", "abu", "ben", "op", "vander",
    }
)

#: Word processors and phone keyboards emit curly quotes and en-dashes, so student
#: prose carries them and NFKD does not fold them. Left unmapped, "Lincoln’s"
#: normalises to "lincoln s" and misses the gazetteer entirely — caught by a unit
#: test, not by inspection. Must stay identical to the runtime module's copy.
_SMART_QUOTES = str.maketrans(
    {
        "‘": "'", "’": "'", "ʼ": "'", "′": "'",
        "“": '"', "”": '"',
        "‐": "-", "‑": "-", "‒": "-", "–": "-",
        "—": "-", "−": "-",
    }
)

_PREFIXES = (
    "PREFIX wdt: <http://www.wikidata.org/prop/direct/> "
    "PREFIX wd: <http://www.wikidata.org/entity/> "
    "PREFIX wikibase: <http://wikiba.se/ontology#> "
    "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> "
)

#: Label languages to accept, and the reason there is more than one.
#:
#: Wikidata is migrating labels that are spelled identically across languages out
#: of the per-language slots and into the language-agnostic ``mul`` code. When an
#: entity's English label is folded that way, its ``@en`` label is *removed* — so
#: a query filtering ``LANG(?l) = 'en'`` returns nothing for it, with no error and
#: no empty row to notice. Charles Darwin (Q1035, 277 sitelinks, ``P31 wd:Q5``) is
#: the case that surfaced this: he carries ``"Charles Darwin"@mul`` and no ``@en``
#: label at all, so the full tier had no entry for the most-cited scientist a
#: school essay names, and bare "Darwin" could not be corroborated from it either.
#:
#: Measured on the 2026-08-05 cut: 1,429 humans at >= FULL_MIN_SITELINKS are
#: mul-only (1,387 of them multi-token), and 28 clear SHORT_MIN_SITELINKS —
#: Victor Hugo, J. K. Rowling, Alexander Graham Bell, Niels Bohr, Montesquieu,
#: Douglas Adams, Anne Brontë. Small as a count, and squarely the population a
#: student writes about. The migration is ongoing, so the hole grows on its own;
#: this is why the filter names the languages it accepts rather than assuming one.
#:
#: Accepting ``mul`` cannot cost recall on its own terms — it only ever *adds*
#: public figures — but an addition to the short tier is still a bare surname, so
#: it stays subject to SHORT_MAX_US_SURNAME_POPULATION like every other entry.
LABEL_LANGUAGES = ("en", "mul")


def _label_language_filter(var: str = "?l") -> str:
    """SPARQL filter accepting any of :data:`LABEL_LANGUAGES` for ``var``."""
    langs = ", ".join(f"'{code}'" for code in LABEL_LANGUAGES)
    return f"FILTER(LANG({var}) IN ({langs})) "


def normalize(name: str) -> str:
    """Fold a name to its lookup key.

    Accent-stripped, lower-cased, punctuation reduced to spaces, with the
    apostrophe and the internal hyphen preserved because they are part of the
    name ("O'Keeffe", "Raghunathan-Bell") rather than punctuation around it.

    Kept byte-identical to :func:`vicary.gazetteer.normalize` — the builder
    and the reader must fold the same way or the asset answers nothing. The
    duplication is deliberate: the runtime module must not import a build tool.
    """
    folded = name.translate(_SMART_QUOTES)
    folded = unicodedata.normalize("NFKD", folded)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    folded = folded.lower()
    out = []
    for char in folded:
        if char.isalnum() or char in "'-":
            out.append(char)
        else:
            out.append(" ")
    key = " ".join("".join(out).split())
    # The possessive strip belongs to the query side ("Terrence's older brother"),
    # not to labels — but it lives here too so the two folds are behaviourally
    # identical rather than merely similar. A parity test compares them over a
    # shared vector set, and "similar" is what that test exists to reject.
    for clitic in ("'s", "s'"):
        if key.endswith(clitic) and len(key) > len(clitic) + 1:
            key = key[: -len(clitic)].rstrip("'").strip()
            break
    return key


#: Retries for a transient endpoint failure, and why the builder needs them.
#:
#: A full build makes four large queries in sequence and the last one takes the
#: longest, so a single 429 on query four discards three successful multi-minute
#: fetches and the whole run. That happened: a handful of interactive diagnostic
#: probes against the same public endpoint were enough to rate-limit the build
#: (MUST #8c — a long network job has to survive a transient failure, not restart
#: from zero because of one).
_QUERY_ATTEMPTS: int = 5
_QUERY_BACKOFF_SECONDS: float = 20.0

#: qlever answers a **query timeout** with HTTP 429 and a JSON body reading
#: ``{"exception": "Operation timed out. Last operation: ..."}``. That is the same
#: status code it uses for actual throttling, and the two need opposite handling:
#: a throttle clears on its own, a timeout is a property of the query and will
#: still be a timeout on the fifth attempt. Retrying it costs the full backoff
#: ladder — 20+40+60+80s of sleeping — and then fails anyway, while reporting a
#: cause ("Too Many Requests") that sends the reader looking for a rate limit
#: that was never there. Measured: the 5-9 title band failed this way in 365s.
#:
#: So the body is read and matched. If it names a timeout the error is re-raised
#: immediately, carrying qlever's own message, because the fix is to split the
#: query rather than to wait.
_TIMEOUT_MARKERS = ("timed out", "timeout")


def _query(sparql: str, *, timeout: int = 300) -> list[tuple[str, int]]:
    """Run a SPARQL query returning ``(?l, ?s)`` and parse the CSV response.

    Retries a rate-limit or server-side failure with linear backoff. A 4xx that
    is *not* 429 raises immediately: a malformed query does not become valid on
    the second attempt, and retrying it just multiplies the load that caused the
    throttling in the first place. A 429 that is really a *query timeout* also
    raises immediately — see :data:`_TIMEOUT_MARKERS`.
    """
    body = urllib.parse.urlencode({"query": _PREFIXES + sparql}).encode()
    request = urllib.request.Request(
        SPARQL_ENDPOINT,
        data=body,
        headers={"Accept": "text/csv", "User-Agent": user_agent()},
    )
    payload = ""
    for attempt in range(1, _QUERY_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read().decode("utf-8")
            break
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            retryable = (
                not isinstance(exc, urllib.error.HTTPError)
                or exc.code == 429
                or exc.code >= 500
            )
            if retryable and isinstance(exc, urllib.error.HTTPError):
                # Read once — the body is consumed by this, which is fine because
                # a retry re-issues the request rather than re-reading this one.
                try:
                    detail = exc.read().decode("utf-8", "replace")
                except Exception:  # noqa: BLE001 - a body we cannot read is not a verdict
                    detail = ""
                if any(marker in detail.lower() for marker in _TIMEOUT_MARKERS):
                    raise RuntimeError(
                        f"endpoint reported a query timeout as HTTP {exc.code}; "
                        "retrying cannot help — split the query. Endpoint said: "
                        f"{detail[:300]}"
                    ) from exc
            if not retryable or attempt == _QUERY_ATTEMPTS:
                raise
            delay = _QUERY_BACKOFF_SECONDS * attempt
            print(
                f"  {exc} — retry {attempt}/{_QUERY_ATTEMPTS - 1} in {delay:.0f}s",
                file=sys.stderr,
            )
            time.sleep(delay)
    reader = csv.reader(io.StringIO(payload))
    header = next(reader, None)
    if header is None:
        raise RuntimeError("empty response from SPARQL endpoint")
    rows: list[tuple[str, int]] = []
    for row in reader:
        if len(row) != 2:
            continue
        try:
            rows.append((row[0], int(row[1])))
        except ValueError:
            continue
    if not rows:
        raise RuntimeError(f"query returned no usable rows: {sparql[:120]}...")
    return rows


def cached(
    cache_dir: Path | None, name: str, fetch: Callable[[], list[tuple[str, int]]]
) -> list[tuple[str, int]]:
    """Run ``fetch``, or read its rows back from ``cache_dir/name.json``.

    A threshold in this module is only defensible once somebody has measured what
    moving it costs, and that measurement is a *sweep* — build the tiers at five
    values, score each against the Census control and the held-out figures, keep
    the one that pays. :func:`build_tiers` is a pure function of the fetched rows,
    so every one of those iterations wants the same rows and none of them wants
    the network.

    Without this the sweep re-issues ~30 SPARQL queries per candidate value
    against donated infrastructure, which is both rude and self-defeating: the
    2026-08-05 rebuild rate-limited itself exactly that way, and the retry ladder
    then hides the cost as latency rather than reporting it as a throttle.

    The cache is keyed by name only, not by the query text, because the fetch
    parameters live in this file next to it — so **delete the directory after
    changing a query or a fetch threshold**. It is a build-time convenience, not
    a coherence mechanism, and the manifest records the cut date of the rows that
    were actually folded, not of the run that folded them.
    """
    if cache_dir is None:
        return fetch()
    path = cache_dir / f"{name}.json"
    if path.exists():
        rows = [(label, int(count)) for label, count in json.loads(path.read_text())]
        print(f"  {len(rows):,} labels (cached: {path})", file=sys.stderr)
        return rows
    rows = fetch()
    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows))
    return rows


def fetch_humans(min_sitelinks: int) -> list[tuple[str, int]]:
    """Labels + sitelink counts for ``P31 wd:Q5`` above a threshold.

    English *or* ``mul`` — see :data:`LABEL_LANGUAGES` for why one is not enough.
    """
    return _query(
        "SELECT DISTINCT ?l ?s WHERE { "
        "?i wdt:P31 wd:Q5 ; wikibase:sitelinks ?s ; rdfs:label ?l . "
        f"FILTER(?s >= {min_sitelinks}) {_label_language_filter()}}}"
    )


def fetch_places(min_sitelinks: int) -> list[tuple[str, int]]:
    """Public geography and landmarks, with settlements and schools removed."""
    roots = " ".join(f"wd:{qid}" for qid in PLACE_ROOTS)
    return _query(
        "SELECT DISTINCT ?l ?s WHERE { "
        f"VALUES ?cls {{ {roots} }} "
        "?i wdt:P31/wdt:P279* ?cls ; wikibase:sitelinks ?s ; rdfs:label ?l . "
        f"FILTER(?s >= {min_sitelinks}) {_label_language_filter()}"
        f"FILTER NOT EXISTS {{ ?i wdt:P31/wdt:P279* wd:{SETTLEMENT_ROOT} }} "
        f"FILTER NOT EXISTS {{ ?i wdt:P31/wdt:P279* wd:{SCHOOL_ROOT} }} }}"
    )


def fetch_landmarks(min_sitelinks: int) -> list[tuple[str, int]]:
    """Named landmarks, kept even when Wikidata also types them a settlement."""
    roots = " ".join(f"wd:{qid}" for qid in LANDMARK_ROOTS)
    return _query(
        "SELECT DISTINCT ?l ?s WHERE { "
        f"VALUES ?cls {{ {roots} }} "
        "?i wdt:P31/wdt:P279* ?cls ; wikibase:sitelinks ?s ; rdfs:label ?l . "
        f"FILTER(?s >= {min_sitelinks}) {_label_language_filter()}"
        f"FILTER NOT EXISTS {{ ?i wdt:P31/wdt:P279* wd:{SCHOOL_ROOT} }} }}"
    )


def fetch_settlements(min_sitelinks: int) -> list[tuple[str, int]]:
    """Human settlements — the names :func:`fetch_places` throws away.

    The same ``NOT EXISTS`` filter that makes the place tier safe is inverted
    here to ``EXISTS``, because the two tiers want opposite things from the same
    rows: the place tier must not KEEP a student's hometown, and this tier needs
    that hometown's name in order to TYPE it. So the signal was never unused — it
    was never fetched, which is why `Akron` needed a rebuild rather than a patch.

    Schools stay excluded, for the reason they are excluded everywhere: a school
    name is PII covered by identity interpolation, and it must mask as
    ``{SCHOOL}`` rather than being retyped by a gazetteer hit.
    """
    return _query(
        "SELECT DISTINCT ?l ?s WHERE { "
        f"?i wdt:P31/wdt:P279* wd:{SETTLEMENT_ROOT} ; "
        "wikibase:sitelinks ?s ; rdfs:label ?l . "
        f"FILTER(?s >= {min_sitelinks}) {_label_language_filter()}"
        f"FILTER NOT EXISTS {{ ?i wdt:P31/wdt:P279* wd:{SCHOOL_ROOT} }} }}"
    )


def fetch_titles(min_sitelinks: int) -> list[tuple[str, int]]:
    """Works and fictional characters — what a student writes *about*.

    Schools are excluded for the same reason they are excluded from the place
    tier: a school name is PII covered by identity interpolation, and a gazetteer
    hit would override that. Humans are NOT excluded, because a fictional
    character is sometimes also typed a human in Wikidata and dropping those
    would lose exactly the entries this tier exists for.

    **One query per root, not a ``VALUES`` union.** This is the largest of the
    four fetches and the union shape does not survive being asked for more: at
    ``>= 10`` it completes, and at ``5..9`` qlever times out sorting on ``?cls``
    and reports it as a 429 (see :data:`_TIMEOUT_MARKERS`). Split, the same band
    returns in 2-12s per root. Splitting also makes a failure attributable to a
    class instead of to the whole tier, and the union is free in Python.
    """
    rows: list[tuple[str, int]] = []
    for qid in TITLE_ROOTS:
        rows.extend(
            _query(
                "SELECT DISTINCT ?l ?s WHERE { "
                f"?i wdt:P31/wdt:P279* wd:{qid} ; "
                "wikibase:sitelinks ?s ; rdfs:label ?l . "
                f"FILTER(?s >= {min_sitelinks}) {_label_language_filter()}"
                f"FILTER NOT EXISTS {{ ?i wdt:P31/wdt:P279* wd:{SCHOOL_ROOT} }} }}"
            )
        )
    return rows


def fetch_demonyms() -> list[tuple[str, int]]:
    """English demonyms — ``Cuban``, ``Nigerian``, ``Bostonian`` — via ``P1549``.

    Sitelinks are not fetched and the count column is a constant 1: a demonym is
    not notable, it is a *word*, and there is nothing here for a threshold to
    rank. That is also the reason this tier is subtracted harder than any other
    (see :data:`DEMONYM_MAX_US_SURNAME_POPULATION`).

    One query, no root walk: ``P1549`` is a direct statement on the place, so the
    result set is ~1,000 rows and returns in under two seconds.
    """
    return _query(
        "SELECT DISTINCT ?l (1 AS ?s) WHERE { "
        '?i wdt:P1549 ?l . FILTER(LANG(?l) = "en") }'
    )


#: U.S. Census 2010 surname frequencies: every surname borne by 100 or more
#: people at the 2010 census, with counts. Public domain, no credentials.
CENSUS_SURNAMES_URL = (
    "https://www2.census.gov/topics/genealogy/2010surnames/names.zip"
)
CENSUS_SURNAMES_MEMBER = "Names_2010Census.csv"


def parse_census_surnames(text: str) -> dict[str, int]:
    """``{normalised surname: number of U.S. bearers}`` from the Census CSV text.

    The row-count floor is not decoration. This list is used only to *subtract*
    from the short tier, so a short read would make the gazetteer more permissive
    rather than less — the wrong direction to fail in silently.
    """
    counts: dict[str, int] = {}
    for row in csv.DictReader(io.StringIO(text)):
        name = normalize(row.get("name", ""))
        if not name or name == "all other names":
            continue
        try:
            counts[name] = int(row["count"])
        except (KeyError, TypeError, ValueError):
            continue
    if len(counts) < 100_000:
        raise RuntimeError(
            f"Census surname file parsed to only {len(counts)} rows; expected "
            "~162k. Refusing to build a short tier from a truncated exclusion "
            "list, because the failure mode is a more permissive gazetteer."
        )
    return counts


def read_census_surnames(source: str | Path) -> dict[str, int]:
    """Parse a locally-held copy of the Census surname file.

    Accepts the distributed ``.zip`` or an already-extracted ``.csv``. Exists so
    the false-positive control in :mod:`vicary.eval.census` can run offline and
    reproducibly — a measurement that needs the network on every run is a
    measurement that gets skipped.
    """
    path = Path(source).expanduser()
    if path.suffix.lower() == ".zip":
        import zipfile

        with zipfile.ZipFile(path) as archive:
            with archive.open(CENSUS_SURNAMES_MEMBER) as member:
                text = io.TextIOWrapper(member, encoding="utf-8").read()
    else:
        text = path.read_text(encoding="utf-8")
    return parse_census_surnames(text)


def fetch_census_surnames() -> dict[str, int]:
    """Download and parse the Census surname file. Reaches the network."""
    import zipfile

    request = urllib.request.Request(
        CENSUS_SURNAMES_URL, headers={"User-Agent": user_agent()}
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        payload = response.read()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        with archive.open(CENSUS_SURNAMES_MEMBER) as member:
            text = io.TextIOWrapper(member, encoding="utf-8").read()
    return parse_census_surnames(text)


#: SSA baby-names archive: per-name US birth counts by year, 1880-present, public
#: domain. Members are ``yob<year>.txt``, each row ``name,sex,count``.
SSA_GIVEN_NAMES_URL = "https://www.ssa.gov/oact/babynames/names.zip"

#: Member prefix inside that archive.
SSA_GIVEN_NAMES_MEMBER_PREFIX = "yob"


def parse_ssa_given_names(members: dict[str, str]) -> dict[str, int]:
    """``{normalised given name: total US births}`` from ``{member: text}``.

    Births are summed across **all years and both sexes**. Both halves of that
    were measured rather than assumed:

    * *All years, not a recent window.* A recent window tracks the population
      actually in school, which is the better argument in the abstract; the data
      disagrees at the tail, which is where this tier's misses live. `Meisha` has
      1,048 births since 1880 and 189 since 2001, and the tail is exactly the
      population the old bearer-derived tier was missing.
    * *Both sexes summed.* A name split across the two files is one name to a
      reader, and holding each half to the floor separately would drop names near
      it for being androgynous.

    The row-count floor is not decoration, and it fails in the same direction the
    Census one does. This list makes the redactor **more** aggressive, so a short
    read makes it *less* — quietly restoring exactly the leak this tier exists to
    close, with no error to notice.
    """
    totals: dict[str, int] = defaultdict(int)
    rows = 0
    for text in members.values():
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(",")
            if len(parts) != 3:
                continue
            key = normalize(parts[0])
            if not key or " " in key:
                continue
            try:
                totals[key] += int(parts[2])
            except ValueError:
                continue
            rows += 1
    if rows < SSA_MIN_ROWS:
        raise ValueError(
            f"SSA given-name file parsed only {rows} rows (expected "
            f">= {SSA_MIN_ROWS:,}) — truncated or the wrong file. Refusing to "
            "build a `given` tier that would silently redact less."
        )
    return dict(totals)


#: Sanity floor for the SSA parse. The 2026 archive carries ~2.1M rows across 146
#: year files; anything under this is a truncated download or the wrong archive.
SSA_MIN_ROWS = 1_000_000


def read_ssa_given_names(source: str | Path) -> dict[str, int]:
    """Parse a locally-held copy of the SSA archive.

    Accepts the distributed ``names.zip`` or a directory of extracted
    ``yob<year>.txt`` files. Local-only by design — see
    :data:`vicary_build.config.SSA_NAMES_ZIP_ENV_VAR` for why there is no
    download path to fall back on.
    """
    path = Path(source).expanduser()
    members: dict[str, str] = {}
    if path.is_dir():
        for child in sorted(path.glob(f"{SSA_GIVEN_NAMES_MEMBER_PREFIX}*.txt")):
            members[child.name] = child.read_text(encoding="utf-8")
    else:
        import zipfile

        with zipfile.ZipFile(path) as archive:
            for name in sorted(archive.namelist()):
                if not name.rsplit("/", 1)[-1].startswith(
                    SSA_GIVEN_NAMES_MEMBER_PREFIX
                ):
                    continue
                with archive.open(name) as member:
                    members[name] = io.TextIOWrapper(
                        member, encoding="utf-8"
                    ).read()
    if not members:
        raise ValueError(
            f"no {SSA_GIVEN_NAMES_MEMBER_PREFIX}<year>.txt members in {path} — "
            f"this is not the SSA archive from {SSA_GIVEN_NAMES_URL}"
        )
    return parse_ssa_given_names(members)


def fetch_ssa_given_names() -> dict[str, int]:
    """The SSA births table, from the local copy the build requires.

    Deliberately does NOT reach the network, and deliberately raises rather than
    returning empty. ``ssa.gov`` 403s some networks on every path, so a download
    is not a dependency this build can take; and an empty return would build a
    `given` tier of nothing, which reads as a quiet recall regression rather than
    as a missing file.
    """
    source = config.get(config.SSA_NAMES_ZIP_ENV_VAR)
    if not source:
        raise RuntimeError(
            f"the `given` tier needs the SSA baby-names archive. Download "
            f"{SSA_GIVEN_NAMES_URL} by hand — ssa.gov returns HTTP 403 to some "
            f"networks on every path — and set "
            f"{config.SSA_NAMES_ZIP_ENV_VAR} to it."
        )
    return read_ssa_given_names(source)


def short_forms(label: str) -> tuple[str, ...]:
    """Partial surface forms a student might write instead of the full name.

    ``"Vincent van Gogh"`` yields ``("gogh", "van gogh")``; ``"Plato"`` yields
    ``("plato",)``. The bare first name is **not** a short form: first names are
    the single most common private-name surface form in student prose ("Terrence
    and I stayed up late"), so admitting them would trade the whole recall leg
    for a handful of mononymous celebrities.
    """
    tokens = normalize(label).split()
    if not tokens:
        return ()
    if len(tokens) == 1:
        return (tokens[0],)
    forms = [tokens[-1]]
    if len(tokens) >= 2 and tokens[-2] in PARTICLES:
        forms.append(" ".join(tokens[-2:]))
    if len(tokens) >= 3 and tokens[-3] in PARTICLES:
        forms.append(" ".join(tokens[-3:]))
    return tuple(forms)


def first_and_last_form(label: str) -> str | None:
    """``"Franklin Delano Roosevelt"`` -> ``"franklin roosevelt"``, or ``None``.

    Wikidata stores a president under the name on the inauguration card;
    fourteen-year-olds write the name on the textbook cover. "Franklin Delano
    Roosevelt", "Harry S. Truman" and "Dwight D. Eisenhower" are all in the full
    tier, and "Franklin Roosevelt", "Harry Truman" and "Dwight Eisenhower" — the
    forms actually written — were in no tier at all, so the *full* name of a
    three-term president redacted. That is a middle-name gap, distinct from the
    bare-surname problem the short tier exists for, and it is invisible to any
    test whose KEEP figures happen to have two-token labels.

    Particles are left alone: "Ludwig van Beethoven" must not yield "ludwig
    beethoven", because the particle belongs to the surname rather than sitting
    between two names. Anything past three tokens is skipped — a four-token label
    is usually nobility or a transliteration, where the first and last tokens are
    not a name anybody writes.
    """
    tokens = normalize(label).split()
    if len(tokens) != 3:
        return None
    if any(token in PARTICLES for token in tokens):
        return None
    return f"{tokens[0]} {tokens[-1]}"


def build_tiers(
    humans: list[tuple[str, int]],
    places: list[tuple[str, int]],
    census: dict[str, int] | None = None,
    titles: list[tuple[str, int]] | None = None,
    demonyms: list[tuple[str, int]] | None = None,
    settlements: list[tuple[str, int]] | None = None,
    ssa_births: dict[str, int] | None = None,
) -> dict[str, set[str]]:
    """Fold raw ``(label, sitelinks)`` rows into the lookup tiers."""
    best: dict[str, int] = {}
    for label, sitelinks in humans:
        key = normalize(label)
        if key and sitelinks > best.get(key, 0):
            best[key] = sitelinks

    full = {key for key in best if " " in key}

    # The name on the textbook cover, for the people famous enough to have one.
    for key, sitelinks in best.items():
        if sitelinks < FULL_DERIVED_MIN_SITELINKS:
            continue
        derived = first_and_last_form(key)
        if derived:
            full.add(derived)

    short: dict[str, int] = defaultdict(int)
    for key, sitelinks in best.items():
        if sitelinks < SHORT_MIN_SITELINKS:
            continue
        for form in short_forms(key):
            short[form] = max(short[form], sitelinks)

    if census is not None:
        short = defaultdict(
            int,
            {
                form: sitelinks
                for form, sitelinks in short.items()
                if census.get(form, 0) < SHORT_MAX_US_SURNAME_POPULATION
            },
        )

    place = set()
    for label, sitelinks in places:
        key = normalize(label)
        if not key:
            continue
        floor = (
            PLACE_MIN_SITELINKS_SINGLE_TOKEN
            if " " not in key
            else PLACE_MIN_SITELINKS
        )
        if sitelinks >= floor:
            place.add(key)

    # Given names come from US birth records, NOT from the full tier's first
    # tokens. See GIVEN_NAME_MIN_BIRTHS: the derived version asked which names
    # famous people have, and the answer skewed away from the students whose
    # names this tier exists to catch.
    given = {
        token
        for token, births in (ssa_births or {}).items()
        if births >= GIVEN_NAME_MIN_BIRTHS
        and len(token) >= 2
        and "-" not in token
        and "'" not in token
    }

    title: set[str] = set()
    for label, sitelinks in titles or []:
        key = normalize(label)
        if not key or sitelinks < TITLE_MIN_SITELINKS:
            continue
        tokens = key.split()
        if len(tokens) < TITLE_MIN_TOKENS:
            continue
        if TITLE_REQUIRES_A_DISTINCTIVE_TOKEN and all(
            t in _TITLE_ORDINARY_WORDS for t in tokens
        ):
            continue
        title.add(key)

    # Demonyms last, because both subtractions read tiers built above: `given`
    # is a redact signal and must win (a demonym that is also a common given
    # name is evidence of a person, not of a nationality), and the Census bar is
    # applied even when no census file was supplied for the short tier — in that
    # case the tier is built without it and the manifest says so.
    demonym: set[str] = set()
    for label, _ in demonyms or []:
        key = normalize(label)
        if not key or key in given:
            continue
        if census is not None and (
            census.get(key, 0) >= DEMONYM_MAX_US_SURNAME_POPULATION
        ):
            continue
        demonym.add(key)

    # Settlements last, for the same reason demonyms are: both subtractions read
    # tiers built above. Unlike every tier before it this one is neither a keep
    # nor a redact signal — see SETTLEMENT_MIN_SITELINKS. A settlement that is
    # also a common given name or a well-borne US surname is dropped and falls
    # back to the conservative `{NAME}` type.
    settlement: set[str] = set()
    for label, sitelinks in settlements or []:
        key = normalize(label)
        if not key or sitelinks < SETTLEMENT_MIN_SITELINKS:
            continue
        if key in given:
            continue
        if census is not None and (
            census.get(key, 0) >= SETTLEMENT_MAX_US_SURNAME_POPULATION
        ):
            continue
        # A settlement Wikidata ALSO types a landmark or an administrative
        # subdivision is already in the place tier, which is a KEEP and is
        # checked first, so it is never masked and never reaches typing. Leaving
        # it here would be inert; dropping it keeps the tier's meaning exact —
        # every entry is a name that gets masked.
        if key in place:
            continue
        settlement.add(key)

    return {
        "full": full,
        "short": set(short),
        "place": place,
        "given": given,
        "title": title,
        "demonym": demonym,
        "settlement": settlement,
    }


def write_asset(path: Path, tiers: dict[str, set[str]], meta: dict) -> int:
    """Write the gzipped tiered text asset. Returns bytes written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.StringIO()
    buffer.write(f"#!gazetteer {ASSET_FORMAT}\n")
    buffer.write("#!meta " + json.dumps(meta, sort_keys=True) + "\n")
    # Every tier the fold produced, not a hardcoded list — a tier added to
    # build_tiers and forgotten here builds clean, ships, and reads back empty,
    # which for a KEEP tier means redacting everything it was meant to protect.
    for tier in sorted(tiers):
        entries = sorted(tiers[tier])
        buffer.write(f"#!tier {tier} {len(entries)}\n")
        for entry in entries:
            buffer.write(entry + "\n")
    # mtime=0 so an unchanged cut produces a byte-identical file, which keeps a
    # rebuild from showing up as a spurious diff on a checked-in binary.
    with gzip.GzipFile(filename=str(path), mode="wb", compresslevel=9, mtime=0) as fh:
        fh.write(buffer.getvalue().encode("utf-8"))
    return path.stat().st_size


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=None,
        help="Output path. Defaults to data/notability.txt.gz beside this module.",
    )
    parser.add_argument(
        "--stats", action="store_true", help="Report tier sizes and write nothing."
    )
    parser.add_argument(
        "--cache-dir",
        default=None,
        help=(
            "Cache raw SPARQL rows here and reuse them on the next run. For "
            "threshold sweeps — see cached(). Delete it after changing a query."
        ),
    )
    args = parser.parse_args(argv)
    cache_dir = Path(args.cache_dir).expanduser() if args.cache_dir else None

    print(
        f"fetching humans (sitelinks >= {FULL_MIN_SITELINKS}) ...",
        file=sys.stderr,
        flush=True,
    )
    humans = cached(cache_dir, "humans", lambda: fetch_humans(FULL_MIN_SITELINKS))
    print(f"  {len(humans):,} labels", file=sys.stderr)

    print(
        f"fetching places (sitelinks >= {PLACE_MIN_SITELINKS}) ...",
        file=sys.stderr,
        flush=True,
    )
    places = cached(cache_dir, "places", lambda: fetch_places(PLACE_MIN_SITELINKS))
    print(f"  {len(places):,} labels", file=sys.stderr)

    print("fetching landmarks ...", file=sys.stderr, flush=True)
    landmarks = cached(
        cache_dir, "landmarks", lambda: fetch_landmarks(PLACE_MIN_SITELINKS)
    )
    print(f"  {len(landmarks):,} labels", file=sys.stderr)

    print(
        f"fetching titles and characters (sitelinks >= {TITLE_MIN_SITELINKS}) ...",
        file=sys.stderr,
        flush=True,
    )
    titles = cached(cache_dir, "titles", lambda: fetch_titles(TITLE_MIN_SITELINKS))
    print(f"  {len(titles):,} labels", file=sys.stderr)

    print(
        f"fetching settlements (sitelinks >= {SETTLEMENT_MIN_SITELINKS}) ...",
        file=sys.stderr,
        flush=True,
    )
    settlements = cached(
        cache_dir, "settlements",
        lambda: fetch_settlements(SETTLEMENT_MIN_SITELINKS),
    )
    print(f"  {len(settlements):,} labels", file=sys.stderr)

    print("fetching demonyms ...", file=sys.stderr, flush=True)
    demonyms = cached(cache_dir, "demonyms", fetch_demonyms)
    print(f"  {len(demonyms):,} labels", file=sys.stderr)

    print("fetching U.S. Census surname frequencies ...", file=sys.stderr, flush=True)
    census = fetch_census_surnames()
    print(f"  {len(census):,} surnames", file=sys.stderr)

    print("reading SSA given-name births ...", file=sys.stderr, flush=True)
    ssa_births = fetch_ssa_given_names()
    print(f"  {len(ssa_births):,} names", file=sys.stderr)

    tiers = build_tiers(
        humans, places + landmarks, census, titles, demonyms, settlements,
        ssa_births,
    )
    for tier, entries in tiers.items():
        print(f"tier {tier}: {len(entries):,} entries", file=sys.stderr)

    if args.stats:
        return 0

    meta = {
        "source": "wikidata (via qlever.cs.uni-freiburg.de)",
        "cut_date": date.today().isoformat(),
        "label_languages": list(LABEL_LANGUAGES),
        "full_min_sitelinks": FULL_MIN_SITELINKS,
        "full_derived_min_sitelinks": FULL_DERIVED_MIN_SITELINKS,
        "short_min_sitelinks": SHORT_MIN_SITELINKS,
        "short_max_us_surname_population": SHORT_MAX_US_SURNAME_POPULATION,
        "short_exclusion_source": "US Census 2010 surname file",
        "place_min_sitelinks": PLACE_MIN_SITELINKS,
        "place_min_sitelinks_single_token": PLACE_MIN_SITELINKS_SINGLE_TOKEN,
        "given_name_min_births": GIVEN_NAME_MIN_BIRTHS,
        "given_name_source": "SSA baby names, all years, both sexes",
        "demonym_max_us_surname_population": DEMONYM_MAX_US_SURNAME_POPULATION,
        "demonym_labels_fetched": len(demonyms),
        "demonym_source_property": "P1549",
        "settlement_min_sitelinks": SETTLEMENT_MIN_SITELINKS,
        "settlement_max_us_surname_population": (
            SETTLEMENT_MAX_US_SURNAME_POPULATION
        ),
        "settlement_labels_fetched": len(settlements),
        "settlement_root": SETTLEMENT_ROOT,
        "ssa_names_parsed": len(ssa_births),
        "title_min_sitelinks": TITLE_MIN_SITELINKS,
        "title_min_tokens": TITLE_MIN_TOKENS,
        "title_roots": list(TITLE_ROOTS),
        "title_labels_fetched": len(titles),
        "human_labels_fetched": len(humans),
        "place_labels_fetched": len(places) + len(landmarks),
        "place_roots": list(PLACE_ROOTS),
        "landmark_roots": list(LANDMARK_ROOTS),
        # Named per-tier now that a root excluded from `place` is the root the
        # `settlement` tier is built FROM. One flat "excluded_roots" list stopped
        # being true the moment both readings of Q486972 shipped together.
        "place_excluded_roots": [SETTLEMENT_ROOT, SCHOOL_ROOT],
        "globally_excluded_roots": [SCHOOL_ROOT],
    }
    out = Path(args.out) if args.out else default_out()
    written = write_asset(out, tiers, meta)
    print(f"wrote {out} ({written:,} bytes)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
