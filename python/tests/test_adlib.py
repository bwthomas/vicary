"""Adlib rendering: invented values over the placeholder mode's exact spans."""

from __future__ import annotations

import hashlib
import logging

import pytest

from vicary import (
    RENDER_ADLIB,
    AdlibPoolExhausted,
    RedactionResult,
    build_redactor_if_enabled,
    gazetteer,
    render_adlib,
    render_mode,
)
from vicary.adlib import (
    AGE_BANDS,
    CARD_PLUGS,
    GIVEN_POOL,
    PHONE_AREA_CODE,
    PHONE_PLUG,
    SSN_PLUG,
    SURNAME_POOL,
    URL_SHAPES,
    _plural,
    acronym,
    card_plug,
    draw,
    invent,
    invent_names,
    load_pool,
    restore_adlib,
    shift_date,
)
from vicary.lexicon import stop_words
from vicary.placeholders import placeholder_kind

ESSAY = (
    "Maria Lopez helped me study. Later Maria said Ms. Lopez was strict, but "
    "Maria's notes saved me. I yelled MARIA LOPEZ across the gym. My friend "
    "Terrence Okonkwo and Jane Quincy-Adams met mr. kowalski there. Abraham "
    "Lincoln was the topic."
)


def _redact(text: str, **kwargs) -> RedactionResult:
    redactor = build_redactor_if_enabled("local", **kwargs)
    assert redactor is not None
    return redactor.redact_inbound(text)


def test_spans_identical_to_placeholder_mode():
    placeholder = _redact(ESSAY)
    adlib = _redact(ESSAY, render="adlib")
    assert placeholder.spans(), "the fixture must exercise redaction"
    assert ([(s.orig_start, s.orig_end) for s in adlib.spans()]
            == [(s.orig_start, s.orig_end) for s in placeholder.spans()])
    assert adlib.original() == ESSAY
    for span in adlib.spans():
        assert adlib.text[span.new_start:span.new_end] in adlib.restore_map


def test_no_placeholders_and_public_figures_kept():
    adlib = _redact(ESSAY, render="adlib")
    assert "{" not in adlib.text
    assert "Abraham Lincoln" in adlib.text
    assert adlib.render == RENDER_ADLIB


def test_person_level_consistency_titles_possessives_case():
    out = invent_names(
        ["Maria Lopez", "Maria", "Ms. Lopez", "lopez", "Maria's", "MARIA LOPEZ",
         "Lopez, Maria"],
        document="x", seed=3,
    )
    given, surname = out["Maria Lopez"].split()
    assert given in GIVEN_POOL and surname in SURNAME_POOL
    assert out["Maria"] == given
    assert out["Ms. Lopez"] == f"Ms. {surname}"
    assert out["lopez"] == surname.lower()
    assert out["Maria's"] == f"{given}'s"
    assert out["MARIA LOPEZ"] == f"{given.upper()} {surname.upper()}"
    assert out["Lopez, Maria"] == f"{surname}, {given}"


def test_hyphenated_surname_and_initials():
    out = invent_names(["Jane Quincy-Adams", "J. Smith", "K. Smith"], document="", seed=0)
    given, double = out["Jane Quincy-Adams"].split()
    left, right = double.split("-")
    assert given in GIVEN_POOL and left in SURNAME_POOL and right in SURNAME_POOL
    assert out["J. Smith"] != out["K. Smith"]
    assert out["J. Smith"][1] == "." and out["J. Smith"][0] not in "JK"


def test_possessive_after_s():
    out = invent_names(["James'"], document="James' bag", seed=0)
    stand_in = out["James'"]
    assert stand_in.endswith("'") if stand_in[:-1].endswith("s") else stand_in.endswith("'s")


def test_unique_people_and_injective():
    names = ["Ann Lee", "Bo Diaz", "Cy Park", "Di Wu", "Ed Kim", "Fay Roe"]
    out = invent_names(names, document=" ".join(names), seed=0)
    assert len(set(out.values())) == len(names)
    tokens = [t for v in out.values() for t in v.split()]
    assert len(set(tokens)) == len(tokens)


