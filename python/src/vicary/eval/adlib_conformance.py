"""``conformance/adlib.json``: adlib's contract as data, for the TypeScript and Ruby ports.

Same direction of truth as the other conformance documents: the Python
implementation is the source, this file is generated from it, and
``tools/tests/test_conformance.py`` byte-compares the committed file against a
fresh export so it cannot drift. Regenerate with ``just sync-conformance``.

Layers, smallest brick first, so a failing port learns which brick is crooked:

* ``pools``: sha256 and length of each vendored pool, so a port's copy is
  provably the same bytes;
* ``constants``: the tables a port copies verbatim (URL shapes, plugs, bands);
* ``draw`` / ``shift_date`` / ``card_plug`` / ``acronym``: the primitives;
* ``invent``: whole-document stand-in assignment, with or without the
  production oracles (gazetteer given-name and notability, lexicon stop words);
* ``restore``: putting originals back into a reply that carries stand-ins;
* ``render``: the reference arm's placeholder output over each fixture frame,
  rendered as adlib at two seeds: the end-to-end answer.
"""

from __future__ import annotations

import hashlib
from importlib import resources
from typing import Any

ADLIB_FILENAME = "adlib.json"

#: The essay ``python/tests/test_adlib.py`` exercises end to end.
ESSAY = (
    "Maria Lopez helped me study. Later Maria said Ms. Lopez was strict, but "
    "Maria's notes saved me. I yelled MARIA LOPEZ across the gym. My friend "
    "Terrence Okonkwo and Jane Quincy-Adams met mr. kowalski there. Abraham "
    "Lincoln was the topic."
)

_DOC = hashlib.sha256(b"Maria Lopez").hexdigest()
_EMPTY = hashlib.sha256(b"").hexdigest()

DRAW_CASES: list[tuple[str, str, str, str, int, int, int]] = [
    ("0", _DOC, "given", "maria", 0, 0, 1000),
    ("0", _DOC, "surname", "lopez", 0, 0, 1000),
    ("7", _DOC, "scramble", "12345", 2, 3, 10),
    ("seed", _EMPTY, "age", "14", 0, 0, 4),
    ("0", _DOC, "dob-offset", "", 0, 0, 364),
    ("3", _EMPTY, "given", "maría", 5, 1, 97),
    ("x", _DOC, "url-id", "100012345678", 255, 11, 10),
]

SHIFT_DATE_CASES: list[tuple[str, int]] = [
    ("12/31/2008", 1), ("2/29/2024", 365), ("25-12-09", 7), ("03/01/2009", 100),
    ("1/1/2000", 0), ("02/28/2023", 1), ("02/28/2024", 1), ("7-4-1776", 200),
    ("31/12/2010", 364), ("13/01/99", 45),
]

CARD_PLUG_CASES: list[tuple[str, int]] = [
    ("4012888888881881", 0), ("4012888888881881", 1), ("4012888888881881", 4),
    ("371449635398431", 0), ("5105105105105100", 2),
    ("6011000990139424", 0), ("30000000000004", 0), ("3566002020360505", 3),
    ("4000000000006", 0), ("9999888877776666", 0), ("1111111111111111", 0),
]

ACRONYM_CASES: list[str] = [
    "Lincoln High School", "Riverside Chess Club", "Oak Ridge Middle School",
    "Saint Mary's Academy", "a b c", "",
]

#: ``case_id -> (spans, document, seed, oracles, identity)``. ``oracles`` is
#: ``"production"`` (the gazetteer and lexicon :func:`render_adlib` passes) or
#: ``"none"`` (``invent``'s defaults: no stop words, no oracles).
_PERSON = ["Maria Lopez", "Maria", "Ms. Lopez", "lopez", "Maria's", "MARIA LOPEZ",
           "Lopez, Maria"]
_SIX = ["Ann Lee", "Bo Diaz", "Cy Park", "Di Wu", "Ed Kim", "Fay Roe"]


