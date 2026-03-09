import datetime
import json
import os
import pathlib
import re
import shutil
import urllib.parse
import xml.sax.saxutils

import bleach
import jinja2
import markdown
from markupsafe import Markup


root_dir = pathlib.Path(__file__).resolve().parent
src = root_dir / "src"
site = src / "site"
out = root_dir / "out"
cms_data_dir = root_dir / "cms_data"
cms_assets_dir = cms_data_dir / "assets"
site_url = os.getenv("SITE_URL", "https://architekturaheleny.pl").rstrip("/")

CATEGORY_LABELS = {
    "all": "Wszystkie",
    "individual": "Autorskie",
    "team": "Zespołowe",
    "bachelors_thesis": "Praca inżynierska",
    "masters_thesis": "Praca magisterska",
}

CATEGORY_FILTERS = [
    {"key": key, "label": label}
    for key in (
        "all",
        "individual",
        "team",
        "bachelors_thesis",
        "masters_thesis",
    )
    if (label := str(CATEGORY_LABELS.get(key) or "").strip())
]


def build_category_filters(projects: list[dict]) -> list[dict]:
    active_categories = {
        category
        for project in projects
        for category in project.get("categories", [])
        if category
    }

    filters: list[dict] = [{"key": "all", "label": CATEGORY_LABELS["all"]}]
    for item in CATEGORY_FILTERS:
        key = item["key"]
        if key == "all":
            continue
        if key not in active_categories:
            continue
        filters.append(item)

    return filters


def absolute_url(path: str) -> str:
    normalized = str(path or "").strip()
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return f"{site_url}{normalized}"


def seo_description(content: str | None, fallback: str, max_length: int = 150) -> str:
    text = (content or "").replace("\r", "\n")
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^\)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^\)]*\)", r"\1", text)
    text = re.sub(r"[#>*_~\-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    if not text:
        text = fallback

    if len(text) <= max_length:
        return text

    truncated = text[:max_length].rsplit(" ", 1)[0].strip()
    if not truncated:
        truncated = text[:max_length].strip()
    return truncated


def normalize_iso_date(value: str | None, fallback_date: str) -> str:
    if not value:
        return fallback_date
    try:
        return (
            datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
            .date()
            .isoformat()
        )
    except ValueError:
        return fallback_date


def write_sitemap_and_robots(projects: list[dict], build_date: str) -> None:
    urls: list[dict] = [
        {
            "loc": absolute_url("/"),
            "changefreq": "weekly",
            "priority": "1.0",
            "lastmod": build_date,
        },
        {
            "loc": absolute_url("/o-mnie/"),
            "changefreq": "monthly",
            "priority": "0.6",
            "lastmod": build_date,
        },
    ]

    for project in projects:
        urls.append(
            {
                "loc": absolute_url(f"/projekty/{project['url']}/"),
                "changefreq": "weekly",
                "priority": "0.8",
                "lastmod": project.get("updated_date") or build_date,
            }
        )

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]

    for item in urls:
        lines.extend(
            [
                "  <url>",
                f"    <loc>{xml.sax.saxutils.escape(item['loc'])}</loc>",
                f"    <lastmod>{item['lastmod']}</lastmod>",
                f"    <changefreq>{item['changefreq']}</changefreq>",
                f"    <priority>{item['priority']}</priority>",
                "  </url>",
            ]
        )

    lines.append("</urlset>")
    (out / "sitemap.xml").write_text("\n".join(lines) + "\n")

    robots_content = "\n".join(
        [
            "User-agent: *",
            "Allow: /",
            f"Sitemap: {absolute_url('/sitemap.xml')}",
            "",
        ]
    )
    (out / "robots.txt").write_text(robots_content)


def normalize_url_path(url: str) -> str:
    return str(url).strip().strip("/")


def relation_sort_key(relation: dict) -> tuple:
    sort_number = relation.get("sort_number")
    relation_id = relation.get("id")

    if sort_number is not None:
        return (0, sort_number, relation_id or 0)

    return (1, 0, relation_id or 0)


def group_relations_by_parent(
    relations: list[dict], parent_key: str
) -> dict[int, list[dict]]:
    grouped: dict[int, list[dict]] = {}
    for relation in relations:
        parent_id = relation.get(parent_key)
        if parent_id is None:
            continue
        grouped.setdefault(parent_id, []).append(relation)
    return grouped