def test_shared_given_name_stays_shared():
    out = invent_names(["Maria Lopez", "Maria Chen"], document="", seed=0)
    assert out["Maria Lopez"].split()[0] == out["Maria Chen"].split()[0]
    assert out["Maria Lopez"] != out["Maria Chen"]


def test_no_collision_with_document_words():
    seed_out = invent_names(["Maria"], document="Maria", seed=0)["Maria"]
    # Put the would-be stand-in into the document: it must be avoided.
    document = f"Maria met {seed_out} and {seed_out.lower()}."
    out = invent_names(["Maria"], document=document, seed=0)
    assert out["Maria"] != seed_out


def test_deterministic_and_seeded():
    assert _redact(ESSAY, render="adlib", adlib_seed=7).text == \
        _redact(ESSAY, render="adlib", adlib_seed=7).text
    assert _redact(ESSAY, render="adlib", adlib_seed=7).text != \
        _redact(ESSAY, render="adlib", adlib_seed=8).text


def test_seed_from_environment(monkeypatch):
    monkeypatch.setenv("VICARY_ADLIB_SEED", "7")
    monkeypatch.setenv("VICARY_RENDER", "adlib")
    redactor = build_redactor_if_enabled("local")
    assert redactor is not None and redactor.render == RENDER_ADLIB
    assert redactor.redact_inbound(ESSAY).text == \
        _redact(ESSAY, render="adlib", adlib_seed=7).text


def test_render_default_is_placeholder(monkeypatch):
    monkeypatch.delenv("VICARY_RENDER", raising=False)
    assert render_mode() == "placeholder"
    assert "{NAME_" in _redact(ESSAY).text


def test_adlib_refuses_modes_without_a_restore_map():
    with pytest.raises(ValueError):
        build_redactor_if_enabled("stub", render="adlib")
    with pytest.raises(ValueError):
        render_adlib(RedactionResult("hi {NAME}", True, 0), "hi Bo")


def test_mapping_never_logged(caplog):
    with caplog.at_level(logging.DEBUG):
        result = _redact(ESSAY, render="adlib")
    assert result.adlib_map
    for original, stand_in in result.adlib_map.items():
        assert original not in caplog.text
        assert stand_in not in caplog.text
    assert "adlib_map" not in repr(result)


def test_rerunning_vicary_redacts_the_stand_ins():
    """Idempotence: a second pass recognises every stand-in as a name."""
    first = _redact(ESSAY, render="adlib")
    second = _redact(first.text)
    assert len(second.spans()) == len(first.spans())


def test_pool_exhaustion_raises():
    many = [f"Q{i}x Zz{i}" for i in range(len(GIVEN_POOL) + 1)]
    with pytest.raises(AdlibPoolExhausted):
        invent_names(many, document="", seed=0)


@pytest.mark.parametrize("name", GIVEN_POOL)
def test_given_pool_entries_are_safe(name):
    assert gazetteer.is_common_given_name(name)
    assert not gazetteer.is_notable(name)
    assert not gazetteer.is_settlement(name)
    assert not gazetteer.is_title(name)
    assert name.lower() not in stop_words()


@pytest.mark.parametrize("name", SURNAME_POOL)
def test_surname_pool_entries_are_safe(name):
    assert not gazetteer.is_common_given_name(name)
    assert not gazetteer.is_notable(name)
    assert not gazetteer.is_settlement(name)
    assert not gazetteer.is_title(name)
    assert name.lower() not in stop_words()


def test_pools_are_disjoint_and_unique():
    assert len(set(GIVEN_POOL)) == len(GIVEN_POOL)
    assert len(set(SURNAME_POOL)) == len(SURNAME_POOL)
    assert not set(GIVEN_POOL) & set(SURNAME_POOL)


# -- every other kind -----------------------------------------------------------

def _one(kind: str, original: str, document: str = "doc", **kwargs) -> str:
    return invent([(kind, original)], document=document, **kwargs).stand_ins[0]


def _kinds(spans, document: str = "doc", **kwargs) -> list[str]:
    return list(invent(spans, document=document, **kwargs).stand_ins)