def _names(names: list[str]) -> list[tuple[str, str]]:
    return [("NAME", n) for n in names]


def _with_name(*rest: tuple[str, str]) -> list[tuple[str, str]]:
    return [("NAME", "Maria Lopez"), *rest]


INVENT_CASES: dict[str, tuple[list[tuple[str, str]], str, int | str, str, list[str]]] = {
    "name:person_consistency": (_names(_PERSON), "x", 3, "none", []),
    "name:person_consistency_production": (_names(_PERSON), ESSAY, 3, "production", []),
    "name:hyphen_and_initials": (
        _names(["Jane Quincy-Adams", "J. Smith", "K. Smith"]), "", 0, "none", []),
    "name:possessive_after_s": (_names(["James'"]), "James' bag", 0, "none", []),
    "name:six_people": (_names(_SIX), " ".join(_SIX), 0, "none", []),
    "name:six_people_production": (_names(_SIX), " ".join(_SIX), 0, "production", []),
    "name:shared_given": (_names(["Maria Lopez", "Maria Chen"]), "", 0, "none", []),
    "name:plural_possessive_with_singular": (
        _names(["Smith", "Smiths'", "James'"]), "x", 0, "none", []),
    "name:plural_possessive_alone": (_names(["Smiths'"]), "x", 0, "none", []),
    "name:identity_guard": (
        _names(["Maria Lopez"]), "x", 0, "none", ["Quinn", "Okafor Academy"]),
    "name:string_seed": (_names(["Maria Lopez", "Terrence"]), ESSAY, "seed-α", "none", []),
    "name:curly_and_modifier_apostrophes": (
        _names(["Maria’s", "Lopezʼs", "Maria"]), "x", 0, "none", []),
    "name:accented": (_names(["José Álvarez", "José"]), "José Álvarez", 0, "production", []),
    "name:cues_render_as_school_or_org": (
        _names(["Lincoln Elementary", "Riverside Chess Club", "Lincoln High"]),
        "doc", 0, "none", []),
    "case:all_caps_and_title": (
        [("NAME", "MARIA"), ("NAME", "Maria"), ("SCHOOL", "LINCOLN HIGH SCHOOL"),
         ("SCHOOL", "Lincoln High School")], "doc", 0, "none", []),
    "plug:ssn": ([("US_SOCIAL_SECURITY_NUMBER", o) for o in
                  ("123-45-6789", "987-65-4321", "123456789", "123-45-6789")],
                 "doc", 0, "none", []),
    "plug:zip": ([("ZIP_CODE", o) for o in ("94110", "10001-1234", "60614")],
                 "doc", 0, "none", []),
    "plug:phone": ([("PHONE", o) for o in ("(973) 444-1234", "+1 973-444-9999 ext. 12",
                                          "973.444.0000", "201-444-1234", "555-444-1234",
                                          "444-1234")], "doc", 0, "none", []),
    "plug:ip": ([("IP_ADDRESS", o) for o in ("10.1.2.3", "172.16.0.9")], "doc", 0, "none", []),
    "plug:card": ([("CREDIT_DEBIT_CARD_NUMBER", o) for o in
                   ("4012888888881881", "4000056655665556", "4242424242424242",
                    "3714-496353-98431", "9999888877776666")], "doc", 0, "none", []),
    "age:bands": ([("AGE", a) for a in ("5", "9", "14", "17", "26", "27", "28", "64", "90",
                                        "twelve")], "doc", 0, "none", []),
    "age:tight_band": ([("AGE", a) for a in ("14", "15", "16")], "doc", 0, "none", []),
    "dob:month_of_dates": ([("DATE_OF_BIRTH", f"03/{d:02d}/2009") for d in range(1, 29)],
                           "doc", 0, "none", []),
    "dob:formats": ([("DATE_OF_BIRTH", d) for d in ("25-12-09", "born 1/2/2010", "May 3")],
                    "doc", 0, "none", []),
    "school:acronym_follows": ([("SCHOOL", "LHS"), ("SCHOOL", "Lincoln High School")],
                               "doc", 0, "none", []),
    "school:acronym_alone": ([("SCHOOL", "RMS")], "doc", 0, "none", []),
    "place_and_org": ([("LOCATION", "North Dallas"), ("ORGANIZATION", "Lincoln Robotics Team"),
                       ("LOCATION", "Lake Tahoe"), ("LOCATION", "north dallas")],
                      "doc", 0, "production", []),
    "email": (_with_name(("EMAIL", "maria.lopez09@lincoln.org"),
                         ("EMAIL", "mlopez@gmail.com"), ("EMAIL", "zz_top@school.k12.tx.us")),
              "doc", 0, "none", []),
    "username": (_with_name(("USERNAME", "@maria_lopez"), ("USERNAME", "@MariaLopez"),
                            ("USERNAME", "xX_gamer99_Xx")), "doc", 0, "none", []),
    "url:kept_verbatim": (_with_name(*[("URL", u) for u in (
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s", "https://github.com/explore",
        "https://docs.google.com/document/d/1AbCdEf/edit#heading=h.x1",
        "https://en.wikipedia.org/wiki/Frida_Kahlo", "instagram.com/p/C1x2y3z4",
        "youtube.com/watch", "https://twitter.com/search?q=lopezia",
        "https://example.com:8080/user/mar", "https://lopezia.org/marial",
        "https://instagram.com/explore/tags/art", "https://github.com/orgs",
        "https://www.facebook.com/groups/12345", "https://twitter.com/home",
        "https://www.tiktok.com/discover", "https://www.facebook.com/profile.php")]),
        "doc", 0, "none", []),
    "url:prose_name_map": (_with_name(*[("URL", u) for u in (
        "https://lincolnhs.org/staff/lopez", "https://marialopez.net/about/maria",
        "https://en.wikipedia.org/wiki/Maria_Lopez#early-life",
        "https://twitter.com/search?q=Lopez&src=typed", "https://example.com:8080/user/maria",
        "https://lopez.com")]), "doc", 0, "none", []),
    "url:handle_before": (_with_name(("USERNAME", "@zebediah"),
                                     ("URL", "https://site.org/zebediah")), "doc", 0, "none", []),
    "url:handle_after": (_with_name(("URL", "https://site.org/zebediah"),
                                    ("USERNAME", "@zebediah")), "doc", 0, "none", []),
    "url:per_user_shapes": (_with_name(*[("URL", u) for u in (
        "instagram.com/marialopez2009", "https://www.youtube.com/@MariaLopez/videos",
        "https://github.com/mlopez/essay-repo/blob/main/README.md#intro",
        "https://www.facebook.com/profile.php?id=100012345678&ref=bm",
        "https://x.com/maria_lopez/status/1234567890", "https://maria.substack.com/p/my-essay",
        "https://www.youtube.com/channel/UCabc123XYZ")]), "doc", 0, "none", []),
    "address": ([("ADDRESS", a) for a in ("1234 N Maple Ave Apt 5B", "77 Elm Street",
                                          "9 W. Oak Blvd #12", "1600 Pennsylvania Avenue NW")],
                "doc", 0, "none", []),
    "scramble": ([(k, o) for k, o in (("DRIVER_LICENSE", "D123-4567-89"),
                                      ("PASSPORT", "X12345678"), ("OTHER", "Abc-9z"),
                                      ("STUDENT_ID", "S-000042"))], "doc", 0, "none", []),
    "exhaustion": ([("NAME", f"Q{i}x Zz{i}") for i in range(2000)], "", 0, "none", []),
}

