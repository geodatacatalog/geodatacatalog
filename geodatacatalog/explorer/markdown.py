"""Generate SOURCES.md (the GitHub-friendly view) from the registry."""
from __future__ import annotations

from pathlib import Path

from .registry import Source

_STATE_ICON = {
    "ok": "✅",
    "fail": "❌",
    "auth_required": "🔐",
    "skipped": "⏭",
    None: "❔",
}

_FETCH_ICON = {"ok": "✅", "empty": "∅", "fail": "❌",
               "auth_required": "🔐", "skipped": "⏭", None: "❔"}

#: Display order and titles of the thematic sections.
_SECTIONS = [
    ("satellite", "🛰️ Satellite imagery & Earth observation"),
    ("basemap", "🗺️ Basemaps & general purpose"),
    ("admin", "🏛️ Administrative, cadastre & urban"),
    ("elevation", "⛰️ Elevation & terrain"),
    ("landcover", "🌍 Land cover & land use"),
    ("fire", "🔥 Fire"),
    ("forest", "🌲 Forest"),
    ("agriculture", "🌾 Agriculture"),
    ("soil", "🟤 Soils"),
    ("geology", "🪨 Geology"),
    ("water", "💧 Inland water"),
    ("transport", "🚢 Transport & mobility"),
    ("ocean", "🌊 Oceans & marine"),
    ("hazards", "⚠️ Hazards & disasters"),
    ("weather", "🌦️ Weather & climate"),
    ("climate", "🌡️ Climate"),
    ("biodiversity", "🐾 Biodiversity & ecology"),
    ("infrastructure", "🚧 Infrastructure"),
    ("urban", "🏙️ Urban"),
    ("population", "👥 Population & demographics"),
    ("economy", "💶 Socio-economic"),
    ("meta", "📚 Meta-catalogues & registries"),
]

#: Explicit section overrides for the rare source whose intended category isn't
#: the first _SECTIONS entry it happens to match — e.g. a cloud-free satellite
#: mosaic curated specifically as a general-purpose world basemap, unlike other
#: satellite-themed sources that are EO/analysis products (time series, scientific
#: bands...). Keeps the source's other themes intact for search.
_SECTION_OVERRIDES: dict[str, str] = {
    "eox_s2cloudless": "basemap",
    "shom": "ocean",  # nautical charts & bathymetry, not a general-purpose basemap
    # geology sits right before hazards now (moved next to soil for adjacency) —
    # these two are active hazard trackers, not static geology maps, so pin them
    # to hazards rather than let the earlier geology theme grab them.
    "brgm_risques": "hazards",
    "usgs_earthquake": "hazards",
}

#: coverage -> flag/globe emoji shown as a compact badge (falls back to the
#: plain coverage text for any value not listed here).
_COVERAGE_FLAG = {
    "global": "🌐",
    "europe": "🇪🇺",
    "france": "🇫🇷",
    "usa": "🇺🇸",
    "uk": "🇬🇧",
    "australia": "🇦🇺",
    "africa": "🌍",
    "germany": "🇩🇪",
    "netherlands": "🇳🇱",
    "spain": "🇪🇸",
    "switzerland": "🇨🇭",
    "belgium": "🇧🇪",
    "canada": "🇨🇦",
    "norway": "🇳🇴",
    "new-zealand": "🇳🇿",
    "finland": "🇫🇮",
}

#: theme -> shields.io colour for the compact tag badges.
_THEME_COLOR = {
    "satellite": "blue",
    "imagery": "blue",
    "basemap": "lightgrey",
    "admin": "grey",
    "elevation": "slategray",
    "landcover": "yellowgreen",
    "forest": "forestgreen",
    "agriculture": "gold",
    "soil": "sienna",
    "water": "dodgerblue",
    "transport": "navy",
    "air": "skyblue",
    "maritime": "teal",
    "rail": "darkslategray",
    "ocean": "blue",
    "hazards": "red",
    "fire": "orangered",
    "weather": "lightblue",
    "climate": "orange",
    "biodiversity": "limegreen",
    "geology": "brown",
    "infrastructure": "darkgrey",
    "urban": "purple",
    "population": "hotpink",
    "economy": "gold",
    "meta": "blueviolet",
}

#: type -> shields.io colour for the compact type badge.
_TYPE_COLOR = {
    "stac": "blueviolet",
    "wfs": "green",
    "wms": "blue",
    "wcs": "teal",
    "wmts": "cadetblue",
    "rest": "orange",
    "xyz": "gold",
    "csw": "grey",
    "registry": "lightgrey",
}


def _grouped_sections(sources: list[Source]) -> list[tuple[str, list[Source]]]:
    """Group sources into their display sections, in ``_SECTIONS`` order.

    A source is placed under the *first* matching theme in ``_SECTIONS``
    order (not the order of its own ``themes`` list) — with two exceptions:
    ``_SECTION_OVERRIDES`` pins specific sources to a specific section, and
    registry/CSW catalogues also tagged ``meta`` are always grouped under
    Meta regardless of which domain theme would otherwise claim them first
    (those domain themes exist so search can surface them, not to place them).
    Anything matching no section lands in a trailing "Other" group.
    """
    forced_meta = [s for s in sources if "meta" in s.themes and s.type in ("registry", "csw")]

    def _matches(s: Source, theme: str) -> bool:
        return theme in s.themes and _SECTION_OVERRIDES.get(s.id, theme) == theme

    groups: list[tuple[str, list[Source]]] = []
    placed: set[str] = {s.id for s in forced_meta}
    for theme, title in _SECTIONS:
        if theme == "meta":
            rows = forced_meta + [s for s in sources if s.id not in placed and _matches(s, theme)]
        else:
            rows = [s for s in sources if s.id not in placed and _matches(s, theme)]
        if not rows:
            continue
        placed.update(s.id for s in rows)
        groups.append((title, rows))

    leftovers = [s for s in sources if s.id not in placed]
    if leftovers:
        groups.append(("Other", leftovers))
    return groups