# The port contract: inputs -> index. The TypeScript and Ruby ports copy these.
DOC = hashlib.sha256(b"Maria Lopez").hexdigest()


@pytest.mark.parametrize(("args", "expected"), [
    (("0", DOC, "given", "maria", 0, 0, 1000), 885),
    (("0", DOC, "surname", "lopez", 0, 0, 1000), 882),
    (("7", DOC, "scramble", "12345", 2, 3, 10), 6),
    (("seed", hashlib.sha256(b"").hexdigest(), "age", "14", 0, 0, 4), 0),
])
def test_draw_test_vectors(args, expected):
    assert DOC == "ec7dafcd351fe5964d3cc607a9e5b12d036ea6cac582fa22dc70a48330d8dd2f"
    assert draw(*args) == expected
    # Spelled out: NUL-joined, sha256, first 8 bytes big-endian, mod n.
    seed, digest, purpose, key, bump, index, n = args
    material = "\0".join((seed, digest, purpose, key, f"{bump}.{index}")).encode()
    assert int.from_bytes(hashlib.sha256(material).digest()[:8], "big") % n == expected


@pytest.mark.parametrize(("kind", "originals", "plugs"), [
    ("US_SOCIAL_SECURITY_NUMBER", ["123-45-6789", "987-65-4321", "123456789"],
     ["799-99-9999", "799-99-9998", "799999997"]),
    ("ZIP_CODE", ["94110", "10001-1234", "60614"], ["99999", "99998-9999", "99997"]),
    ("PHONE", ["(973) 444-1234", "+1 973-444-9999 ext. 12", "973.444.0000"],
     ["(201) 555-0100", "+1 201-555-0101 ext. 12", "201.555.0102"]),
    ("IP_ADDRESS", ["10.1.2.3", "172.16.0.9"], ["192.0.2.1", "192.0.2.2"]),
    ("CREDIT_DEBIT_CARD_NUMBER", ["4012888888881881", "4000056655665556"],
     ["4111111111111111", "4111111111111103"]),
])
def test_plugs_are_pinned(kind, originals, plugs):
    assert _kinds([(kind, o) for o in originals]) == plugs


@pytest.mark.parametrize(("original", "plug"), [
    ("4012 8888 8888 1881", "4111 1111 1111 1111"), ("3714-496353-98431", "3782-822463-10005"),
    ("5105105105105100", "5555555555554444"), ("6011000990139424", "6011111111111117"),
    ("30000000000004", "30569309025904"), ("3566002020360505", "3530111333300000"),
    ("4000000000006", "4222222222222"), ("9999888877776666", "4111111111111111"),
])
def test_card_plug_by_brand(original, plug):
    assert _one("CREDIT_DEBIT_CARD_NUMBER", original) == plug


def test_phone_plug_never_keeps_the_area_code_or_uses_555():
    assert PHONE_PLUG == "201-555-0100" and PHONE_AREA_CODE == "201"
    assert _one("PHONE", "201-444-1234").startswith("201-555-01")  # fixed, not echoed
    assert not _one("PHONE", "555-444-1234").startswith("555-555")
    assert _one("PHONE", "444-1234") == "555-0100"


def test_plug_variants_are_distinct_and_repeat_for_one_value():
    out = _kinds([("US_SOCIAL_SECURITY_NUMBER", "123-45-6789"),
                  ("US_SOCIAL_SECURITY_NUMBER", "222-33-4444"),
                  ("US_SOCIAL_SECURITY_NUMBER", "123-45-6789")])
    assert out == [SSN_PLUG, "799-99-9998", SSN_PLUG]
    cards = _kinds([("CREDIT_DEBIT_CARD_NUMBER", c)
                    for c in ("4012888888881881", "4000056655665556", "4242424242424242")])
    assert cards[0] == "4111111111111111" and len(set(cards)) == 3


@pytest.mark.parametrize("text", [
    "My SSN is 799-99-9999.", "Card 4111111111111111 on file.",
    "Card 378282246310005 on file.", "Card 5555555555554444 on file.",
    "Card 6011111111111117 on file.", "Card 30569309025904 on file.",
    "Card 3530111333300000 on file.", "Card 4222222222222 on file.",
    "Call (201) 555-0100 now.", "From 192.0.2.1 today.", "zip 99999-9999",
])
def test_every_plug_redetects_as_its_kind(text):
    kinds = {placeholder_kind(t) for t in _redact(text).restore_map}
    assert len(kinds) == 1