#: ``case_id -> (text, restore_map, tokens)`` hand-built around ``ESSAY``'s render
#: are added at build time; these pin the re-casing rules on their own.
RESTORE_TOKEN_CASES: dict[str, tuple[str, dict[str, str], dict[str, str]]] = {
    "lone_token_title": ("Quinn wrote well.", {}, {"Quinn": "Maria"}),
    "lone_token_upper": ("QUINN!", {}, {"Quinn": "Maria"}),
    "lone_token_lower": ("quinn said so", {}, {"Quinn": "Maria"}),
    "single_letter_upper": ("Q was here", {}, {"Q": "M"}),
    "span_beats_token": ("Quinn Okafor and Quinn.", {"Quinn Okafor": "Maria Lopez"},
                         {"Quinn": "Maria", "Okafor": "Lopez"}),
    "inside_a_word_is_not_a_token": ("Quinnton stayed.", {}, {"Quinn": "Maria"}),
    "empty": ("", {}, {}),
}

RENDER_SEEDS: tuple[int | str, ...] = (0, 7)


def _pool_digests() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for entry in sorted(resources.files("vicary").joinpath("pools").iterdir(),
                        key=lambda p: p.name):
        if not entry.name.endswith(".txt"):
            continue
        raw = entry.read_bytes()
        from vicary.adlib import load_pool

        out[entry.name.removesuffix(".txt")] = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "entries": len(load_pool(entry.name.removesuffix(".txt"))),
        }
    return out