def _coverage_badge(s: Source) -> str:
    return _COVERAGE_FLAG.get(s.coverage, s.coverage)


def _type_badge(s: Source) -> str:
    t = s.type.upper()
    color = _TYPE_COLOR.get(s.type, "lightgrey")
    return f"![{t}](https://img.shields.io/badge/{t}-{color})"


def _status_badge(s: Source) -> str:
    """3-state status icon: reachability alone isn't enough to know a source is
    actually usable, so this folds in the separate `fetch` test result too.

    ✅ reachable AND a real fetch test returned data · 🟡 reachable but the
    fetch test failed/was empty/hasn't been run · anything else (❌ unreachable,
    🔐 needs credentials, ⏭ skipped, ❔ never checked) is a status problem on
    its own, shown as-is.
    """
    status_state = (s.status or {}).get("state")
    fetch_state = (s.fetch or {}).get("state")
    if status_state == "ok" and fetch_state == "ok":
        return "✅"
    if status_state == "ok":
        return "🟡"
    return _STATE_ICON.get(status_state, "❔")


def _tag_badges(s: Source) -> str:
    return " ".join(
        f"![{t}](https://img.shields.io/badge/{t}-{_THEME_COLOR.get(t, 'lightgrey')})"
        for t in s.themes
    )


def _truncate(text: str, limit: int = 110) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut + "…"


def _short_description(s: Source) -> str:
    if s.summary.strip():
        return s.summary.strip()
    return _truncate(s.notes)


def render_markdown(
    sources: list[Source],
    *,
    sources_yml_link: str = "geodatacatalog/explorer/sources.yml",
    intro: str | None = None,
    cli_hints: bool = True,
) -> str:
    checked = [s.status.get("checked_at") for s in sources if s.status]
    last_check = max(checked) if checked else "never"

    lines = ["# Open geospatial data sources", ""]
    if intro:
        lines += [intro, ""]
    if cli_hints:
        lines += [
            "> **Generated file — do not edit.** The source of truth is",
            f"> [`{sources_yml_link}`]({sources_yml_link});",
            "> regenerate with `geodatacatalog check` (probes every service) or `geodatacatalog md`.",
            ">",
            f"> {len(sources)} sources · last check: {last_check} · search them with",
            '> `geodatacatalog search "forêt"` (FR/EN) and turn them into a config with',
            "> `geodatacatalog add <source> <layer> -o catalog.yml`.",
            "",
        ]
    else:
        lines += [
            f"> **Generated file — do not edit.** The source of truth is [`{sources_yml_link}`]({sources_yml_link}).",
            f"> {len(sources)} sources · last check: {last_check}.",
            "",
        ]
    lines += [
        "Each entry: name · type · coverage · last-check status · 🔒 if credentials are needed "
        "(linked to the registration page when known) · 💰 if there's no free tier · coloured "
        "theme tags. Click **Détails** for the full note.",
        "Status: ✅ reachable & fetch confirmed · 🟡 reachable, fetch not confirmed "
        "(failed/empty/untested) · ❌ unreachable · 🔐 credentials required · ⏭ skipped · ❔ not checked",
        "",
    ]

    def _block(s: Source) -> list[str]:
        badges = [f"**[{s.name}]({s.endpoint})** (`{s.id}`)"]
        if s.paid:
            badges.append("💰")
        badges.append(_type_badge(s))
        badges.append(_coverage_badge(s))
        badges.append(_status_badge(s))
        if s.auth != "none":
            badges.append(f"[🔒]({s.auth_url})" if s.auth_url else "🔒")
        tags = _tag_badges(s)
        head = " ".join(badges) + (f" · {tags}" if tags else "")
        block = [f"- {head}", f"  {_short_description(s)}"]

        full_notes = s.notes.strip()
        extra = ""
        f = s.fetch or {}
        if f.get("state") not in (None, "ok"):
            icon = _FETCH_ICON.get(f.get("state"), "❔")
            extra = f"\n\n  Fetch test: {icon} {f.get('detail') or f.get('state')}"
        show_notes = full_notes and full_notes != _short_description(s)
        if show_notes or extra:
            body = f"{full_notes}{extra}".strip()
            block.append(f"  <details><summary>Détails</summary>\n\n  {body}\n\n  </details>")
        return block

    for title, rows in _grouped_sections(sources):
        lines += [f"## {title}", ""]
        for s in rows:
            lines += _block(s)
        lines.append("")

    return "\n".join(lines)


def write_markdown(
    sources: list[Source],
    path: str | Path = "SOURCES.md",
    *,
    sources_yml_link: str = "geodatacatalog/explorer/sources.yml",
    intro: str | None = None,
    cli_hints: bool = True,
) -> Path:
    path = Path(path)
    path.write_text(
        render_markdown(sources, sources_yml_link=sources_yml_link, intro=intro, cli_hints=cli_hints),
        encoding="utf-8",
    )
    return path