def test_plugs_redetect_on_a_second_pass_over_adlib_output():
    text = ("SSN 123-45-6789, card 4012 8888 8888 1881, call (973) 444-1234, "
            "IP 10.1.2.3, zip 94110-1234.")
    first = _redact(text, render="adlib")
    second = _redact(first.text)
    assert (sorted(placeholder_kind(t) for t in second.restore_map)
            == sorted(placeholder_kind(t) for t in _redact(text).restore_map))


@pytest.mark.parametrize("brand", [plug for _, _, plug in CARD_PLUGS])
def test_card_plugs_are_luhn_valid(brand):
    def luhn_ok(digits: str) -> bool:
        total = 0
        for i, ch in enumerate(reversed(digits)):
            d = int(ch) * (2 if i % 2 else 1)
            total += d - 9 if d > 9 else d
        return total % 10 == 0
    for variant in range(5):
        assert luhn_ok(card_plug(brand, variant))
    assert luhn_ok(card_plug("1" * 16, 0))  # unknown brand fallback


@pytest.mark.parametrize(("lo", "hi"), AGE_BANDS)
def test_age_stays_in_its_band_and_moves(lo, hi):
    for age in {lo, hi, (lo + hi) // 2}:
        out = int(_one("AGE", str(age)))
        assert lo <= out <= hi and out != age


def test_ages_are_distinct_and_avoid_other_originals():
    out = _kinds([("AGE", "26"), ("AGE", "27"), ("AGE", "28")])
    assert len(set(out)) == 3 and not set(out) & {"26", "27", "28"}
    # A narrow band runs out of free values: still distinct, still in band.
    tight = _kinds([("AGE", "14"), ("AGE", "15"), ("AGE", "16")])
    assert len(set(tight)) == 3 and all(14 <= int(a) <= 18 for a in tight)
    assert all(a != o for a, o in zip(tight, ("14", "15", "16"), strict=True))


def test_dob_keeps_year_and_format_and_never_lands_on_an_original():
    dobs = [f"03/{d:02d}/2009" for d in range(1, 29)]
    out = _kinds([("DATE_OF_BIRTH", d) for d in dobs])
    assert all(o.endswith("/2009") and len(o) == 10 for o in out)
    assert not set(out) & set(dobs)
    assert len(set(out)) == len(out)
    assert shift_date("12/31/2008", 1) == "01/01/2008"  # wraps inside its year
    assert shift_date("2/29/2024", 365) == "2/28/2024"
    assert shift_date("25-12-09", 7) == "01-01-09"  # day first, 2-digit year


def test_school_stem_and_acronym_follow_the_full_name():
    out = _kinds([("SCHOOL", "LHS"), ("SCHOOL", "Lincoln High School")])
    assert out[1].endswith(" High School")
    assert out[1].split(" High")[0] in load_pool("school")
    assert out[0] == acronym(out[1]) != "LHS"


def test_name_cues_render_as_school_or_org():
    school, org, person = _kinds([("NAME", "Lincoln Elementary"),
                                  ("NAME", "Riverside Chess Club"),
                                  ("NAME", "Lincoln High")])
    assert school.endswith(" Elementary") and "Lincoln" not in school
    assert school.split(" Elementary")[0] in load_pool("school")
    assert org.endswith(" Club") and org.removesuffix(" Club") in load_pool("org")
    assert person.endswith(" High")


def test_email_rules():
    name, school, provider = _kinds([("NAME", "Maria Lopez"),
                                     ("EMAIL", "maria.lopez09@lincoln.org"),
                                     ("EMAIL", "mlopez@gmail.com")])
    given, surname = name.split()
    assert school == f"{given.lower()}.{surname.lower()}09@example.org"
    local, _, domain = provider.partition("@")
    assert domain == "gmail.com" and local != "mlopez" and local.isalpha()


def test_handle_reuses_the_name_map():
    name, handle, joined = _kinds([("NAME", "Maria Lopez"), ("USERNAME", "@maria_lopez"),
                                   ("USERNAME", "@MariaLopez")])
    given, surname = name.split()
    assert handle == f"@{given.lower()}_{surname.lower()}"
    assert joined == f"@{given}{surname}"


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s",
    "https://github.com/explore",
    "https://docs.google.com/document/d/1AbCdEf/edit#heading=h.x1",
    "https://en.wikipedia.org/wiki/Frida_Kahlo",
    "instagram.com/p/C1x2y3z4",
    "youtube.com/watch",
    "https://twitter.com/search?q=lopezia",
    "https://example.com:8080/user/mar",
    "https://lopezia.org/marial",
])
def test_url_that_is_not_a_per_user_shape_is_kept_verbatim(url):
    """Blake 11-04: a URL off the shape list keeps its host, path, query and
    fragment as written when no whole word in it is a prose-mapped name; a name
    inside a longer word, a short fragment, or the TLD never changes."""
    _, out = _kinds([("NAME", "Maria Lopez"), ("URL", url)])
    assert out == url


