"""Regenerate the adlib stand-in pools in ``python/src/vicary/pools/``.

Run by hand, never by a test; the diff of the pool files is what gets reviewed.
The runtime reads only the written files, so no network and no build input is
needed to redact. The ports vendor the same bytes.

Inputs, all local:

* the SSA baby-names archive (``names.zip``; ``--ssa`` or
  ``VICARY_BUILD_SSA_NAMES_ZIP``) — the given-name pool is every name born
  1980-2024 with at least 3,000 bearers and a female share in [0.2, 0.8], so a
  stand-in agrees with either pronoun the essay uses;
* ``conformance/census/surnames.txt.gz`` (in this repository) — every curated
  surname must have at least 100 bearers in the 2010 Census, so it is a real
  American surname and not an invention;
* a word list (``--words``, default ``/usr/share/dict/words``) — a lowercase
  entry there is an ordinary English word, and no person-name stand-in may be one;
* the shipped gazetteer and stoplist, through the installed ``vicary`` package.

Every entry of every pool passes the same safety predicates the tests assert
(``python/tests/test_adlib.py``): not notable, not a settlement, not a published
title, not a stop word. Given names must sit in the gazetteer's common-given-name
tier, so a second vicary pass over adlib output redacts the stand-ins; surnames
must not, so a surname stand-in reads as a surname.

    cd python && .venv/bin/python ../tools/adlib_pools_build.py
"""

from __future__ import annotations

import argparse
import collections
import gzip
import hashlib
import os
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "python" / "src" / "vicary" / "pools"
CENSUS = REPO / "conformance" / "census" / "surnames.txt.gz"

SSA_YEARS = range(1980, 2025)
SSA_MIN_BEARERS = 3000
SSA_FEMALE_SHARE = (0.2, 0.8)
CENSUS_MIN_BEARERS = 100

#: Unisex SSA names that pass every predicate but read as a famous place, brand,
#: deity or institution — "Dallas helped me" reads, "Krishna helped me" in an
#: essay about religion would not, and "Baylor" is a university.
GIVEN_EXCLUDED = frozenset({
    "Ali", "Aries", "Armani", "Artemis", "Baylor", "Brighton", "Campbell",
    "Charleston", "Dallas", "Denver", "Dior", "Everest", "Harlem", "Krishna",
    "Milan", "Osiris", "Raleigh", "Salem", "Santana",
})