def collect_files_from_relations(
    relations: list[dict],
    files_map: dict,
    used_ids: set[str] | None = None,
    required_type_prefix: str | None = None,
) -> list[dict]:
    collected: list[dict] = []
    for relation in relations:
        file_id = relation.get("directus_files_id")
        if not file_id:
            continue

        file_id_str = str(file_id)
        if used_ids is not None and file_id_str in used_ids:
            continue

        file_data = files_map.get(file_id_str)
        if not file_data:
            continue

        if required_type_prefix and not str(file_data.get("type") or "").startswith(
            required_type_prefix
        ):
            continue

        if used_ids is not None:
            used_ids.add(file_id_str)
        collected.append(file_data)

    return collected


def process_path(
    p: pathlib.Path, context: dict, environment: jinja2.Environment
) -> None:
    rel_src = p.relative_to(src)
    rel_site = p.relative_to(site)

    out_file = out / rel_site
    out_file.parent.mkdir(parents=True, exist_ok=True)

    if p.is_file() and p.name.endswith(".html"):
        content = environment.get_template(str(rel_src)).render(context)
        inline_css = context.get("inline_global_css")
        if inline_css:
            content = content.replace("/*INLINE_GLOBAL_CSS*/", inline_css, 1).strip()
    elif p.is_dir():
        for nested_path in p.iterdir():
            process_path(nested_path, context, environment)
        return
    else:
        content = p.read_bytes()
        out_file.write_bytes(content)
        return

    out_file.write_text(content)


def load_json(path: pathlib.Path) -> dict:
    if not path.exists():
        return {"data": []}
    return json.loads(path.read_text())


def load_projects() -> list[dict]:
    posts = load_json(cms_data_dir / "items" / "architekturahelenypl_post.json").get(
        "data", []
    )
    main_relations = load_json(
        cms_data_dir / "items" / "architekturahelenypl_post_files.json"
    ).get("data", [])
    other_relations = load_json(
        cms_data_dir / "items" / "architekturahelenypl_post_files_1.json"
    ).get("data", [])
    video_relations = load_json(
        cms_data_dir / "items" / "architekturahelenypl_post_files_2.json"
    ).get("data", [])
    files_map = load_json(cms_data_dir / "files_index.json")

    relation_by_post = group_relations_by_parent(
        main_relations,
        "architekturahelenypl_post_id",
    )
    other_relation_by_post = group_relations_by_parent(
        other_relations,
        "architekturahelenypl_post_id",
    )
    video_relation_by_post = group_relations_by_parent(
        video_relations,
        "architekturahelenypl_post_id",
    )

    projects: list[dict] = []
    for post in posts:
        post_status = str(post["status"]).strip()
        if post_status != "published":
            continue

        title = str(post["title"]).strip()
        post_id = post["id"]
        project_url = normalize_url_path(str(post["url"]).strip())

        sorted_carousel_relations = sorted(
            relation_by_post.get(post_id, []),
            key=relation_sort_key,
        )
        sorted_other_relations = sorted(
            other_relation_by_post.get(post_id, []),
            key=relation_sort_key,
        )
        sorted_video_relations = sorted(
            video_relation_by_post.get(post_id, []),
            key=relation_sort_key,
        )

        main_page_image_id = post.get("main_page_image")

        main_image = (
            files_map.get(str(main_page_image_id)) if main_page_image_id else None
        )

        used_ids: set[str] = set()
        if main_page_image_id:
            used_ids.add(str(main_page_image_id))

        carousel_images = collect_files_from_relations(
            sorted_carousel_relations,
            files_map,
            used_ids=used_ids,
        )
        other_images = collect_files_from_relations(
            sorted_other_relations,
            files_map,
            used_ids=used_ids,
        )
        videos = collect_files_from_relations(
            sorted_video_relations,
            files_map,
            required_type_prefix="video/",
        )

        cover_image = main_image or (carousel_images[0] if carousel_images else None)
        if not cover_image and other_images:
            cover_image = other_images[0]

        categories: list[str] = []
        for value in post["category"]:
            category_key = str(value).strip()
            if not category_key:
                continue
            if not str(CATEGORY_LABELS.get(category_key) or "").strip():
                continue
            if category_key not in categories:
                categories.append(category_key)

        projects.append(
            {
                "id": post_id,
                "title": title,
                "url": project_url,
                "categories": categories,
                "updated_date": normalize_iso_date(
                    post.get("date_updated") or post.get("date_created"),
                    fallback_date=datetime.datetime.now(datetime.UTC)
                    .date()
                    .isoformat(),
                ),
                "localization": post.get("localization"),
                "authors": post.get("authors"),
                "project_status": post.get("project_status"),
                "surface": post.get("surface"),
                "long_description": post.get("long_description") or "",
                "seo_description": seo_description(
                    post.get("long_description") or "",
                    fallback=f"{title} - projekt architektoniczny.",
                ),
                "cover_image": cover_image,
                "main_image": main_image,
                "carousel_images": carousel_images,
                "other_images": other_images,
                "videos": videos,
            }
        )

    return projects