def test_off_list_url_takes_the_prose_name_map():
    """Blake 11-04: lincolnhs.org/staff/lopez must not keep the name the prose lost."""
    name, *urls = _kinds([
        ("NAME", "Maria Lopez"),
        ("URL", "https://lincolnhs.org/staff/lopez"),
        ("URL", "https://marialopez.net/about/maria"),
        ("URL", "https://en.wikipedia.org/wiki/Maria_Lopez#early-life"),
        ("URL", "https://twitter.com/search?q=Lopez&src=typed"),
        ("URL", "https://example.com:8080/user/maria"),
        ("URL", "https://lopez.com"),
    ])
    given, surname = name.split()
    staff, site, wiki, search, port, domain = urls
    assert staff == f"https://lincolnhs.org/staff/{surname.lower()}"
    assert site == f"https://{given.lower()}{surname.lower()}.net/about/{given.lower()}"
    assert wiki == f"https://en.wikipedia.org/wiki/{given}_{surname}#early-life"
    assert search == f"https://twitter.com/search?q={surname}&src=typed"
    assert port == f"https://example.com:8080/user/{given.lower()}"
    assert domain == f"https://{surname.lower()}.com"


def test_off_list_url_ignores_names_first_met_outside_the_prose():
    """Only the prose map applies, so a URL's output does not depend on whether a
    handle carrying a new token came before it in the document."""
    first = _kinds([("NAME", "Maria Lopez"), ("USERNAME", "@zebediah"),
                    ("URL", "https://site.org/zebediah")])
    second = _kinds([("NAME", "Maria Lopez"), ("URL", "https://site.org/zebediah"),
                     ("USERNAME", "@zebediah")])
    assert first[2] == second[1] == "https://site.org/zebediah"


def test_per_user_url_shapes_change_only_the_handle_or_id():
    name, *urls = _kinds([
        ("NAME", "Maria Lopez"),
        ("URL", "instagram.com/marialopez2009"),
        ("URL", "https://www.youtube.com/@MariaLopez/videos"),
        ("URL", "https://github.com/mlopez/essay-repo/blob/main/README.md#intro"),
        ("URL", "https://www.facebook.com/profile.php?id=100012345678&ref=bm"),
        ("URL", "https://x.com/maria_lopez/status/1234567890"),
        ("URL", "https://maria.substack.com/p/my-essay"),
        ("URL", "https://www.youtube.com/channel/UCabc123XYZ"),
    ])
    given, surname = name.split()
    insta, yt, gh, fb, x, sub, chan = urls
    assert insta.startswith(f"instagram.com/{given.lower()}{surname.lower()}")
    assert "2009" not in insta and insta[-4:].isdigit()
    assert yt == f"https://www.youtube.com/@{given}{surname}/videos"
    assert gh.startswith("https://github.com/") and "mlopez" not in gh
    assert gh.endswith("/essay-repo/blob/main/README.md#intro")
    assert fb.startswith("https://www.facebook.com/profile.php?id=") and fb.endswith("&ref=bm")
    fb_id = fb.split("id=")[1].split("&")[0]
    assert fb_id != "100012345678" and len(fb_id) == 12 and fb_id.isdigit()
    assert x.startswith(f"https://x.com/{given.lower()}_{surname.lower()}/status/")
    assert "1234567890" not in x and len(x.rsplit("/", 1)[1]) == 10
    assert sub.endswith(".substack.com/p/my-essay") and not sub.startswith("https://maria.")
    chan_id = chan.rsplit("/", 1)[1]
    assert chan_id != "UCabc123XYZ" and len(chan_id) == len("UCabc123XYZ")


