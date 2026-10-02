#!/usr/bin/env python3
"""Static docs-site builder for okfsmith.

Reads Markdown sources from ``docs/src/*.md``, renders each through
``docs/templates/base.html`` with a small built-in Markdown-subset parser,
and writes the static site into ``docs/``.

Usage (from the repo root):
    python3 docs/build.py

Outputs:
    docs/<slug>.html            one page per source file (13 pages)
    docs/index.html             rendered from docs/index.md (the homepage)
    docs/assets/js/search-index.json   search index for the client-side search

Caching note (for whoever deploys this): the asset filenames under
docs/assets/ (docs.css, docs.js, search.js, search-index.json, logo.svg,
favicon.svg) are stable — there is deliberately NO content hashing, to keep
the build beginner-friendly. That is safe to deploy with far-future cache
headers (e.g. Cache-Control: max-age=31536000, immutable): if an asset's
contents change, the rebuild must also change the corresponding *.html
pages anyway, so bump the deploy / rely on the HTML (short cache) being
refetched. Never serve the HTML with immutable caching.

Stdlib only. Exits non-zero if a broken internal *.html link is found.
"""

import html as htmlmod
import json
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # docs/
SRC_DIR = HERE / "src"
TEMPLATE_FILE = HERE / "templates" / "base.html"
SEARCH_INDEX_FILE = HERE / "assets" / "js" / "search-index.json"

# ---------------------------------------------------------------------------
# Site structure (mirrors docs/SITEMAP.md)
# ---------------------------------------------------------------------------

SLUG_ORDER = [
    "install", "quickstart", "configuration",
    "cli", "dashboard", "dunning", "webhooks", "contracts", "database", "docker",
    "troubleshooting", "changelog", "roadmap",
]

# ---------------------------------------------------------------------------
# SEO / discoverability
# ---------------------------------------------------------------------------
#
# ASSUMPTION: the docs site is published to GitHub Pages as a project page
# at https://bilal-junaid-jiwani.github.io/invoiceguard/ with the docs site
# served from the repository's docs/ directory (Pages source: branch master,
# path /docs). Every canonical URL, og:url, and the sitemap/robots/llms.txt
# references are built on this base. If the site is published somewhere else,
# update this single constant and rebuild.

SITE_URL = "https://bilal-junaid-jiwani.github.io/invoiceguard/"


def page_url(slug):
    return SITE_URL + slug + ".html"


def jsonld_for_page(slug, title, description):
    """WebSite + TechArticle JSON-LD. Uses real page metadata only — no
    invented metrics, ratings, dates, or author claims."""
    data = [
        {
            "@context": "https://schema.org",
            "@type": "WebSite",
            "name": "invoiceguard docs",
            "url": SITE_URL,
        },
        {
            "@context": "https://schema.org",
            "@type": "TechArticle",
            "headline": title,
            "description": description,
            "inLanguage": "en",
            "isPartOf": {"@type": "WebSite", "name": "invoiceguard docs", "url": SITE_URL},
            "mainEntityOfPage": page_url(slug),
        },
    ]
    # Escape "</" as "<\/" so a "</script>" sequence in page metadata can
    # never terminate the JSON-LD block early ("<\/ " is valid JSON and
    # equivalent to "/" for parsers).
    return (
        '  <script type="application/ld+json">\n'
        + json.dumps(data, ensure_ascii=False, indent=2).replace("</", "<\\/")
        + "\n  </script>"
    )


def jsonld_for_home(title, description):
    """WebSite + WebPage JSON-LD for the docs home page. Uses real page
    metadata only — no invented metrics, ratings, dates, or author claims."""
    data = [
        {
            "@context": "https://schema.org",
            "@type": "WebSite",
            "name": "invoiceguard docs",
            "url": SITE_URL,
        },
        {
            "@context": "https://schema.org",
            "@type": "WebPage",
            "headline": title,
            "description": description,
            "inLanguage": "en",
            "isPartOf": {"@type": "WebSite", "name": "invoiceguard docs", "url": SITE_URL},
            "mainEntityOfPage": SITE_URL,
        },
    ]
    # Escape "</" as "<\/" so a "</script>" sequence in page metadata can
    # never terminate the JSON-LD block early ("<\/ " is valid JSON and
    # equivalent to "/" for parsers).
    return (
        '  <script type="application/ld+json">\n'
        + json.dumps(data, ensure_ascii=False, indent=2).replace("</", "<\\/")
        + "\n  </script>"
    )