#: Curated surnames by origin. Validated, not trusted: an entry that fails the
#: census floor or a predicate is dropped and reported, never shipped.
SURNAMES: dict[str, tuple[str, ...]] = {
    "european": (
        "Abernathy", "Abrams", "Achterberg", "Albescu", "Ashworth", "Bergstrom",
        "Brightwell", "Calloway", "Cavanaugh", "Coldwell", "Corrigan", "Dalca",
        "Darrow", "Demir", "Dunmore", "Eastman", "Eckhart", "Eriksen",
        "Esposito", "Fairley", "Fairweather", "Falk", "Fenwick", "Ferreira",
        "Fonseca", "Garroway", "Gilmore", "Grimaldi", "Haddad", "Halvorsen",
        "Hargrove", "Hendricks", "Holcomb", "Holloway", "Iversen", "Jankowski",
        "Jessop", "Jessup", "Jovanovic", "Kaminski", "Kessler", "Kilbride",
        "Kincaid", "Kowalczyk", "Lachance", "Langley", "Laursen", "Lindahl",
        "Lindqvist", "Lockhart", "Lombardi", "Lundqvist", "Marchetti",
        "Mayfield", "Merriweather", "Moreau", "Nesbitt", "Nilsen", "Nordahl",
        "Northcott", "Novak", "Nygaard", "Oakes", "Osgood", "Ostrowski",
        "Pemberton", "Pendleton", "Petrakis", "Pietrangeli", "Pruitt",
        "Quigley", "Rademacher", "Rasmussen", "Rinaldi", "Rourke", "Sorensen",
        "Stavros", "Szabo", "Thornbury", "Thorsen", "Toivonen", "Tolliver",
        "Tremblay", "Upton", "Urquhart", "Varga", "Vickers", "Vickery",
        "Vukovic", "Westergaard", "Wexler", "Whitaker", "Whitfield", "Whitlock",
        "Wojcik", "Yardley", "Yilmaz", "Zielinski",
    ),
    "hispanic": (
        "Acosta", "Aguilar", "Aldana", "Alvarado", "Avila", "Barrera", "Barros",
        "Bautista", "Benavides", "Cabrera", "Calderon", "Camacho", "Cardenas",
        "Castillo", "Cervantes", "Contreras", "Cordova", "Delgado", "Dominguez",
        "Escobedo", "Espinoza", "Estrada", "Figueroa", "Fuentes", "Gallardo",
        "Galvez", "Guerrero", "Gutierrez", "Guzman", "Ibarra", "Iglesias",
        "Jaramillo", "Jimenez", "Juarez", "Lozano", "Maldonado", "Marquez",
        "Mejia", "Mendoza", "Montoya", "Navarro", "Ochoa", "Orozco", "Ortega",
        "Pacheco", "Padilla", "Paredes", "Quintero", "Quiroga", "Rosales",
        "Salazar", "Sandoval", "Serrano", "Solano", "Trevino", "Trujillo",
        "Ulloa", "Uribe", "Valdez", "Vasquez", "Velasquez", "Villanueva",
        "Zamora", "Zepeda", "Zuniga",
    ),
    "south_asian": (
        "Agarwal", "Bajwa", "Banerjee", "Bhatt", "Bhattacharya", "Chatterjee",
        "Chaudhry", "Chowdhury", "Desai", "Dhillon", "Dutta", "Ghosh", "Grewal",
        "Gurung", "Iyer", "Jayawardena", "Joshi", "Kapoor", "Karki", "Khanna",
        "Krishnan", "Kulkarni", "Mehta", "Menon", "Mishra", "Mukherjee",
        "Naidu", "Nair", "Pandey", "Patil", "Perera", "Pillai", "Qureshi",
        "Raman", "Reddy", "Sandhu", "Saxena", "Shrestha", "Siddiqui", "Sidhu",
        "Sinha", "Srivastava", "Subramanian", "Thakur", "Thapa", "Tiwari",
        "Trivedi", "Venkatesan", "Verma",
    ),
    "east_asian": (
        "Aoki", "Bui", "Cheng", "Choi", "Dang", "Deng", "Duong", "Endo",
        "Fujita", "Fukuda", "Hasegawa", "Hashimoto", "Hayashi", "Hoang",
        "Huang", "Hwang", "Ikeda", "Ishikawa", "Jiang", "Kang", "Kobayashi",
        "Kondo", "Kwon", "Liang", "Matsuda", "Miura", "Murakami", "Nakajima",
        "Nakamura", "Nguyen", "Nishimura", "Ogawa", "Okada", "Peng", "Pham",
        "Phan", "Sakamoto", "Shimizu", "Suzuki", "Takahashi", "Takeda",
        "Takeuchi", "Tanaka", "Tran", "Watanabe", "Xiao", "Yamada", "Yamamoto",
        "Yoon", "Yoshida", "Yuan", "Zhang", "Zhao", "Zheng", "Zhou",
    ),
    "african": (
        "Abebe", "Adebayo", "Adeyemi", "Afolabi", "Agyeman", "Akinola", "Alemu",
        "Amoah", "Ansah", "Appiah", "Asante", "Babatunde", "Balogun", "Bekele",
        "Boateng", "Chukwu", "Danso", "Diallo", "Dlamini", "Gebre", "Girma",
        "Ibekwe", "Kamau", "Kariuki", "Keita", "Khumalo", "Mekonnen", "Mensah",
        "Mokoena", "Mwangi", "Ndiaye", "Ndlovu", "Njoroge", "Nkosi",
        "Nwachukwu", "Nwosu", "Ochieng", "Odhiambo", "Ogunleye", "Okafor",
        "Okeke", "Okonjo", "Okoro", "Oladipo", "Olawale", "Onyango", "Osei",
        "Otieno", "Owusu", "Oyelaran", "Tadesse", "Tesfaye", "Traore",
        "Warsame",
    ),
}