def load_site_content() -> dict:
    data = load_json(cms_data_dir / "items" / "architekturahelenypl_data.json").get(
        "data", {}
    )
    data_relations = load_json(
        cms_data_dir / "items" / "architekturahelenypl_data_files.json"
    ).get("data", [])
    files_map = load_json(cms_data_dir / "files_index.json")

    row = data if isinstance(data, dict) else {}

    about_me = (row.get("about_me") or "").strip()
    main_page_description = (row.get("main_page_description") or "").strip()
    about_me_page_description = (row.get("about_me_page_description") or "").strip()
    data_id = row.get("id")

    sorted_data_relations = sorted(
        [
            relation
            for relation in data_relations
            if relation.get("architekturahelenypl_data_id") == data_id
        ],
        key=relation_sort_key,
    )
    about_me_images = collect_files_from_relations(
        sorted_data_relations,
        files_map,
        used_ids=set(),
    )

    about_me_main_image = None
    about_me_main_image_id = row.get("image")
    if about_me_main_image_id:
        about_me_main_image = files_map.get(str(about_me_main_image_id))
        if about_me_main_image:
            about_me_images = [
                about_me_main_image,
                *[
                    img
                    for img in about_me_images
                    if img.get("id") != about_me_main_image.get("id")
                ],
            ]

    return {
        "about_me": about_me,
        "main_page_description": main_page_description,
        "about_me_page_description": about_me_page_description,
        "about_me_images": about_me_images,
    }


def render_project_pages(environment: jinja2.Environment, context: dict) -> None:
    detail_template = environment.get_template("components/project_detail.html")

    for project in context["projects"]:
        image = project.get("main_image") or project.get("cover_image")
        image_path = None
        if image:
            image_path = image.get("desktop_asset_path") or image.get("asset_path")

        detail_context = {
            **context,
            "project": project,
            "seo_title": f"Architektura Heleny | {project['title']}",
            "seo_description": project["seo_description"],
            "canonical_url": absolute_url(f"/projekty/{project['url']}/"),
            "og_type": "article",
            "og_image_url": absolute_url(image_path) if image_path else "",
            "twitter_card": "summary_large_image" if image_path else "summary",
        }

        destination = out / "projekty" / project["url"] / "index.html"
        destination.parent.mkdir(parents=True, exist_ok=True)
        rendered = detail_template.render(detail_context)
        inline_css = context.get("inline_global_css")
        if inline_css:
            rendered = rendered.replace("/*INLINE_GLOBAL_CSS*/", inline_css, 1)
        destination.write_text(rendered)