def write_sitemap():
    """sitemap.xml: all 16 content pages + index, lastmod = build date."""
    today = __import__("datetime").date.today().isoformat()
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for slug in SLUG_ORDER:
        lines.append(
            "  <url>\n    <loc>%s</loc>\n    <lastmod>%s</lastmod>\n  </url>"
            % (page_url(slug), today)
        )
    # index.html is the real docs home page; list it so crawlers can discover
    # the site entry point.
    lines.append(
        "  <url>\n    <loc>%s</loc>\n    <lastmod>%s</lastmod>\n  </url>"
        % (SITE_URL + "index.html", today)
    )
    lines.append("</urlset>")
    (HERE / "sitemap.xml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_robots():
    (HERE / "robots.txt").write_text(
        "User-agent: *\nAllow: /\n\nSitemap: %ssitemap.xml\n" % SITE_URL,
        encoding="utf-8",
    )


def write_llms(pages):
    """llms.txt: short intro + one-line description per page for LLM discovery."""
    lines = [
        "# invoiceguard docs",
        "",
        "> okfsmith is an open-source command-line tool (pip install okfsmith) that",
        "> turns messy documentation — Markdown files, PDFs, Office docs, whole",
        "> folders — into a knowledge bundle following the Open Knowledge Format",
        "> (OKF). This is the documentation index.",
        "",
        "Canonical base: %s" % SITE_URL,
        "Source: https://github.com/Bilal-Junaid-Jiwani/okfsmith",
        "",
        "## Pages",
        "",
    ]
    for slug in SLUG_ORDER:
        page = pages[slug]
        lines.append(
            "- [%s](%s): %s" % (page["title"], page_url(slug), page["description"])
        )
    lines.append("")
    (HERE / "llms.txt").write_text("\n".join(lines), encoding="utf-8")

# (tab label, landing slug, page slugs in order)
TABS = [
    ("Getting started", "install", ["install", "quickstart", "configuration"]),
    ("User guide", "cli", ["cli", "dashboard", "dunning", "webhooks", "contracts", "database", "docker"]),
    ("Reference", "troubleshooting", ["troubleshooting", "changelog", "roadmap"]),
]

SIDEBAR_GROUPS = {
    "Getting started": [("Start here", ["install", "quickstart", "configuration"])],
    "User guide": [
        ("Workflows", ["cli", "dashboard", "dunning", "webhooks"]),
        ("Data", ["contracts", "database", "docker"]),
    ],
    "Reference": [("Help", ["troubleshooting", "changelog", "roadmap"])],
}

# Explicit next-page chain (SITEMAP §5): the core beginner flow plus the
# user-guide middle pages in tab order. ingesting -> providers wins over
# ingesting -> validation so the happy path never stalls.
NEXT = {
    "install": "quickstart",
    "quickstart": "configuration",
    "configuration": "cli",
    "cli": "dashboard",
    "dashboard": "dunning",
    "dunning": "webhooks",
    "webhooks": "contracts",
    "contracts": "database",
    "database": "docker",
    "docker": "troubleshooting",
    "troubleshooting": "changelog",
    "changelog": "roadmap",
}
PREV = {nxt: prv for prv, nxt in NEXT.items()}

# ---------------------------------------------------------------------------
# Markdown-subset parser
# ---------------------------------------------------------------------------

_CODE_RE = re.compile(r"`([^`\n]+?)`")
_IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_BOLD_RE = re.compile(r"\*\*([^*]+?)\*\*")
_ITALIC_RE = re.compile(r"(?<![*\w])\*([^*\n]+?)\*(?![*\w])")
_LIST_RE = re.compile(r"^(\s*)([-*]|\d+[.)])\s+(.*)$")
_HEADING_RE = re.compile(r"^(#{1,4})\s+(.*?)\s*$")
_ANCHOR_RE = re.compile(r"\{#([A-Za-z0-9_-]+)\}\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")

ADMON_TITLES = {"note": "Note", "tip": "Tip", "warning": "Warning", "danger": "Danger"}
ADMON_ICONS = {
    "note": '<svg width="18" height="18" viewBox="0 0 16 16" aria-hidden="true">'
            '<circle cx="8" cy="8" r="6.75" fill="none" stroke="currentColor" stroke-width="1.5"/>'
            '<path d="M8 7.25v3.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>'
            '<circle cx="8" cy="5" r="0.9" fill="currentColor"/></svg>',
    "tip": '<svg width="18" height="18" viewBox="0 0 16 16" aria-hidden="true">'
           '<path d="M8 1.75a4.5 4.5 0 0 0-2.6 8.15c.6.48 1.1 1.1 1.1 1.85V12h3v-.25c0-.75.5-1.37 1.1-1.85A4.5 4.5 0 0 0 8 1.75z"'
           ' fill="none" stroke="currentColor" stroke-width="1.5"/>'
           '<path d="M6.5 14h3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
    "warning": '<svg width="18" height="18" viewBox="0 0 16 16" aria-hidden="true">'
               '<path d="M8 2L14.5 13.5h-13L8 2z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>'
               '<path d="M8 6.5v3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>'
               '<circle cx="8" cy="11.25" r="0.9" fill="currentColor"/></svg>',
    "danger": '<svg width="18" height="18" viewBox="0 0 16 16" aria-hidden="true">'
              '<path d="M5.5 1.75h5L14.25 5.5v5L10.5 14.25h-5L1.75 10.5v-5L5.5 1.75z"'
              ' fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>'
              '<path d="M8 5.5v3.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>'
              '<circle cx="8" cy="11" r="0.9" fill="currentColor"/></svg>',
}


def esc(text):
    return htmlmod.escape(text, quote=False)


def slugify(text):
    """Turn heading text into a URL-safe anchor id."""
    text = re.sub(r"<[^>]+>", "", text)
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    return re.sub(r"\s+", "-", text.strip()).strip("-") or "section"


def inline(text):
    """Inline Markdown: `code`, **bold**, *italic*, [links](...)."""
    parts = []

    def stash_code(m):
        # m.group(1) is already escaped (the whole text was esc()'d above),
        # so it must NOT be escaped a second time.
        parts.append("<code>%s</code>" % m.group(1))
        return "\x00%d\x00" % (len(parts) - 1)

    s = _CODE_RE.sub(stash_code, esc(text))
    s = _IMG_RE.sub(
        lambda m: '<img src="%s" alt="%s" loading="lazy">'
        % (htmlmod.escape(m.group(2), quote=True), htmlmod.escape(m.group(1), quote=True)),
        s,
    )
    s = _LINK_RE.sub(
        lambda m: '<a href="%s">%s</a>'
        % (htmlmod.escape(m.group(2), quote=True), m.group(1)),
        s,
    )
    s = _BOLD_RE.sub(r"<strong>\1</strong>", s)
    s = _ITALIC_RE.sub(r"<em>\1</em>", s)
    return re.sub(r"\x00(\d+)\x00", lambda m: parts[int(m.group(1))], s)


def split_row(line):
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def render_table(header, rows):
    th = "".join("<th>%s</th>" % inline(cell) for cell in header)
    body = "".join(
        "<tr>%s</tr>" % "".join("<td>%s</td>" % inline(cell) for cell in row)
        for row in rows
    )
    return (
        '<div class="table-wrap"><table><thead><tr>%s</tr></thead>'
        "<tbody>%s</tbody></table></div>" % (th, body)
    )


def _render_list(node):
    parts = ["<%s>" % node["tag"]]
    for item in node["items"]:
        parts.append("<li>%s" % inline(item["text"]))
        for child in item["children"]:
            parts.append(_render_list(child))
        parts.append("</li>")
    parts.append("</%s>" % node["tag"])
    return "\n".join(parts)


def render_lists(rows):
    """rows: [(indent, 'ul'|'ol', text)] -> nested <ul>/<ol> HTML."""
    roots = []
    stack = []  # [indent, node]
    for indent, tag, text in rows:
        while stack and indent < stack[-1][0]:
            stack.pop()
        node = None
        if stack and stack[-1][0] == indent and stack[-1][1]["tag"] == tag:
            node = stack[-1][1]
        else:
            if stack and stack[-1][0] == indent:
                stack.pop()  # same level, different list kind: close it
            node = {"tag": tag, "items": []}
            if stack:
                stack[-1][1]["items"][-1]["children"].append(node)
            else:
                roots.append(node)
            stack.append([indent, node])
        node["items"].append({"text": text, "children": []})
    return "\n".join(_render_list(node) for node in roots)


def render_admonition(kind, raw_lines):
    """GitHub-style > [!NOTE]/[!TIP]/[!WARNING]/[!DANGER] blocks."""
    paras, cur = [], []
    for b in raw_lines:
        if b == "":
            if cur:
                paras.append(cur)
                cur = []
        else:
            cur.append(b)
    if cur:
        paras.append(cur)
    body = "".join("<p>%s</p>" % inline(" ".join(p)) for p in paras)
    return (
        '<aside class="admonition %s"><span class="admon-icon" aria-hidden="true">%s</span>'
        '<div class="admon-body"><p class="admon-title">%s</p>%s</div></aside>'
        % (kind, ADMON_ICONS[kind], ADMON_TITLES[kind], body)
    )


def render_toc(headings):
    """headings: [(level, id, text)] for h2/h3 -> nested TOC list."""
    tree, current = [], None
    for level, hid, text in headings:
        if level == 2 or current is None:
            current = {"id": hid, "text": text, "children": []}
            tree.append(current)
        else:
            current["children"].append((hid, text))
    parts = ['<ul class="toc-list">']
    for node in tree:
        parts.append('<li><a href="#%s">%s</a>' % (node["id"], esc(node["text"])))
        if node["children"]:
            parts.append('<ul class="toc-sub">')
            for hid, text in node["children"]:
                parts.append('<li><a href="#%s">%s</a></li>' % (hid, esc(text)))
            parts.append("</ul>")
        parts.append("</li>")
    parts.append("</ul>")
    return "\n".join(parts) if tree else ""


def parse_markdown(text):
    """Parse the Markdown subset. Returns (content_html, headings)."""
    lines = text.splitlines()
    out = []
    headings = []  # (level, id, plain_text)
    used_ids = {}
    para = []

    def flush_para():
        if para:
            out.append("<p>%s</p>" % inline(" ".join(para)))
            para.clear()

    def claim_id(base):
        hid, k = base, 2
        while hid in used_ids:
            hid = "%s-%d" % (base, k)
            k += 1
        used_ids[hid] = True
        return hid

    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        s = line.strip()

        if not s:
            flush_para()
            i += 1
            continue

        # Fenced code block -> <pre class="codeblock" data-lang="..."><code>
        if s.startswith("```"):
            flush_para()
            lang = s[3:].strip()
            i += 1
            buf = []
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(lines[i].rstrip("\n"))
                i += 1
            i += 1  # skip the closing fence
            out.append(
                '<pre class="codeblock" data-lang="%s"><code>%s\n</code></pre>'
                % (htmlmod.escape(lang, quote=True), esc("\n".join(buf)))
            )
            continue

        # Admonition: > [!KIND] followed by > lines (multi-line until blank)
        m = re.match(r"^>\s*\[!(NOTE|TIP|WARNING|DANGER)\]\s*$", s)
        if m:
            flush_para()
            kind = m.group(1).lower()
            i += 1
            raw = []
            while i < n and lines[i].lstrip().startswith(">"):
                raw.append(lines[i].lstrip()[1:].strip())
                i += 1
            out.append(render_admonition(kind, raw))
            continue

        # Plain blockquote (non-admonition)
        if s.startswith(">"):
            flush_para()
            buf = []
            while i < n and lines[i].lstrip().startswith(">"):
                buf.append(lines[i].lstrip()[1:].strip())
                i += 1
            out.append("<blockquote><p>%s</p></blockquote>" % inline(" ".join(buf)))
            continue

        # Raw HTML block: passed through untouched.
        # <details>..</details> spans lines; other block HTML ends at a blank line.
        tag_m = re.match(r"^</?([A-Za-z][A-Za-z0-9]*)", s)
        if tag_m:
            flush_para()
            tag = tag_m.group(1).lower()
            buf = [line.rstrip("\n")]
            i += 1
            if tag == "details":
                while i < n and lines[i].strip() != "</details>":
                    buf.append(lines[i].rstrip("\n"))
                    i += 1
                if i < n:
                    buf.append(lines[i].rstrip("\n"))
                    i += 1
            else:
                while i < n and lines[i].strip() != "":
                    buf.append(lines[i].rstrip("\n"))
                    i += 1
            out.append("\n".join(buf))
            continue

        # Table: | header | row followed by a | --- | separator row
        if (
            s.startswith("|")
            and i + 1 < n
            and "-" in lines[i + 1]
            and _TABLE_SEP_RE.match(lines[i + 1])
        ):
            flush_para()
            header = split_row(s)
            i += 2
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append(split_row(lines[i].strip()))
                i += 1
            out.append(render_table(header, rows))
            continue

        # Headings ## / ### (#### tolerated), honoring explicit {#anchor}
        m = _HEADING_RE.match(s)
        if m:
            flush_para()
            level = len(m.group(1))
            text = m.group(2)
            am = _ANCHOR_RE.search(text)
            if am:
                hid = claim_id(am.group(1))
                text = text[: am.start()].rstrip()
            else:
                hid = claim_id(slugify(text))
            html_text = inline(text)
            plain = htmlmod.unescape(re.sub(r"<[^>]+>", "", html_text))
            if level in (2, 3):
                headings.append((level, hid, plain))
            out.append(
                '<h%d id="%s">%s</h%d>'
                % (level, htmlmod.escape(hid, quote=True), html_text, level)
            )
            i += 1
            continue

        # Horizontal rule
        if re.match(r"^---+\s*$", s):
            flush_para()
            out.append("<hr>")
            i += 1
            continue

        # Lists (- / 1.), with nesting by indent
        m = _LIST_RE.match(line)
        if m:
            flush_para()
            rows = []
            while i < n:
                lm = _LIST_RE.match(lines[i])
                if lm:
                    indent = len(lm.group(1).expandtabs(4))
                    tag = "ul" if lm.group(2) in ("-", "*") else "ol"
                    rows.append((indent, tag, lm.group(3).strip()))
                    i += 1
                    continue
                cont = lines[i]
                if (
                    rows
                    and cont.strip()
                    and cont[:1] == " "
                    and not re.match(r"^\s*(#{1,4}\s|```|>|---+\s*$|\|)", cont)
                ):
                    rows[-1] = (
                        rows[-1][0],
                        rows[-1][1],
                        rows[-1][2] + " " + cont.strip(),
                    )
                    i += 1
                    continue
                break
            out.append(render_lists(rows))
            continue

        # Plain paragraph line
        para.append(s)
        i += 1

    flush_para()
    return "\n".join(out), headings


def read_frontmatter(text):
    """Split leading --- frontmatter (title/eyebrow/description) from the body."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    meta = {}
    for line in text[4:end].splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta, text[end + 5 :]


# ---------------------------------------------------------------------------
# Chrome builders (sidebar / tabs / prev-next)
# ---------------------------------------------------------------------------


def tab_of(slug):
    if slug == "index":
        # the docs home lives under the first tab, with nothing active
        return TABS[0]
    for name, landing, pages in TABS:
        if slug in pages:
            return name, landing, pages
    raise KeyError("slug %r not in any tab" % slug)


def build_tabs_html(active_slug):
    active_tab, _, _ = tab_of(active_slug)
    parts = []
    for name, landing, _ in TABS:
        attrs = ""
        if name == active_tab:
            attrs = ' data-active="true" aria-current="page"'
        parts.append(
            '<li class="tab-item"><a class="tab-link" href="%s.html"%s>%s</a></li>'
            % (landing, attrs, esc(name))
        )
    return "\n".join(parts)


def build_sidebar_html(active_slug, titles):
    active_tab, _, _ = tab_of(active_slug)
    parts = []
    for group_label, slugs in SIDEBAR_GROUPS[active_tab]:
        parts.append('<div class="nav-group">')
        parts.append('<p class="nav-group-label">%s</p>' % esc(group_label))
        parts.append('<ul class="nav-list">')
        for slug in slugs:
            attrs = ""
            if slug == active_slug:
                attrs = ' data-active="true" aria-current="page"'
            parts.append(
                '<li><a class="nav-item" href="%s.html"%s>%s</a></li>'
                % (slug, attrs, esc(titles[slug]))
            )
        parts.append("</ul></div>")
    return "\n".join(parts)


def build_nav_link(slug, titles, kind):
    """kind: 'prev' or 'next'. Returns '' when there is no neighbour."""
    target = (PREV if kind == "prev" else NEXT).get(slug)
    if not target:
        return ""
    arrow = "&larr;" if kind == "prev" else "&rarr;"
    label = "Previous" if kind == "prev" else "Next"
    if kind == "prev":
        inner = "%s %s" % (arrow, esc(titles[target]))
    else:
        inner = "%s %s" % (esc(titles[target]), arrow)
    return (
        '<a class="page-%s" href="%s.html">'
        '<span class="prev-next-label">%s</span>'
        '<span class="prev-next-title">%s</span></a>'
        % (kind, target, label, inner)
    )


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

HREF_RE = re.compile(r'href="([^"]+)"')
ID_RE = re.compile(r'\sid="([^"]+)"')


def plain_text(content_html):
    text = re.sub(r"<[^>]+>", " ", content_html)
    text = htmlmod.unescape(re.sub(r"\s+", " ", text)).strip()
    return text


def main():
    if not SRC_DIR.is_dir():
        print("error: %s not found; run from the repo root" % SRC_DIR, file=sys.stderr)
        return 2
    if not TEMPLATE_FILE.is_file():
        print("error: template %s not found" % TEMPLATE_FILE, file=sys.stderr)
        return 2

    template = TEMPLATE_FILE.read_text(encoding="utf-8")

    # --- parse every source page first (titles feed sidebar/prev-next) ---
    pages = {}
    for slug in SLUG_ORDER:
        src_file = SRC_DIR / (slug + ".md")
        if not src_file.is_file():
            print("error: missing source %s" % src_file, file=sys.stderr)
            return 2
        meta, body = read_frontmatter(src_file.read_text(encoding="utf-8"))
        content_html, headings = parse_markdown(body)
        pages[slug] = {
            "title": meta.get("title", slug),
            "eyebrow": meta.get("eyebrow", ""),
            "description": meta.get("description", ""),
            "content_html": content_html,
            "headings": headings,
        }
    titles = {slug: pages[slug]["title"] for slug in SLUG_ORDER}

    # --- render ---
    written = []
    for slug in SLUG_ORDER:
        page = pages[slug]
        ctx = {
            "title": esc(page["title"]),
            "description": htmlmod.escape(page["description"], quote=True),
            "canonical": htmlmod.escape(page_url(slug), quote=True),
            "jsonld": jsonld_for_page(slug, page["title"], page["description"]),
            "eyebrow": esc(page["eyebrow"]),
            "h1": esc(page["title"]),
            "content_html": page["content_html"],
            "sidebar_html": build_sidebar_html(slug, titles),
            "toc_html": render_toc(page["headings"]),
            "tabs_html": build_tabs_html(slug),
            "prev_link": build_nav_link(slug, titles, "prev"),
            "next_link": build_nav_link(slug, titles, "next"),
            "active_slug": slug,
        }
        rendered = template
        for key, value in ctx.items():
            rendered = rendered.replace("{{" + key + "}}", value)
        leftover = re.findall(r"\{\{\w+\}\}", rendered)
        if leftover:
            print("warning: unreplaced placeholders on %s: %s" % (slug, leftover))
        out_file = HERE / (slug + ".html")
        out_file.write_text(rendered, encoding="utf-8")
        written.append(out_file)

    # --- homepage: render docs/index.md as the real docs home (never a copy
    #     of another page — a byte-copy here once shipped install.html as the
    #     site root with the wrong title and canonical) ---
    index_src = HERE / "index.md"
    if index_src.is_file():
        _, index_body = read_frontmatter(index_src.read_text(encoding="utf-8"))
        index_content_html, index_headings = parse_markdown(index_body)
        first_para = ""
        for block in re.split(r"\n\s*\n", index_body.strip()):
            b = block.strip()
            if b and not b.startswith("#"):
                first_para = plain_text(parse_markdown(b)[0])
                break
        index_h1 = "invoiceguard documentation"
        for level, _hid, plain in index_headings:
            if level == 1:
                index_h1 = plain
                break
        # the template renders {{h1}} itself — drop the duplicate h1 from the content
        index_content_html = re.sub(
            r"<h1[^>]*>.*?</h1>\s*", "", index_content_html, count=1, flags=re.S
        )
        index_ctx = {
            "title": esc("invoiceguard docs"),
            "description": htmlmod.escape(first_para, quote=True),
            "canonical": htmlmod.escape(SITE_URL, quote=True),
            "jsonld": jsonld_for_home("invoiceguard docs", first_para),
            "eyebrow": esc("Overview"),
            "h1": esc(index_h1),
            "content_html": index_content_html,
            "sidebar_html": build_sidebar_html("index", titles),
            "toc_html": render_toc(index_headings),
            "tabs_html": build_tabs_html("index").replace(
                ' data-active="true" aria-current="page"', ""
            ),
            "prev_link": "",
            "next_link": (
                '<a class="page-next" href="install.html">'
                '<span class="prev-next-label">Next</span>'
                '<span class="prev-next-title">%s &rarr;</span></a>'
                % esc(titles["install"])
            ),
            "active_slug": "index",
        }
        rendered = template
        for key, value in index_ctx.items():
            rendered = rendered.replace("{{" + key + "}}", value)
        # home page title is just "invoiceguard docs" (not "invoiceguard docs — invoiceguard docs")
        rendered = rendered.replace(
            "<title>invoiceguard docs — invoiceguard docs</title>",
            "<title>invoiceguard docs</title>",
            1,
        )
        rendered = rendered.replace(
            '<meta property="og:title" content="invoiceguard docs — invoiceguard docs">',
            '<meta property="og:title" content="invoiceguard docs">',
            1,
        )
        rendered = rendered.replace(
            '<meta name="twitter:title" content="invoiceguard docs — invoiceguard docs">',
            '<meta name="twitter:title" content="invoiceguard docs">',
            1,
        )
        # brand link points home on the home page
        rendered = rendered.replace(
            '<a class="brand" href="install.html">',
            '<a class="brand" href="index.html">',
            1,
        )
        leftover = re.findall(r"\{\{\w+\}\}", rendered)
        if leftover:
            print("warning: unreplaced placeholders on index: %s" % leftover)
        (HERE / "index.html").write_text(rendered, encoding="utf-8")
        written.append(HERE / "index.html")
    else:
        print("warning: docs/index.md missing; homepage not rebuilt")

    # --- discoverability: sitemap.xml, robots.txt, llms.txt ---
    write_sitemap()
    write_robots()
    write_llms(pages)

    # --- search index ---
    SEARCH_INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    index = []
    for slug in SLUG_ORDER:
        page = pages[slug]
        index.append(
            {
                "url": slug + ".html",
                "title": page["title"],
                "eyebrow": page["eyebrow"],
                "description": page["description"],
                "headings": [
                    {"id": hid, "text": text} for _, hid, text in page["headings"]
                ],
                "text": plain_text(page["content_html"])[:4000],
            }
        )
    SEARCH_INDEX_FILE.write_text(
        json.dumps(index, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )

    # --- internal link check: every *.html href must resolve to a built page ---
    valid_files = {s + ".html" for s in SLUG_ORDER} | {"index.html"}
    anchors = {}
    broken = []
    for out_file in written:
        html_text = out_file.read_text(encoding="utf-8")
        anchors[out_file.name] = set(ID_RE.findall(html_text))
    anchor_warnings = []
    for out_file in written:
        html_text = out_file.read_text(encoding="utf-8")
        for m in HREF_RE.finditer(html_text):
            href = m.group(1)
            if href.startswith(("http://", "https://", "mailto:", "#", "data:")):
                continue
            path, _, frag = href.partition("#")
            path = path.split("?")[0]
            if not path:
                continue
            name = path.split("/")[-1]
            if name.endswith(".html"):
                if name not in valid_files:
                    broken.append((out_file.name, href))
                elif frag and frag not in anchors.get(name, set()) and name != out_file.name:
                    # cross-page anchor check (same-page anchors were parsed too,
                    # but tolerate them via the page's own id set)
                    if frag not in anchors.get(name, set()):
                        anchor_warnings.append((out_file.name, href))
            elif "." in name.split("/")[-1]:
                pass  # asset (css/js/svg) — not part of the page universe

    # --- summary ---
    print("invoiceguard docs build")
    print("  pages written : %d (%s)" % (len(written), ", ".join(f.name for f in written)))
    print("  homepage      : index.html (rendered from index.md)")
    print("  search index  : %s (%d entries)" % (SEARCH_INDEX_FILE, len(index)))
    if anchor_warnings:
        print("  anchor warnings (%d):" % len(anchor_warnings))
        for page, href in sorted(set(anchor_warnings)):
            print("    %s -> %s" % (page, href))
    if broken:
        print("  BROKEN internal links (%d):" % len(broken))
        for page, href in sorted(set(broken)):
            print("    %s -> %s" % (page, href))
        return 1
    print("  internal links: all *.html hrefs resolve (%d checked)" % sum(
        1
        for f in written
        for m in HREF_RE.finditer(f.read_text(encoding="utf-8"))
        if m.group(1).split("#")[0].endswith(".html")
        and not m.group(1).startswith(("http://", "https://"))
    ))
    print("build OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