def _constants() -> dict[str, Any]:
    from vicary import adlib as a

    return {
        "url_shapes": [list(s) for s in a.URL_SHAPES],
        "url_reserved": sorted(a.URL_RESERVED),
        "url_user_subdomains": sorted(a.URL_USER_SUBDOMAINS),
        "providers": sorted(a.PROVIDERS),
        "ssn_plug": a.SSN_PLUG,
        "zip_plug": a.ZIP_PLUG,
        "phone_area_code": a.PHONE_AREA_CODE,
        "phone_plug": a.PHONE_PLUG,
        "ip_ranges": list(a.IP_RANGES),
        "card_plugs": [[list(prefixes), length, plug] for prefixes, length, plug in a.CARD_PLUGS],
        "age_bands": [list(b) for b in a.AGE_BANDS],
        "max_bumps": a._MAX_BUMPS,
    }


def _invent_case(case_id: str, spans: list[tuple[str, str]], document: str,
                 seed: int | str, oracles: str, identity: list[str]) -> dict[str, Any]:
    from vicary import gazetteer
    from vicary.adlib import AdlibPoolExhausted, invent
    from vicary.lexicon import stop_words

    kwargs: dict[str, Any] = {}
    if oracles == "production":
        kwargs = {"stop_words": stop_words(), "given_name": gazetteer.is_common_given_name,
                  "notable": gazetteer.is_notable}
    case: dict[str, Any] = {
        "case_id": case_id, "spans": [list(s) for s in spans], "document": document,
        "seed": seed, "oracles": oracles, "identity": identity,
    }
    try:
        result = invent(spans, document=document, seed=seed, identity=identity, **kwargs)
    except AdlibPoolExhausted:
        case["raises"] = "AdlibPoolExhausted"
        # The 2,000 inputs are generated by rule; ship the rule, not the list.
        case["spans"] = {"generator": "NAME 'Q{i}x Zz{i}' for i in 0..1999"}
        return case
    case["stand_ins"] = list(result.stand_ins)
    case["tokens"] = dict(result.tokens)
    return case


def _render_cases() -> list[dict[str, Any]]:
    from vicary.eval import fixture as fx
    from vicary.eval.conformance import REFERENCE_ARM
    from vicary.eval.recall import build_redactor
    from vicary.redaction import render_adlib

    redactor = build_redactor(REFERENCE_ARM, None)
    texts = [(f"frame:{f.frame_id}", f.sentence) for f in fx.ALL_FRAMES]
    texts.append(("essay", ESSAY))
    cases = []
    for case_id, text in texts:
        placeholder = redactor._apply(text, source="INPUT")
        for seed in RENDER_SEEDS:
            rendered = render_adlib(placeholder, text, seed=seed)
            cases.append({
                "case_id": f"{case_id}@{seed}", "seed": seed, "original": text,
                "placeholder_text": placeholder.text,
                "placeholder_restore_map": dict(placeholder.restore_map),
                "text": rendered.text,
                "restore_map": dict(rendered.restore_map),
                "adlib_map": dict(rendered.adlib_map),
                "adlib_tokens": dict(rendered.adlib_tokens),
                "spans": [[s.orig_start, s.orig_end, s.new_start, s.new_end, source]
                          for s, source in rendered.adlib_spans],
            })
    return cases