if __name__ == "__main__":
    build_date = datetime.datetime.now(datetime.UTC).date().isoformat()

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(exist_ok=True)

    if cms_assets_dir.exists():
        shutil.copytree(cms_assets_dir, out / "cms_assets", dirs_exist_ok=True)

    environment = jinja2.Environment(
        loader=jinja2.FileSystemLoader(src),
        autoescape=jinja2.select_autoescape(
            enabled_extensions=("html", "xml"),
            default_for_string=True,
        ),
    )

    def asset_url_filter(value: str | None) -> str:
        if not value:
            return ""

        string_value = str(value)
        if "://" in string_value:
            parsed = urllib.parse.urlsplit(string_value)
            return urllib.parse.urlunsplit(
                (
                    parsed.scheme,
                    parsed.netloc,
                    urllib.parse.quote(parsed.path, safe="/-._~"),
                    parsed.query,
                    parsed.fragment,
                )
            )

        return urllib.parse.quote(string_value, safe="/-._~")

    def _responsive_candidates(image: dict | None) -> list[tuple[int, str]]:
        if not image:
            return []

        candidates: list[tuple[int, str]] = []
        responsive_paths = image["responsive_asset_paths"]

        for width_key, path in responsive_paths.items():
            width = int(width_key)
            candidates.append((width, str(path)))

        if candidates:
            candidates.sort(key=lambda item: item[0])
            return candidates

        mobile = image.get("mobile_asset_path") or image.get("asset_path")
        desktop = image.get("desktop_asset_path") or image.get("asset_path")
        if mobile:
            candidates.append((900, str(mobile)))
        if desktop:
            candidates.append((1800, str(desktop)))

        if candidates:
            dedup: dict[int, str] = {}
            for width, path in candidates:
                dedup[width] = path
            return sorted(dedup.items(), key=lambda item: item[0])

        asset_path = image.get("asset_path")
        if asset_path:
            width = image["width"]
            candidates.append((max(1, width), str(asset_path)))

        return candidates

    def image_default_src_filter(image: dict | None) -> str:
        if not image:
            return ""

        candidates = _responsive_candidates(image)
        if candidates:
            return candidates[-1][1]

        for key in (
            "largest_asset_path",
            "desktop_asset_path",
            "asset_path",
            "mobile_asset_path",
            "placeholder_asset_path",
        ):
            value = image.get(key)
            if value:
                return str(value)
        return ""

    def image_srcset_filter(image: dict | None) -> str:
        candidates = _responsive_candidates(image)
        if not candidates:
            return ""
        return ", ".join(
            f"{asset_url_filter(path)} {width}w" for width, path in candidates
        )

    def image_for_width_filter(image: dict | None, target_width: int) -> str:
        candidates = _responsive_candidates(image)
        if not candidates:
            return image_default_src_filter(image)

        target = target_width

        eligible = [item for item in candidates if item[0] <= target]
        if eligible:
            return eligible[-1][1]

        return candidates[0][1]

    allowed_tags = [
        "p",
        "br",
        "hr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "ul",
        "ol",
        "li",
        "blockquote",
        "strong",
        "em",
        "code",
        "pre",
        "a",
        "table",
        "thead",
        "tbody",
        "tr",
        "th",
        "td",
    ]
    allowed_attributes = {
        "a": ["href", "title", "target", "rel"],
        "th": ["colspan", "rowspan"],
        "td": ["colspan", "rowspan"],
    }

    def markdown_filter(content: str) -> Markup:
        markdown_converter = markdown.Markdown(
            extensions=[
                "markdown.extensions.fenced_code",
                "markdown.extensions.tables",
                "markdown.extensions.sane_lists",
                "markdown.extensions.extra",
            ],
            output_format="html",
        )
        rendered = markdown_converter.convert(content or "")
        sanitized = bleach.clean(
            rendered,
            tags=allowed_tags,
            attributes=allowed_attributes,
            protocols=["http", "https", "mailto"],
            strip=True,
        )
        return Markup(sanitized)

    environment.filters["markdown"] = markdown_filter
    environment.filters["asset_url"] = asset_url_filter
    environment.filters["image_default_src"] = image_default_src_filter
    environment.filters["image_srcset"] = image_srcset_filter
    environment.filters["image_for_width"] = image_for_width_filter

    projects = load_projects()
    category_filters = build_category_filters(projects)
    site_content = load_site_content()
    inline_global_css = (site / "global.css").read_text()
    context = {
        "build_year": datetime.datetime.now(datetime.UTC).year,
        "site_url": site_url,
        "projects": projects,
        "category_labels": CATEGORY_LABELS,
        "category_filters": category_filters,
        "about_me": site_content["about_me"],
        "main_page_description": site_content["main_page_description"],
        "about_me_page_description": site_content["about_me_page_description"],
        "about_me_images": site_content["about_me_images"],
        "inline_global_css": inline_global_css,
    }

    for path in site.iterdir():
        process_path(path, context, environment)

    render_project_pages(environment, context)
    write_sitemap_and_robots(projects=projects, build_date=build_date)