@pytest.mark.parametrize("url", [
    "https://instagram.com/explore/tags/art", "https://github.com/orgs",
    "https://www.facebook.com/groups/12345", "https://twitter.com/home",
    "https://www.tiktok.com/discover", "https://www.facebook.com/profile.php",
])
def test_a_site_page_in_a_handle_slot_is_not_a_handle(url):
    _, out = _kinds([("NAME", "Maria Lopez"), ("URL", url)])
    assert out == url


def test_url_shapes_are_well_formed():
    """Ports copy URL_SHAPES verbatim; pin its grammar so a typo cannot slip in."""
    for host, pattern in URL_SHAPES:
        assert host == host.lower() and not host.startswith(("www.", "m."))
        path, _, query = pattern.partition("?")
        assert path.startswith("/")
        assert any(s in ("{h}", "@{h}", "{i}") for s in path.split("/")) or query
        assert not query or query.endswith("={i}")


def test_address_keeps_shape():
    out = _one("ADDRESS", "1234 N Maple Ave Apt 5B")
    number, direction, *street, suffix, apt, unit = out.split()
    assert len(number) == 4 and number[0] != "0" and number != "1234"
    assert (direction, suffix, apt) == ("N", "Ave", "Apt")
    assert " ".join(street) in load_pool("street") and street != ["Maple"]
    assert len(unit) == 2 and unit[0].isdigit() and unit[1].isupper()


def test_plural_possessive_with_its_singular_present():
    out = invent_names(["Smith", "Smiths'", "James'"], document="x", seed=0)
    assert out["Smiths'"] == _plural(out["Smith"]) + "'"
    assert out["James'"].endswith(("s'", "'s"))
    assert not out["James'"].endswith("s's")


def test_plural_possessive_without_its_singular_is_read_singular():
    """Known gap, pinned: with no "Smith" key, "Smiths'" cannot be told from
    "Jones'" or "James'", so it maps as one name token with a singular possessive."""
    out = invent_names(["Smiths'"], document="x", seed=0)
    stand_in = out["Smiths'"].rstrip("'s")
    assert stand_in in GIVEN_POOL + SURNAME_POOL
    assert out["Smiths'"] == stand_in + ("'" if stand_in.endswith("s") else "'s")


def test_all_caps_and_title_case_follow_the_original():
    out = _kinds([("NAME", "MARIA"), ("NAME", "Maria"), ("SCHOOL", "LINCOLN HIGH SCHOOL"),
                  ("SCHOOL", "Lincoln High School")])
    assert out[0] == out[1].upper() and out[0] != out[1]
    assert out[2] == out[3].upper()


def test_identity_words_are_never_handed_out():
    free = invent_names(["Maria Lopez"], document="x", seed=0)["Maria Lopez"]
    given, surname = free.split()
    guarded = invent_names(["Maria Lopez"], document="x", seed=0,
                           identity=[given, f"{surname} Academy"])["Maria Lopez"]
    assert given not in guarded.split() and surname not in guarded.split()


def test_restore_adlib_round_trip_and_a_lone_token():
    result = _redact(ESSAY, render="adlib")
    assert restore_adlib(result.text, result.restore_map, result.adlib_tokens) == ESSAY
    stand_in = result.adlib_map["Maria Lopez"]
    given = stand_in.split()[0]
    assert restore_adlib(f"{given} wrote well.", result.restore_map,
                         result.adlib_tokens) == "Maria wrote well."
    assert restore_adlib(f"{given.upper()}!", {}, result.adlib_tokens) == "MARIA!"