def _restore_cases(render: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from vicary.adlib import restore_adlib

    cases = []
    essay = next(c for c in render if c["case_id"] == "essay@0")
    given = essay["adlib_map"]["Maria Lopez"].split()[0]
    built = {
        "essay_round_trip": (essay["text"], essay["restore_map"], essay["adlib_tokens"]),
        "essay_lone_given": (f"{given} wrote well.", essay["restore_map"],
                             essay["adlib_tokens"]),
        "essay_lone_given_upper": (f"{given.upper()}!", {}, essay["adlib_tokens"]),
        **RESTORE_TOKEN_CASES,
    }
    for case_id, (text, restore_map, tokens) in built.items():
        cases.append({"case_id": case_id, "text": text, "restore_map": restore_map,
                      "tokens": tokens,
                      "restored": restore_adlib(text, restore_map, tokens)})
    return cases


#: Map fields whose insertion order is part of the contract (restore_adlib is
#: first-wins); dumps() sorts object keys, so they ship as [key, value] lists.
ORDERED_MAP_FIELDS = ("placeholder_restore_map", "restore_map", "adlib_map",
                      "adlib_tokens", "tokens")


def _ordered(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Emit each case's ordered map fields as [key, value] pair lists."""
    return [{k: [[mk, mv] for mk, mv in v.items()]
             if k in ORDERED_MAP_FIELDS and isinstance(v, dict) else v
             for k, v in case.items()} for case in cases]


def build_adlib_document() -> dict[str, Any]:
    """Adlib's contract as data: pools, constants, primitives, cases, answers."""
    from vicary.adlib import acronym, card_plug, draw, shift_date
    from vicary.eval.conformance import DOCUMENT_VERSION

    render = _render_cases()
    return {
        "document_version": DOCUMENT_VERSION,
        "conventions": {
            "spec": "README.md, section \"Adlib\", is the byte-level rule text",
            "draw": "NUL-join seed, document digest, purpose, key and "
                    "\"<bump>.<index>\"; sha256 the UTF-8; first 8 bytes big-endian "
                    "unsigned, mod n",
            "document_digest": "sha256 hex of the original document's UTF-8",
            "oracles": "\"production\": stop_words = lexicon stop words, given_name = "
                       "gazetteer common given name, notable = gazetteer notable; "
                       "\"none\": invent's defaults (no stop words, no oracles)",
            "seed": "an integer seed is drawn as its decimal string",
            "offsets": "render spans are [orig_start, orig_end, new_start, new_end, "
                       "original] in code points, not UTF-16 code units",
            "ordered_maps": "restore_map, placeholder_restore_map, adlib_map, "
                            "adlib_tokens and tokens are [key, value] lists in "
                            "insertion order, which restore's first-wins rule reads",
            "render": "the reference arm's placeholder output over `original`, then "
                      "render_adlib(result, original, seed) with no identity",
        },
        "pools": _pool_digests(),
        "constants": _constants(),
        "draw": [{"args": list(args), "index": draw(*args)} for args in DRAW_CASES],
        "shift_date": [{"date": d, "offset": o, "shifted": shift_date(d, o)}
                       for d, o in SHIFT_DATE_CASES],
        "card_plug": [{"digits": d, "variant": v, "plug": card_plug(d, v)}
                      for d, v in CARD_PLUG_CASES],
        "acronym": [{"name": n, "acronym": acronym(n)} for n in ACRONYM_CASES],
        "invent": _ordered([_invent_case(cid, *args)
                            for cid, args in INVENT_CASES.items()]),
        "restore": _ordered(_restore_cases(render)),
        "render": _ordered(render),
    }
