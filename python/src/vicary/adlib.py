"""Adlib: stand an invented value in for every redacted span, instead of a placeholder.

Placeholders are the right shape for a model and the wrong one for a person. A
human grader reading ``{NAME_3} told {NAME_1} that …`` is reading a form, and an
A/B arm that wants to know whether placeholders move a score needs a control in
which the identifiers are gone but the prose is intact. Adlib is that control:
every span the detector would have replaced with a ``{KIND_n}`` placeholder is
replaced with an invented value of the same kind instead.

**It is a rendering mode, not a detector.** It runs over the finished
placeholder output and its restore map, so the set of spans redacted is the
placeholder mode's set exactly — same recall, same over-firing. Nothing here
decides whether something is a name.

Every kind is rendered (the README's "Adlib" section is the byte-level spec
the TypeScript and Ruby ports follow):

* names from the given and surname pools, per token (rules below); a NAME span
  with school or org cues ("Lincoln Elementary", "Chess Club") renders as that
  kind;
* schools, orgs and places: each run of proper-noun words becomes one pool stem,
  descriptive words kept; a school acronym follows its full name's stand-in;
* SSN, ZIP, phone, IP and card numbers: standard plugs, technically valid and
  obviously fake, so a second vicary pass still detects them (:data:`SSN_PLUG`
  and neighbours);
* emails and handles reuse the name map; an unknown mail host becomes
  ``example.*``; a per-user URL shape (:data:`URL_SHAPES`: profiles, handles,
  channels, user pages) changes its handle or id; every other URL is kept as
  written except whole words the prose name map covers (``/staff/lopez`` takes
  Lopez's stand-in), never its TLD;
* addresses: house number redrawn, street words from the street pool, unit
  scrambled; AGE redrawn within its band; DATE_OF_BIRTH shifted by one
  per-document day offset within its own year;
* anything else: a shape-preserving scramble.

The naming rules, each one the answer to a way the prose could break:

* **Per token, not per span.** "Maria Lopez", "Maria", "Ms. Lopez", "lopez" and
  "Maria's" share two tokens between them, so mapping *tokens* — ``maria`` to one
  invented given name, ``lopez`` to one invented surname — keeps every mention of
  one person on one identity without any coreference step, and keeps two people
  who share a given name sharing one, as the original did. The map is injective,
  so no two originals collide.
* **Given names and surnames come from separate pools**, chosen by where a token
  sits in the document: first of a multi-token span reads given, last reads
  surname, right after a formal title ("Ms.", "Coach") reads surname. A token
  only ever seen alone falls back to the given-name tier.
* **The given-name pool is unisex.** Nothing the detector knows says whether
  Maria is "she", so a gendered stand-in would contradict the essay's pronouns
  half the time. "Her friend Quinn" and "his friend Quinn" both read.
* **Titles, separators and possessives are kept verbatim**, and each token keeps
  its surface case form: ``lopez`` -> ``okafor``, ``LOPEZ`` -> ``OKAFOR``.
* **No collision with the document.** A stand-in never equals (case-folded) any
  word already in the text, any stop word, or another stand-in — so an invented
  name cannot be mistaken for a real one in the same essay, and the mapping can
  be read backwards. A full stand-in that the gazetteer knows as a public figure
  is re-drawn.
* **Deterministic and seeded.** The draw for each token hashes the seed, the
  document and the token, so the same document and seed give the same output, a
  different seed gives different names, and the same name in two documents does
  not map to the same stand-in (a stand-in shared across a class's essays would
  link them).

The pools are vendored package data (``pools/*.txt``, :data:`GIVEN_POOL`, :data:`SURNAME_POOL`),
and every entry is tested against the shipped asset: each given name is in the
gazetteer's common-given-name tier, so a later vicary pass over adlib output
recognises the stand-ins as names even when the writer does not capitalise; no
entry is a public figure, a settlement, a published title or a stop word; no
surname is in the given-name tier. Drawing straight from the gazetteer instead
would hand out notable names, which the detector KEEPS — the opposite of what a
stand-in for a private person should do on a second pass.

Never log the mapping. It is the de-identification key.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from importlib import resources


def load_pool(name: str) -> tuple[str, ...]:
    """One vendored pool, ``pools/<name>.txt``: one entry per line, file order.

    The files are package data built by ``tools/adlib_pools_build.py``; the
    TypeScript and Ruby ports read the same bytes, so file order is part of the
    draw and is never re-sorted here.
    """
    raw = resources.files("vicary").joinpath("pools", f"{name}.txt").read_text("utf-8")
    return tuple(line.strip() for line in raw.splitlines() if line.strip())


#: Unisex given names (SSA female share 0.2-0.8). Every entry: in the shipped
#: common-given-name tier, not notable, not a settlement or published title, not
#: a stop word, not an ordinary English word. ``tests/test_adlib.py`` asserts all
#: of it against the loaded asset.
GIVEN_POOL: tuple[str, ...] = load_pool("given")
#: Surnames. Every entry: NOT in the given-name tier (so the role reads), not
#: notable, not a settlement or title, not a stop word or English word.
SURNAME_POOL: tuple[str, ...] = load_pool("surname")

GIVEN = "given"
SURNAME = "surname"

#: A title right before a token makes it a surname: "Ms. Lopez", "Coach Ruiz".
#: Kinship words ("Aunt", "Grandpa") are titles too — kept verbatim — but carry
#: no role, since "Aunt Maria" is a given name.
_FORMAL_TITLES = frozenset({
    "mr", "mrs", "ms", "miss", "mx", "dr", "doctor", "prof", "professor",
    "coach", "principal", "officer", "sgt", "sergeant", "capt", "captain",
    "rev", "reverend", "pastor", "rabbi", "imam", "nurse", "sen", "senator",
    "rep", "gov", "governor", "mayor", "sir", "dame", "madam", "chief", "fr",
    "mister", "judge", "detective", "lord", "lady",
})
_KIN_TITLES = frozenset({
    "aunt", "auntie", "uncle", "grandma", "grandpa", "grandmother",
    "grandfather", "cousin", "sister", "brother", "father", "mother",
})
_TITLES = _FORMAL_TITLES | _KIN_TITLES

_APOSTROPHES = "'’ʼ"
_POSSESSIVE = re.compile(rf"(?:[{_APOSTROPHES}][sS]|(?<=[sS])[{_APOSTROPHES}])$")
_LETTERS = re.compile(r"[^\W\d_]+")
#: Whitespace and commas separate the chunks of a span; both survive verbatim.
_SEPARATORS = re.compile(r"(\s+|,)")
_EDGE = re.compile(r"^(\W*)(.*?)(\W*)$", re.S)

Oracle = Callable[[str], bool]


class AdlibPoolExhausted(RuntimeError):
    """Every pool entry is taken or collides with the document.

    Raised rather than falling back to a placeholder, because a placeholder in
    the middle of adlib prose is exactly what this mode promises not to emit.
    Needs more distinct name tokens of one role than its pool holds, after the
    document's own words are excluded — 60-odd people in one essay.
    """


@dataclass(frozen=True)
class _Part:
    """One chunk of a name span: a separator, a title, or a name token."""

    kind: str  # "sep" | "title" | "name" | "raw"
    text: str
    #: Case-folded key for name parts: the identity the token maps through.
    key: str = ""
    #: Text kept verbatim around a name part ("(", "'s", "-" joins...).
    lead: str = ""
    tail: str = ""
    #: Which whitespace-delimited chunk this part came from, so both halves of
    #: "Quincy-Adams" take the surname role together.
    chunk: int = 0
    #: "Smiths'" when "Smith" is a key: renders the plural of its stand-in.
    plural: bool = False


def _fold(token: str) -> str:
    folded = unicodedata.normalize("NFC", token).casefold()
    for mark in _APOSTROPHES:
        folded = folded.replace(mark, "'")
    return folded


def _split_span(original: str) -> list[_Part]:
    """Chunk one original span. Hyphenated names map part by part."""
    parts: list[_Part] = []
    for index, chunk in enumerate(_SEPARATORS.split(original)):
        if not chunk:
            continue
        if _SEPARATORS.fullmatch(chunk):
            parts.append(_Part("sep", chunk))
            continue
        match = _EDGE.match(chunk)
        assert match is not None  # the pattern matches every string
        lead, core, tail = match.groups()
        possessive = _POSSESSIVE.search(core)
        if possessive:
            tail = core[possessive.start():] + tail
            core = core[:possessive.start()]
        if not core:
            parts.append(_Part("raw", chunk))
            continue
        if _fold(core).rstrip(".") in _TITLES and not parts_have_name(parts):
            parts.append(_Part("title", chunk))
            continue
        pieces = core.split("-")
        for i, piece in enumerate(pieces):
            piece_lead = lead if i == 0 else "-"
            piece_tail = tail if i == len(pieces) - 1 else ""
            if not _LETTERS.search(piece):
                parts.append(_Part("raw", piece_lead + piece + piece_tail))
                continue
            parts.append(_Part("name", piece, key=_fold(piece).rstrip("."),
                               lead=piece_lead, tail=piece_tail, chunk=index))
    return parts


def parts_have_name(parts: Sequence[_Part]) -> bool:
    """Whether a name token already appeared: a title only leads a span."""
    return any(p.kind == "name" for p in parts)


def _is_initial(key: str) -> bool:
    return len(_LETTERS.findall(key)) == 1 and len(key.rstrip(".")) == 1


def _roles(spans: Sequence[list[_Part]],
           given_name: Oracle | None) -> dict[str, str]:
    """GIVEN or SURNAME per token key, by where the token sits across the text."""
    votes: dict[str, list[int]] = {}
    for parts in spans:
        names = [p for p in parts if p.kind == "name" and not _is_initial(p.key)]
        if not names:
            continue
        for p in names:
            votes.setdefault(p.key, [0, 0])
        # "Lopez, Maria": a comma inside a span puts the surname first.
        comma = any(p.kind == "sep" and p.text == "," for p in parts)
        titled = any(p.kind == "title" and _fold(p.text).rstrip(".")
                     in _FORMAL_TITLES for p in parts)
        chunks = sorted({p.chunk for p in names})
        if titled:
            for p in names:
                if p.chunk == chunks[-1]:
                    votes[p.key][1] += 2
                else:
                    votes[p.key][0] += 1
        elif len(chunks) > 1:
            surname = chunks[0] if comma else chunks[-1]
            for p in names:
                votes[p.key][1 if p.chunk == surname else 0] += 1
    roles = {}
    for key, (given, surname) in votes.items():
        if given != surname:
            roles[key] = GIVEN if given > surname else SURNAME
        elif given_name is not None and not given_name(key):
            roles[key] = SURNAME
        else:
            roles[key] = GIVEN
    return roles


def _cased(stand_in: str, like: str) -> str:
    """``stand_in`` in the case form of ``like``: lower, UPPER or Title."""
    letters = "".join(_LETTERS.findall(like))
    if letters.islower():
        return stand_in.lower()
    if letters.isupper() and len(letters) > 1:
        return stand_in.upper()
    return stand_in


def _possessive_tail(tail: str, stand_in: str) -> str:
    """Keep the possessive, but "James'" over a stand-in not ending in s is "'s"."""
    if tail and tail[0] in _APOSTROPHES and (len(tail) == 1 or not tail[1].isalpha()):
        if not stand_in.lower().endswith("s"):
            s = "S" if stand_in.isupper() else "s"
            return tail[0] + s + tail[1:]
    return tail


def draw(seed: str, document_digest: str, purpose: str, key: str,
         bump: int, index: int, n: int) -> int:
    """The one source of randomness: a portable index in ``range(n)``.

    ``sha256("\\0".join([seed, doc_sha256_hex, purpose, key, f"{bump}.{index}"]))``,
    first 8 bytes big-endian, modulo ``n``. Every port computes the same bytes,
    so the same document and seed give the same stand-ins in all three.
    """
    material = "\0".join((seed, document_digest, purpose, key, f"{bump}.{index}"))
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % n


def _initials(seed: str, document_digest: str, keys: Iterable[str],
              forbidden: frozenset[str]) -> dict[str, str]:
    """An injective letter map for initials ("J." -> "R."), walked like a pool."""
    alphabet = "abcdefghijklmnopqrstuvwxyz"
    out: dict[str, str] = {}
    used: set[str] = set()
    for key in keys:
        letter = key.rstrip(".")
        if letter in out:
            continue
        start = draw(seed, document_digest, "initial", letter, 0, 0, len(alphabet))
        for step in range(len(alphabet)):
            candidate = alphabet[(start + step) % len(alphabet)]
            if candidate not in forbidden and candidate not in used:
                break
        else:
            candidate = letter
        out[letter] = candidate
        used.add(candidate)
    return out


#: Placeholder kinds the local classifier mints, and the two adlib adds when a
#: NAME span reads as a school or an organisation.
NAME = "NAME"
SCHOOL = "SCHOOL"
ORGANIZATION = "ORGANIZATION"
LOCATION = "LOCATION"

#: Words a school, organisation or place keeps verbatim: they describe the kind
#: of thing, not which one. Everything else is a proper-noun run and is replaced.
_DESCRIPTIVE = frozenset({
    "elementary", "middle", "high", "school", "schools", "academy", "college",
    "university", "prep", "preparatory", "charter", "montessori", "institute",
    "junior", "senior", "jr", "sr", "community", "christian", "catholic",
    "day", "district", "unified", "isd", "usd", "magnet", "primary",
    "secondary", "intermediate", "kindergarten", "preschool", "early",
    "learning", "center", "centre", "club", "inc", "llc", "ltd", "co", "corp",
    "corporation", "company", "church", "temple", "mosque", "synagogue",
    "foundation", "library", "museum", "hospital", "clinic", "team", "league",
    "society", "association", "council", "scouts", "troop", "band", "choir",
    "orchestra", "park", "lake", "river", "creek", "county", "city", "town",
    "township", "village", "mount", "mt", "valley", "beach", "island",
    "springs", "falls", "heights", "hills", "bay", "harbor", "port", "north",
    "south", "east", "west", "new", "saint", "st", "fort", "the", "of", "and",
    "&", "for", "at", "in", "on", "state", "national", "public",
})
#: A NAME span carrying one of these renders as a school ...
_SCHOOL_CUES = frozenset({"elementary", "school", "academy", "college", "university"})
#: ... or ending in one of these ("Lincoln High").
_SCHOOL_TAIL_CUES = frozenset({"high", "middle"})
#: A NAME span carrying one of these renders as an organisation.
_ORG_CUES = frozenset({
    "inc", "llc", "ltd", "corp", "corporation", "company", "club", "church",
    "foundation", "library", "museum", "society", "association", "league",
    "council", "center", "centre",
})
_RUN_POOLS = {SCHOOL: "school", ORGANIZATION: "org", LOCATION: "place"}

#: Address words kept verbatim: directions, street suffixes, unit markers.
_DIRECTIONS = frozenset({
    "n", "s", "e", "w", "ne", "nw", "se", "sw", "north", "south", "east",
    "west", "northeast", "northwest", "southeast", "southwest",
})
_STREET_SUFFIXES = frozenset({
    "street", "st", "avenue", "ave", "boulevard", "blvd", "road", "rd",
    "drive", "dr", "lane", "ln", "court", "ct", "circle", "cir", "place", "pl",
    "terrace", "ter", "way", "parkway", "pkwy", "highway", "hwy", "trail",
    "trl", "square", "sq", "loop", "alley", "commons",
})
_UNIT = re.compile(r"(\s*(?:(?:Apt|Apartment|Suite|Ste|Unit)\.?|\#)\s*)([\w-]+)(\W*)$",
                   re.IGNORECASE)

#: Mail providers kept verbatim: naming one identifies nobody.
PROVIDERS = frozenset({
    "gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com",
    "aol.com", "protonmail.com", "proton.me", "live.com", "msn.com", "me.com",
    "mac.com", "comcast.net",
})
#: The only URLs adlib changes: per-user shapes, where a path segment, query
#: value or subdomain *is* a person (a profile, handle, channel or user page).
#: Every other URL is kept exactly as written. Each entry is a host (folded,
#: after a leading ``www.``, ``m.`` or ``mobile.`` is dropped) and a pattern
#: matched against the path's leading ``/`` segments; segments past the pattern
#: are kept. In a pattern, ``{h}`` is a handle (it goes through *labels*, so a
#: mapped name takes its stand-in), ``@{h}`` a handle written after ``@``,
#: ``{i}`` an id (scrambled shape for shape), anything else a literal compared
#: case-folded. ``?key={i}`` after the path pattern makes that query key's value
#: an id and requires the path to end there. The first matching entry wins.
URL_SHAPES: tuple[tuple[str, str], ...] = (
    ("youtube.com", "/@{h}"), ("youtube.com", "/c/{h}"),
    ("youtube.com", "/channel/{i}"), ("youtube.com", "/user/{h}"),
    ("instagram.com", "/stories/{h}"), ("instagram.com", "/{h}"),
    ("twitter.com", "/{h}/status/{i}"), ("twitter.com", "/{h}"),
    ("x.com", "/{h}/status/{i}"), ("x.com", "/{h}"),
    ("tiktok.com", "/@{h}/video/{i}"), ("tiktok.com", "/@{h}"),
    ("threads.net", "/@{h}"), ("threads.com", "/@{h}"),
    ("facebook.com", "/profile.php?id={i}"), ("facebook.com", "/people/{h}/{i}"),
    ("facebook.com", "/{h}"),
    ("linkedin.com", "/in/{h}"), ("linkedin.com", "/pub/{h}"),
    ("github.com", "/orgs/{h}"), ("github.com", "/sponsors/{h}"), ("github.com", "/{h}"),
    ("reddit.com", "/user/{h}"), ("reddit.com", "/u/{h}"),
    ("old.reddit.com", "/user/{h}"), ("old.reddit.com", "/u/{h}"),
    ("medium.com", "/@{h}"), ("mastodon.social", "/@{h}"),
    ("bsky.app", "/profile/{h}"), ("snapchat.com", "/add/{h}"),
    ("pinterest.com", "/{h}"), ("twitch.tv", "/{h}"), ("soundcloud.com", "/{h}"),
    ("linktr.ee", "/{h}"), ("vsco.co", "/{h}"), ("patreon.com", "/{h}"),
    ("tumblr.com", "/{h}"), ("deviantart.com", "/{h}"), ("codepen.io", "/{h}"),
    ("replit.com", "/@{h}"), ("scratch.mit.edu", "/users/{h}"),
    ("roblox.com", "/users/{i}"), ("khanacademy.org", "/profile/{h}"),
    ("open.spotify.com", "/user/{h}"), ("steamcommunity.com", "/id/{h}"),
    ("steamcommunity.com", "/profiles/{i}"), ("flickr.com", "/people/{h}"),
    ("flickr.com", "/photos/{h}"), ("wattpad.com", "/user/{h}"),
    ("archiveofourown.org", "/users/{h}"), ("quizlet.com", "/user/{h}"),
    ("goodreads.com", "/user/show/{h}"),
)
#: First segments that are a site's own pages, never a ``{h}`` or ``{i}``
#: (folded, the whole segment). One list for every host.
URL_RESERVED = frozenset({
    "about", "accounts", "add", "apps", "c", "channel", "collections", "compose",
    "dashboard", "developer", "direct", "directory", "discover", "enterprise",
    "events", "explore", "features", "gaming", "groups", "hashtag", "help", "home",
    "i", "ideas", "intent", "issues", "join", "legal", "login", "marketplace",
    "messages", "new", "notifications", "orgs", "p", "pages", "people",
    "permalink.php", "photo.php", "pin", "policies", "pricing", "privacy",
    "profile.php", "pulls", "reel", "reels", "register", "search", "settings",
    "share", "sharer.php", "signup", "sponsors", "stories", "stream", "tagged",
    "today", "topics", "tos", "trending", "tv", "upload", "user", "videos",
    "watch", "you",
})
#: Hosts that give each user a subdomain: ``maria.substack.com``. The one label
#: left of the suffix (after ``www.``) is a handle unless it is in
#: :data:`URL_RESERVED` or is ``www``; the rest of the URL is kept.
URL_USER_SUBDOMAINS = frozenset({
    "substack.com", "tumblr.com", "blogspot.com", "wordpress.com", "github.io",
    "neocities.org", "weebly.com", "wixsite.com", "carrd.co",
})

#: Structured identifiers are standard plugs, technically valid and obviously
#: fake, never random draws. Each still re-detects as its kind. Variant ``k``
#: (the k-th distinct value of the kind in the document, from 0) is noted.
#: SSN: serial ``9999 - k``. Not 9xx: the SSN pattern refuses that area.
SSN_PLUG = "799-99-9999"
#: ZIP: ``99999 - k``; a +4 reads ``-9999``.
ZIP_PLUG = "99999"
#: Phone: line ``0100 + k`` (mod 100) of the fictional 555-01xx range behind
#: one fixed real area code, never 555 and never the original's.
PHONE_AREA_CODE = "201"
PHONE_PLUG = "201-555-0100"
#: IPv4: the RFC 5737 documentation ranges, ``.1`` to ``.254`` of each in turn.
IP_RANGES = ("192.0.2.", "198.51.100.", "203.0.113.")
#: Cards: well-known Luhn-valid test numbers by brand prefix and length; variant
#: ``k`` subtracts k from the last four payload digits (mod 10000) and recomputes
#: the check digit. Unknown brand or length: ``4`` + ``1``s + check digit.
CARD_PLUGS: tuple[tuple[tuple[str, ...], int, str], ...] = (
    (("34", "37"), 15, "378282246310005"),
    (("4",), 16, "4111111111111111"),
    (("4",), 13, "4222222222222"),
    (("51", "52", "53", "54", "55", "22", "23", "24", "25", "26", "27"), 16,
     "5555555555554444"),
    (("6011", "64", "65"), 16, "6011111111111117"),
    (("30", "36", "38"), 14, "30569309025904"),
    (("35",), 16, "3530111333300000"),
)
#: An age is redrawn within its band, so a stand-in keeps the grade cohort.
AGE_BANDS: tuple[tuple[int, int], ...] = (
    (0, 4), (5, 7), (8, 10), (11, 13), (14, 18), (19, 24), (25, 34),
    (35, 44), (45, 54), (55, 64), (65, 74), (75, 84), (85, 99),
)
_DATE = re.compile(r"(\d{1,2})([/-])(\d{1,2})\2(\d{2,4})")
_ACRONYM = re.compile(r"[A-Z]{3,6}")
_MAX_BUMPS = 256


@dataclass(frozen=True)
class Invented:
    """What :func:`invent` returns: one stand-in per input span, in order."""

    stand_ins: tuple[str, ...]
    #: ``{invented token: original token}`` — each name token and place, school
    #: or org stem, so a stand-in a model repeats on its own still restores.
    tokens: Mapping[str, str]


def _plural(word: str) -> str:
    return word + ("es" if word.lower().endswith(("s", "x", "z", "ch", "sh")) else "s")


def _is_plural_possessive(part: _Part) -> bool:
    tail = part.tail
    return (bool(tail) and tail[0] in _APOSTROPHES
            and (len(tail) == 1 or not tail[1].isalpha()) and part.key.endswith("s"))


def _resolve_plurals(spans: dict[str, list[_Part]]) -> None:
    """"Smiths'" maps through "smith" when "Smith" is a key; "James'" stays."""
    bases = {p.key for parts in spans.values() for p in parts
             if p.kind == "name" and not _is_plural_possessive(p)}
    for parts in spans.values():
        for i, p in enumerate(parts):
            if p.kind != "name" or not _is_plural_possessive(p):
                continue
            for cut in (1, 2):
                base = p.key[:-cut]
                if (cut == 1 or p.key.endswith("es")) and base in bases:
                    parts[i] = replace(p, key=base, plural=True)
                    break


def acronym(name: str) -> str:
    """``"Lincoln High School"`` -> ``"LHS"``: first letter of each word."""
    return "".join(w[0] for w in re.findall(r"[A-Za-z][\w'-]*", name)).upper()


def _luhn_digit(payload: str) -> str:
    total = 0
    for i, ch in enumerate(reversed(payload)):
        d = (ord(ch) - 48) * (2 if i % 2 == 0 else 1)
        total += d - 9 if d > 9 else d
    return str((10 - total % 10) % 10)


def _fill_digits(original: str, digits: str) -> str:
    """``original`` with its digits replaced, in order, by ``digits``."""
    it = iter(digits)
    return "".join(next(it) if c.isdigit() else c for c in original)


def card_plug(digits: str, variant: int) -> str:
    """The test card number standing in for ``digits`` (see :data:`CARD_PLUGS`)."""
    base = next((plug for prefixes, length, plug in CARD_PLUGS
                 if length == len(digits) and digits.startswith(prefixes)), None)
    if base is None:
        payload = "4" + "1" * (len(digits) - 2)
        base = payload + _luhn_digit(payload)
    if not variant:
        return base
    payload = base[:-1]
    last4 = (int(payload[-4:]) - variant) % 10000
    payload = payload[:-4] + f"{last4:04d}"
    return payload + _luhn_digit(payload)


def _days(year: int) -> list[int]:
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    return [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]


def shift_date(date: str, offset: int) -> str:
    """Move a ``m/d/y`` (or ``d/m/y`` when the first field > 12) date forward
    ``offset`` days within its own year, wrapping at year end. Field order,
    separator, zero padding and the year are kept; a 2-digit year is 20yy for
    the leap-year rule; an impossible day is clamped to its month first."""
    match = _DATE.search(date)
    if match is None:
        return date
    first, sep, second, year_text = match.groups()
    day_first = int(first) > 12
    month_text, day_text = (second, first) if day_first else (first, second)
    year = int(year_text) + (2000 if len(year_text) == 2 else 0)
    days = _days(year)
    month = min(max(int(month_text), 1), 12)
    day = min(max(int(day_text), 1), days[month - 1])
    doy = sum(days[:month - 1]) + day
    doy = (doy - 1 + offset) % sum(days) + 1
    month = 1
    while doy > days[month - 1]:
        doy -= days[month - 1]
        month += 1
    new_month = f"{month:0{len(month_text)}d}"
    new_day = f"{doy:0{len(day_text)}d}"
    fields = (new_day, new_month) if day_first else (new_month, new_day)
    new = f"{fields[0]}{sep}{fields[1]}{sep}{year_text}"
    return date[:match.start()] + new + date[match.end():]


def _effective_kind(kind: str, original: str) -> str:
    """A NAME span with school or org cues renders as that kind."""
    if kind == "PERSON":
        kind = NAME
    if kind != NAME:
        return kind
    words = [_fold(w).rstrip(".") for w in original.split()]
    if not words:
        return kind
    if any(w in _SCHOOL_CUES for w in words) or words[-1] in _SCHOOL_TAIL_CUES:
        return SCHOOL
    if any(w in _ORG_CUES for w in words):
        return ORGANIZATION
    return kind


def _url_host(host: str) -> tuple[str, str]:
    """``www.``, ``m.`` or ``mobile.`` (one, kept as written) and the bare host."""
    match = re.match(r"^((?:www|m|mobile)\.)?(.*)$", host, re.IGNORECASE | re.DOTALL)
    assert match is not None
    return match.group(1) or "", match.group(2)


class _Engine:
    """One document's adlib state: the taken words, the maps, the used sets."""

    def __init__(self, *, document: str, seed: int | str, stop_words: frozenset[str],
                 identity: Iterable[str], given_name: Oracle | None,
                 notable: Oracle | None) -> None:
        self.seed = str(seed)
        self.digest = hashlib.sha256(document.encode("utf-8")).hexdigest()
        identity_words = {_fold(w) for value in identity
                          for w in _LETTERS.findall(value or "")}
        self.taken = (frozenset(_fold(w) for w in _LETTERS.findall(document))
                      | stop_words | identity_words)
        self.stop_words = stop_words
        self.given_name = given_name
        self.notable = notable
        self.names: dict[str, str] = {}
        #: The prose name map alone, frozen before any other kind renders, so an
        #: off-list URL's names do not depend on document order.
        self.prose_names: dict[str, str] = {}
        self.used_names: set[str] = set()
        self.stems: dict[tuple[str, str], str] = {}
        self.used_stems: dict[str, set[str]] = {}
        self.tokens: dict[str, str] = {}
        self.dob_offset = 1

    def draw(self, purpose: str, key: str, bump: int, index: int, n: int) -> int:
        return draw(self.seed, self.digest, purpose, key, bump, index, n)

    # -- names -------------------------------------------------------------

    def invent_names(self, originals: Sequence[str]) -> dict[str, str]:
        spans = {original: _split_span(original) for original in originals}
        _resolve_plurals(spans)
        roles = _roles(list(spans.values()), self.given_name)
        order: list[str] = []
        surface: dict[str, str] = {}
        for parts in spans.values():
            for p in parts:
                if p.kind == "name" and not _is_initial(p.key) and p.key not in surface:
                    order.append(p.key)
                    surface[p.key] = p.text
        for key in order:
            roles.setdefault(key, GIVEN)
        pools = {GIVEN: GIVEN_POOL, SURNAME: SURNAME_POOL}
        bumps: dict[str, int] = {}

        def assign() -> dict[str, str]:
            used: set[str] = set()
            assigned: dict[str, str] = {}
            for key in order:
                pool = pools[roles[key]]
                start = draw(self.seed, self.digest, roles[key], key,
                             bumps.get(key, 0), 0, len(pool))
                for step in range(len(pool)):
                    candidate = pool[(start + step) % len(pool)]
                    folded = _fold(candidate)
                    if folded not in self.taken and folded not in used:
                        assigned[key] = candidate
                        used.add(folded)
                        break
                else:
                    raise AdlibPoolExhausted(
                        f"no unused {roles[key]} stand-in left for this document")
            return assigned

        assigned = assign()
        # A stand-in pair that happens to name a public figure is re-drawn by
        # bumping its last token. Bounded: each bump moves one draw.
        for _ in range(len(SURNAME_POOL)):
            if self.notable is None:
                break
            clash = None
            for parts in spans.values():
                keys = [p.key for p in parts if p.kind == "name" and p.key in assigned]
                if len(keys) > 1 and self.notable(" ".join(assigned[k] for k in keys)):
                    clash = keys[-1]
                    break
            if clash is None:
                break
            bumps[clash] = bumps.get(clash, 0) + 1
            assigned = assign()
        self.names = dict(assigned)
        self.prose_names = dict(assigned)
        self.used_names = {_fold(v) for v in assigned.values()}
        for key, stand_in in assigned.items():
            self.tokens.setdefault(stand_in, surface[key])

        initial_keys = [p.key for parts in spans.values() for p in parts
                        if p.kind == "name" and _is_initial(p.key)]
        initials = _initials(self.seed, self.digest, initial_keys,
                             frozenset(k.rstrip(".") for k in initial_keys))
        out: dict[str, str] = {}
        for original, parts in spans.items():
            rendered = []
            for p in parts:
                if p.kind != "name":
                    rendered.append(p.text)
                    continue
                if _is_initial(p.key):
                    letter = initials[p.key.rstrip(".")]
                    core = p.text.replace(p.text.rstrip(".")[-1],
                                          _cased(letter.upper(), p.text), 1)
                    rendered.append(p.lead + core + _possessive_tail(p.tail, core))
                elif p.plural:
                    core = _cased(_plural(assigned[p.key]), p.text)
                    rendered.append(p.lead + core + p.tail)
                else:
                    core = _cased(assigned[p.key], p.text)
                    rendered.append(p.lead + core + _possessive_tail(p.tail, core))
            out[original] = "".join(rendered)
        return out

    def name_token(self, key: str, like: str) -> str:
        """A name stand-in for a token first met in an email, handle or URL."""
        if key not in self.names:
            start = self.draw(GIVEN, key, 0, 0, len(GIVEN_POOL))
            for step in range(len(GIVEN_POOL)):
                candidate = GIVEN_POOL[(start + step) % len(GIVEN_POOL)]
                folded = _fold(candidate)
                if folded not in self.taken and folded not in self.used_names:
                    break
            else:
                raise AdlibPoolExhausted("no unused given stand-in left for this document")
            self.names[key] = candidate
            self.used_names.add(folded)
            self.tokens.setdefault(candidate, like)
        return _cased(self.names[key], like)

    # -- proper-noun runs (schools, orgs, places, streets) -------------------

    def stem(self, pool_name: str, key: str, like: str) -> str:
        memo = (pool_name, key)
        if memo not in self.stems:
            pool = load_pool(pool_name)
            used = self.used_stems.setdefault(pool_name, set())
            start = self.draw(pool_name, key, 0, 0, len(pool))
            for step in range(len(pool)):
                candidate = pool[(start + step) % len(pool)]
                words = {_fold(w) for w in _LETTERS.findall(candidate)}
                if _fold(candidate) not in used and not words & self.taken:
                    break
            else:
                raise AdlibPoolExhausted(f"no unused {pool_name} stem left for this document")
            used.add(_fold(candidate))
            self.stems[memo] = candidate
            self.tokens.setdefault(candidate, like)
        return _cased(self.stems[memo], like)

    def runs(self, text: str, pool_name: str, kept: Callable[[str], bool]) -> str:
        """Replace each maximal run of non-``kept`` words with one pool stem."""
        out: list[str] = []
        run: list[tuple[str, str, str]] = []
        pending = ""  # whitespace seen inside a run, emitted only if it ends there

        def flush() -> None:
            nonlocal pending
            if run:
                like = " ".join(core for _, core, _ in run)
                key = " ".join(_fold(core) for _, core, _ in run)
                out.append(run[0][0] + self.stem(pool_name, key, like) + run[-1][2])
                run.clear()
            out.append(pending)
            pending = ""

        for chunk in re.split(r"(\s+)", text):
            if not chunk:
                continue
            if chunk.isspace():
                if run:
                    pending += chunk
                else:
                    out.append(chunk)
                continue
            match = _EDGE.match(chunk)
            assert match is not None
            lead, core, tail = match.groups()
            possessive = _POSSESSIVE.search(core)
            if possessive:
                tail = core[possessive.start():] + tail
                core = core[:possessive.start()]
            if not _LETTERS.search(core) or kept(_fold(core).rstrip(".")):
                flush()
                out.append(chunk)
                continue
            if run and run[-1][2]:
                flush()  # punctuation after a word ends its run: "Lake, Ohio"
            pending = ""
            run.append((lead, core, tail))
        flush()
        return "".join(out)

    # -- every other kind ------------------------------------------------------

    def scramble(self, original: str, bump: int) -> str:
        """Shape-preserving: digit for digit, letter for letter, case kept."""
        out = []
        for i, c in enumerate(original):
            if c.isdigit():
                out.append(str(self.draw("scramble", original, bump, i, 10)))
            elif "a" <= c <= "z":
                out.append(chr(97 + self.draw("scramble", original, bump, i, 26)))
            elif "A" <= c <= "Z":
                out.append(chr(65 + self.draw("scramble", original, bump, i, 26)))
            else:
                out.append(c)
        return "".join(out)

    def labels(self, text: str) -> str:
        """Email local parts and handles: names mapped, digits scrambled."""
        out = []
        for piece in re.split(r"([A-Za-z]+|\d+)", text):
            if not piece:
                continue
            if piece.isdigit():
                out.append(self.scramble(piece, 0) if len(piece) >= 3 else piece)
            elif piece.isalpha() and piece.isascii():
                key = _fold(piece)
                if key in self.names:
                    out.append(_cased(self.names[key], piece))
                elif joined := self.joined_names(key):
                    out.append(_cased(joined, piece))
                elif len(piece) <= 2:
                    out.append(piece)
                else:
                    out.append(self.name_token(key, piece))
            else:
                out.append(piece)
        return "".join(out)

    def joined_names(self, key: str) -> str:
        """"marialopez" -> "quincyokafor" when both halves are mapped names."""
        for cut in range(2, len(key) - 1):
            if key[:cut] in self.names and key[cut:] in self.names:
                return self.names[key[:cut]] + self.names[key[cut:]]
        return ""

    def email(self, original: str, bump: int) -> str:
        local, _, domain = original.rpartition("@")
        local = self.labels(local) + (str(bump + 1) if bump else "")
        if _fold(domain) not in PROVIDERS:
            tld = domain.rsplit(".", 1)[-1]
            domain = "example." + (tld if tld.lower() in ("com", "net", "org") else "org")
        return f"{local}@{domain}"

    def url(self, original: str, bump: int) -> str:
        """A per-user shape (:data:`URL_SHAPES`, :data:`URL_USER_SUBDOMAINS`) changes
        its handle or id; any other URL comes back as written but for prose-mapped
        names (:meth:`mapped_words`)."""
        match = re.match(r"^((?:[a-z][a-z0-9+.-]*://)?)([^/?#]*)([^?#]*)(\?[^#]*)?(#.*)?$",
                         original, re.IGNORECASE | re.DOTALL)
        assert match is not None
        scheme, host, path, query, fragment = (g or "" for g in match.groups())
        lead, bare = _url_host(host)
        changed = self.url_shape(bare, path, query)
        if changed is None:
            label, dot, suffix = bare.partition(".")
            if (dot and _fold(suffix) in URL_USER_SUBDOMAINS and label
                    and _fold(label) not in URL_RESERVED and _fold(label) != "www"):
                changed = (self.labels(label) + dot + suffix, path, query)
        if changed is None:
            # Off-list URL (Blake 11-04): kept as written except whole words the prose
            # name map already covers; the TLD never changes.
            stem, dot, tld = bare.rpartition(".")
            bare = self.mapped_words(stem) + dot + tld if dot else bare
            path, query, fragment = (self.mapped_words(t) for t in (path, query, fragment))
        else:
            bare, path, query = changed
        return scheme + lead + bare + path + query + fragment + (str(bump + 1) if bump else "")

    def mapped_words(self, text: str) -> str:
        """Each letter run that is a prose-mapped name (3+ letters), or two of them
        joined ("marialopez"), takes its stand-in; everything else is kept."""
        out = []
        for piece in re.split(r"([A-Za-z]+)", text):
            key = _fold(piece)
            if len(key) >= 3 and key in self.prose_names:
                out.append(_cased(self.prose_names[key], piece))
                continue
            for cut in range(3, len(key) - 2):
                if key[:cut] in self.prose_names and key[cut:] in self.prose_names:
                    out.append(_cased(self.prose_names[key[:cut]], piece[:cut])
                               + _cased(self.prose_names[key[cut:]], piece[cut:]))
                    break
            else:
                out.append(piece)
        return "".join(out)

    def url_shape(self, host: str, path: str,
                  query: str) -> tuple[str, str, str] | None:
        """The first :data:`URL_SHAPES` entry for ``host`` matching ``path``
        (and ``query``), applied; ``None`` when none does."""
        folded_host = _fold(host)
        segments = path.split("/")
        for shape_host, pattern in URL_SHAPES:
            if shape_host != folded_host:
                continue
            path_pattern, _, query_pattern = pattern.partition("?")
            wanted = path_pattern.split("/")
            if len(segments) < len(wanted) or (query_pattern and len(segments) != len(wanted)):
                continue
            out = list(segments)
            for at, want in enumerate(wanted):
                seg = segments[at]
                if want in ("{h}", "{i}", "@{h}"):
                    body = seg[1:] if want == "@{h}" else seg
                    if (want == "@{h}" and not seg.startswith("@")) or not body \
                            or _fold(body) in URL_RESERVED:
                        break
                    body = self.scramble(body, 0) if want == "{i}" else self.labels(body)
                    out[at] = ("@" if want == "@{h}" else "") + body
                elif _fold(seg) != want:
                    break
            else:
                if query_pattern:
                    key, _, _ = query_pattern.partition("=")
                    pairs = re.split(r"([&;])", query[1:]) if query else []
                    hit = False
                    for n, pair in enumerate(pairs):
                        name, eq, value = pair.partition("=")
                        if eq and _fold(name) == key and value:
                            pairs[n] = name + eq + self.scramble(value, 0)
                            hit = True
                    if not hit:
                        continue
                    query = "?" + "".join(pairs)
                return host, "/".join(out), query
        return None

    def username(self, original: str, bump: int) -> str:
        return "@" + self.labels(original[1:]) + (
            str(bump + 1) if bump else "")

    def address(self, original: str, bump: int) -> str:
        """House number redrawn, unit scrambled, street words from the street
        pool; directions and the suffix kept."""
        unit = _UNIT.search(original)
        head = original[:unit.start()] if unit else original
        number = re.match(r"\d+", head)
        if number:
            head = self.house_number(number.group(0), bump) + head[number.end():]

        def kept(word: str) -> bool:
            return word in _DIRECTIONS or word in _STREET_SUFFIXES

        head = self.runs(head, "street", kept)
        if unit:
            head += unit.group(1) + self.scramble(unit.group(2), bump) + unit.group(3)
        return head

    def house_number(self, digits: str, bump: int) -> str:
        new = [str(1 + self.draw("address-number", digits, bump, 0, 9))]
        new += [str(self.draw("address-number", digits, bump, i, 10))
                for i in range(1, len(digits))]
        return "".join(new)

    def age(self, original: str, used: set[str], originals: set[str]) -> str:
        value = int(original)
        lo, hi = next(((lo, hi) for lo, hi in AGE_BANDS if lo <= value <= hi), (0, 99))
        pool = [a for a in range(lo, hi + 1) if a != value]
        free = [a for a in pool if str(a) not in originals] or pool
        start = self.draw("age", original, 0, 0, len(free))
        # Walk the values no original holds from the draw, then the rest of the
        # band ascending, so distinct ages stay distinct while the band allows.
        walk = [free[(start + step) % len(free)] for step in range(len(free))]
        walk += [a for a in pool if a not in free]
        candidate = next((str(a) for a in walk if str(a) not in used), str(free[start]))
        return candidate.zfill(len(original)) if len(original) > len(candidate) else candidate

    def plug(self, kind: str, original: str, variant: int) -> str:
        if kind == "US_SOCIAL_SECURITY_NUMBER":
            return _fill_digits(original, f"79999{(9999 - variant) % 10000:04d}")
        if kind == "ZIP_CODE":
            digits = f"{(99999 - variant) % 100000:05d}" + "9999"
            return _fill_digits(original, digits)
        if kind == "IP_ADDRESS":
            return IP_RANGES[variant // 254 % 3] + str(variant % 254 + 1)
        if kind == "CREDIT_DEBIT_CARD_NUMBER":
            digits = re.sub(r"\D", "", original)
            return _fill_digits(original, card_plug(digits, variant))
        # PHONE: an extension is kept verbatim; the last 7 digits become
        # 555-01xx, the 3 before them PHONE_AREA_CODE, any country code is kept.
        ext = re.search(r"\s*(?:x|ext\.?|extension)\s*\d{1,6}$", original, re.IGNORECASE)
        main = original[:ext.start()] if ext else original
        number = list(re.sub(r"\D", "", main))
        number[-7:] = list(f"55501{variant % 100:02d}")
        if len(number) >= 10:
            number[-10:-7] = list(PHONE_AREA_CODE)
        return _fill_digits(main, "".join(number)) + (ext.group(0) if ext else "")


_PLUG_KINDS = frozenset({"US_SOCIAL_SECURITY_NUMBER", "ZIP_CODE", "IP_ADDRESS",
                         "CREDIT_DEBIT_CARD_NUMBER", "PHONE"})


def invent(
    spans: Sequence[tuple[str, str]],
    *,
    document: str,
    seed: int | str = 0,
    stop_words: frozenset[str] = frozenset(),
    given_name: Oracle | None = None,
    notable: Oracle | None = None,
    identity: Iterable[str] = (),
) -> Invented:
    """A stand-in for every ``(kind, original)`` span of one document.

    Name tokens are assigned first, in first-appearance order; every other span
    follows in document order, except school acronyms, which resolve last so
    they can follow the full name wherever it sits. The same ``(kind,
    original)`` always gets the same stand-in, and two different originals of
    one kind never share one (case-folded). See the module docstring for the
    per-kind rules.

    Args:
        spans: ``(placeholder kind, original text)`` in document order.
        document: the full original text; every word in it is off-limits.
        seed: varies the draw. Same document and seed, same output.
        stop_words: words an invented token must never be (case-folded).
        given_name: role tie-breaker for a name token only ever seen alone.
        notable: a full name stand-in this oracle calls notable is re-drawn.
        identity: the writer's own strings (``StudentIdentity`` fields); no
            invented token may be one of their words.

    Raises:
        AdlibPoolExhausted: a pool ran out of usable entries.
    """
    engine = _Engine(document=document, seed=seed, stop_words=stop_words,
                     identity=identity, given_name=given_name, notable=notable)
    kinds = [_effective_kind(kind, original) for kind, original in spans]
    names = engine.invent_names([o for (_, o), k in zip(spans, kinds, strict=True) if k == NAME])

    dobs = [o for (_, o), k in zip(spans, kinds, strict=True) if k == "DATE_OF_BIRTH"]
    dob_dates = {m.group(0) for o in dobs if (m := _DATE.search(o))}
    for bump in range(_MAX_BUMPS):
        engine.dob_offset = 1 + engine.draw("dob-offset", "", bump, 0, 364)
        if not {shift_date(d, engine.dob_offset) for d in dob_dates} & dob_dates:
            break
    ages = {o for (_, o), k in zip(spans, kinds, strict=True) if k == "AGE"}
    original_folds = {_fold(o) for _, o in spans}

    out: list[str] = ["" for _ in spans]
    memo: dict[tuple[str, str], str] = {}
    owner: dict[tuple[str, str], str] = {}
    variants: dict[str, int] = {}
    acronyms: list[int] = []

    def render(kind: str, original: str, bump: int) -> str:
        if kind in _RUN_POOLS:
            return engine.runs(original, _RUN_POOLS[kind],
                               lambda w: w in _DESCRIPTIVE)
        if kind == "EMAIL":
            return engine.email(original, bump)
        if kind == "URL":
            return engine.url(original, bump)
        if kind == "USERNAME":
            return engine.username(original, bump)
        if kind == "ADDRESS":
            return engine.address(original, bump)
        if kind == "DATE_OF_BIRTH":
            return shift_date(original, engine.dob_offset)
        if kind == "AGE" and original.isdigit():
            used = {v for (k, _), v in memo.items() if k == "AGE"}
            return engine.age(original, used, ages)
        if kind in _PLUG_KINDS:
            return engine.plug(kind, original, bump)
        return engine.scramble(original, bump)

    for index, ((_, original), kind) in enumerate(zip(spans, kinds, strict=True)):
        if kind == NAME:
            out[index] = names[original]
            continue
        if (kind, original) in memo:
            out[index] = memo[(kind, original)]
            continue
        if kind == SCHOOL and _ACRONYM.fullmatch(original):
            acronyms.append(index)
            continue
        start = variants.get(kind, 0) if kind in _PLUG_KINDS else 0
        for bump in range(start, start + _MAX_BUMPS):
            candidate = render(kind, original, bump)
            folded = _fold(candidate)
            if kind in _RUN_POOLS or kind in ("DATE_OF_BIRTH", "AGE"):
                break  # injective by construction; see the module docstring
            if kind == "URL" and folded == _fold(original):
                break  # not a per-user shape: kept exactly as written
            if folded in original_folds and folded != _fold(original):
                continue
            if owner.get((kind, folded), _fold(original)) != _fold(original):
                continue
            if folded == _fold(original):
                continue
            break
        else:
            raise AdlibPoolExhausted(f"no distinct {kind} stand-in left for this document")
        if kind in _PLUG_KINDS:
            variants[kind] = bump + 1
        owner[(kind, folded)] = _fold(original)
        memo[(kind, original)] = out[index] = candidate

    schools = [(o, memo[(k, o)]) for k, o in memo if k == SCHOOL]
    for index in acronyms:
        original = spans[index][1]
        if (SCHOOL, original) not in memo:
            match = next((acronym(stand_in) for full, stand_in in schools
                          if acronym(full) == original and acronym(stand_in) != original),
                         None)
            memo[(SCHOOL, original)] = match or engine.scramble(original, 0)
        out[index] = memo[(SCHOOL, original)]
    return Invented(tuple(out), dict(engine.tokens))


def invent_names(
    originals: Sequence[str],
    *,
    document: str,
    seed: int | str = 0,
    stop_words: frozenset[str] = frozenset(),
    given_name: Oracle | None = None,
    notable: Oracle | None = None,
    identity: Iterable[str] = (),
) -> dict[str, str]:
    """``{original span: stand-in}`` for NAME spans; :func:`invent` for all kinds."""
    result = invent([(NAME, o) for o in originals], document=document, seed=seed,
                    stop_words=stop_words, given_name=given_name, notable=notable,
                    identity=identity)
    return dict(zip(originals, result.stand_ins, strict=True))


def restore_adlib(text: str, restore_map: Mapping[str, str],
                  token_map: Mapping[str, str] | None = None) -> str:
    """Put originals back into text that carries adlib stand-ins (a model reply).

    One regex pass over every key of ``restore_map`` (``{stand-in: original}``)
    and ``token_map`` (``{invented token: original token}``), longest key first,
    case-insensitive, word-bounded. A whole-span key wins over a token key of
    the same text. The original is re-cased to the match: ALL CAPS, lower, or
    as written. So a lone "Quincy" restores to "Maria" even when the reply
    never repeats the full "Quincy Okafor".
    """
    table: dict[str, str] = {}
    for mapping in (restore_map, token_map or {}):
        for key, original in mapping.items():
            if key:
                table.setdefault(_fold(key), original)
    if not table:
        return text
    keys = sorted(table, key=lambda k: (-len(k), k))
    pattern = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(k) for k in keys) + r")(?!\w)",
                         re.IGNORECASE)

    def recase(match: re.Match[str]) -> str:
        seen = match.group(0)
        original = table[_fold(seen)]
        letters = "".join(_LETTERS.findall(seen))
        if letters.isupper() and len(letters) > 1:
            return original.upper()
        if letters.islower():
            return original.lower()
        return original

    return pattern.sub(recase, text)