#: Proper-noun heads for an invented school ("Maple Ridge" Elementary) and an
#: invented organisation ("Bright Path" Foundation). The original's descriptive
#: words — Elementary, High School, Club — are kept; only the head is invented.
SCHOOL_STEMS = (
    "Aspen Valley", "Birch Meadow", "Bluff Creek", "Briar Glen", "Brookside",
    "Canyon Ridge", "Cedar Grove", "Copper Hills", "Deer Run", "Eagle Crest",
    "Elk Meadow", "Fern Hollow", "Fox Run", "Heron Pointe", "Hidden Oaks",
    "Juniper Hill", "Lark Meadow", "Maple Ridge", "Meadowbrook", "Mill Pond",
    "North Hollow", "Oak Hollow", "Pine Bluff Trail", "Prairie Wind",
    "Quail Hollow", "Red Fox", "River Birch", "Sage Canyon", "Silver Creek",
    "Spring Meadow", "Stone Bridge", "Summit View", "Sycamore Glen",
    "Timber Ridge", "Twin Oaks", "Walnut Grove", "Willow Creek", "Wren Hollow",
)
ORG_STEMS = (
    "Beacon Hill", "Blue Heron", "Bluebird", "Bright Path", "Clover Leaf",
    "Cornerstone", "Crossroads", "Evergreen", "Fieldstone", "Golden Leaf",
    "Harbor Light", "Ironwood", "Keystone", "Kestrel", "Lantern House",
    "Larkspur", "Northstar", "Open Door", "Pinecrest", "Redwood", "Riverstone",
    "Silver Oak", "Starling", "Thistle", "Trailhead", "True North", "Westwind",
    "Wildflower",
)
#: Invented-sounding towns: every prefix x suffix that is not a known
#: settlement, not notable, not a given name and not an English word.
PLACE_PREFIXES = (
    "Amber", "Ash", "Birch", "Bramble", "Briar", "Cedar", "Clay", "Copper",
    "Elm", "Fern", "Glen", "Hazel", "Heather", "Holly", "Iron", "Ivy", "Larch",
    "Maple", "Mill", "Moss", "Oak", "Pine", "Rose", "Sage", "Stone", "Thorn",
    "Willow", "Wren",
)
PLACE_SUFFIXES = (
    "brook", "bury", "crest", "dale", "field", "ford", "gate", "haven", "hurst",
    "leigh", "mere", "mont", "ridge", "stead", "vale", "wick", "worth",
)
STREET_STEMS = (
    "Alder", "Aspen", "Birch", "Briar", "Brookside", "Cedar", "Chestnut",
    "Clover", "Cypress", "Dogwood", "Elm", "Hawthorn", "Hazel", "Hemlock",
    "Hickory", "Hillcrest", "Juniper", "Lakeview", "Larch", "Laurel",
    "Linden", "Magnolia", "Maple", "Meadow", "Mulberry", "Oak", "Orchard",
    "Pine", "Poplar", "Quail Run", "Ridge", "Sassafras", "Spruce", "Sunset",
    "Sycamore", "Valley", "Walnut", "Willow",
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _unsafe(name: str, gazetteer, stops: frozenset[str]) -> list[str]:
    """Why a pool entry is unsafe, per word; empty means it may ship."""
    why = []
    for word in name.split():
        if gazetteer.is_notable(word):
            why.append("notable")
        if gazetteer.is_settlement(word):
            why.append("settlement")
        if gazetteer.is_title(word):
            why.append("title")
        if word.lower() in stops:
            why.append("stop word")
    if " " in name and (gazetteer.is_notable(name) or gazetteer.is_settlement(name)):
        why.append("whole is known")
    return why


def given_pool(ssa: Path, words: set[str], gazetteer, stops) -> list[str]:
    female: collections.Counter[str] = collections.Counter()
    male: collections.Counter[str] = collections.Counter()
    with zipfile.ZipFile(ssa) as z:
        for year in SSA_YEARS:
            for line in z.read(f"yob{year}.txt").decode("utf-8").splitlines():
                name, sex, count = line.split(",")
                (female if sex == "F" else male)[name] += int(count)
    lo, hi = SSA_FEMALE_SHARE
    out = []
    for name in sorted(set(female) | set(male)):
        total = female[name] + male[name]
        if total < SSA_MIN_BEARERS or not lo <= female[name] / total <= hi:
            continue
        if name in GIVEN_EXCLUDED or name.lower() in words:
            continue
        if not gazetteer.is_common_given_name(name) or _unsafe(name, gazetteer, stops):
            continue
        out.append(name)
    return out


def surname_pool(words: set[str], gazetteer, stops) -> list[str]:
    with gzip.open(CENSUS, "rt", encoding="utf-8") as fh:
        census = {k: int(v) for k, v in (line.split("\t") for line in fh)}
    out, dropped = set(), []
    for origin, names in SURNAMES.items():
        for name in names:
            why = _unsafe(name, gazetteer, stops)
            if census.get(name.lower(), 0) < CENSUS_MIN_BEARERS:
                why.append("census<100")
            if gazetteer.is_common_given_name(name):
                why.append("given tier")
            if name.lower() in words:
                why.append("English word")
            if len(name) < 3:
                why.append("too short")
            if why:
                dropped.append(f"{origin}:{name} ({', '.join(why)})")
            else:
                out.add(name)
    for line in dropped:
        print(f"dropped surname {line}", file=sys.stderr)
    return sorted(out)


def stems(candidates, gazetteer, stops, words=None) -> list[str]:
    out = []
    for name in candidates:
        why = _unsafe(name, gazetteer, stops)
        if words is not None and (name.lower() in words
                                  or gazetteer.is_common_given_name(name)):
            why.append("word or given")
        if why:
            print(f"dropped stem {name} ({', '.join(why)})", file=sys.stderr)
        else:
            out.append(name)
    return sorted(set(out))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--ssa", type=Path,
                        default=os.environ.get("VICARY_BUILD_SSA_NAMES_ZIP"))
    parser.add_argument("--words", type=Path, default=Path("/usr/share/dict/words"))
    args = parser.parse_args(argv)
    if not args.ssa or not Path(args.ssa).is_file():
        parser.error("needs the SSA names.zip: --ssa or VICARY_BUILD_SSA_NAMES_ZIP")

    from vicary import gazetteer
    from vicary.lexicon import stop_words

    stops = stop_words()
    words = {w.strip() for w in args.words.read_text().splitlines() if w[:1].islower()}
    pools = {
        "given.txt": given_pool(Path(args.ssa), words, gazetteer, stops),
        "surname.txt": surname_pool(words, gazetteer, stops),
        "school.txt": stems(SCHOOL_STEMS, gazetteer, stops),
        "org.txt": stems(ORG_STEMS, gazetteer, stops),
        "place.txt": stems((p + s for p in PLACE_PREFIXES for s in PLACE_SUFFIXES),
                           gazetteer, stops, words),
        "street.txt": stems(STREET_STEMS, gazetteer, stops),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    for name, entries in pools.items():
        (OUT / name).write_text("".join(f"{e}\n" for e in entries), encoding="utf-8")
        print(f"{name}: {len(entries)} entries, sha256 {_sha(OUT / name)}")
    print(f"inputs: ssa sha256 {_sha(Path(args.ssa))}, words sha256 "
          f"{_sha(args.words)}, census sha256 {_sha(CENSUS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
