import datetime
import json
import os
import pathlib
import re
import shutil
import urllib.parse
import xml.sax.saxutils

import jinja2
import markdown


root_dir = pathlib.Path(__file__).resolve().parent
src = root_dir / "src"
site = src / "site"
out = root_dir / "out"
cms_data_dir = root_dir / "cms_data"
cms_assets_dir = cms_data_dir / "assets"
site_url = os.getenv("SITE_URL", "https://architekturaheleny.pl").rstrip("/")


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
    return str(url or "").strip().strip("/")


def relation_sort_key(relation: dict) -> tuple:
    sort_number = relation.get("sort_number")
    relation_id = relation.get("id")

    if isinstance(sort_number, (int, float)):
        return (0, sort_number, relation_id or 0)

    return (1, 0, relation_id or 0)


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
        if isinstance(inline_css, str) and inline_css:
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

    relation_by_post: dict[int, list[dict]] = {}
    for relation in main_relations:
        post_id = relation.get("architekturahelenypl_post_id")
        if post_id is None:
            continue
        relation_by_post.setdefault(post_id, []).append(relation)

    other_relation_by_post: dict[int, list[dict]] = {}
    for relation in other_relations:
        post_id = relation.get("architekturahelenypl_post_id")
        if post_id is None:
            continue
        other_relation_by_post.setdefault(post_id, []).append(relation)

    video_relation_by_post: dict[int, list[dict]] = {}
    for relation in video_relations:
        post_id = relation.get("architekturahelenypl_post_id")
        if post_id is None:
            continue
        video_relation_by_post.setdefault(post_id, []).append(relation)

    projects: list[dict] = []
    for post in posts:
        if post.get("status") != "published":
            continue

        title = (post.get("title") or "Bez tytułu").strip()
        post_id = post.get("id")
        project_url = normalize_url_path(post.get("url"))
        if not project_url:
            continue

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

        carousel_images: list[dict] = []
        for relation in sorted_carousel_relations:
            file_id = relation.get("directus_files_id")
            if not file_id or str(file_id) in used_ids:
                continue
            file_data = files_map.get(str(file_id))
            if not file_data:
                continue
            used_ids.add(str(file_id))
            carousel_images.append(file_data)

        other_images: list[dict] = []
        for relation in sorted_other_relations:
            file_id = relation.get("directus_files_id")
            if not file_id or str(file_id) in used_ids:
                continue
            file_data = files_map.get(str(file_id))
            if not file_data:
                continue
            used_ids.add(str(file_id))
            other_images.append(file_data)

        videos: list[dict] = []
        for relation in sorted_video_relations:
            file_id = relation.get("directus_files_id")
            if not file_id:
                continue
            file_data = files_map.get(str(file_id))
            if not file_data:
                continue
            if not str(file_data.get("type") or "").startswith("video/"):
                continue
            videos.append(file_data)

        cover_image = main_image or (carousel_images[0] if carousel_images else None)
        if not cover_image and other_images:
            cover_image = other_images[0]

        projects.append(
            {
                "id": post_id,
                "title": title,
                "url": project_url,
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
        if isinstance(inline_css, str) and inline_css:
            rendered = rendered.replace("/*INLINE_GLOBAL_CSS*/", inline_css, 1)
        destination.write_text(rendered)


if __name__ == "__main__":
    build_date = datetime.datetime.now(datetime.UTC).date().isoformat()

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(exist_ok=True)

    if cms_assets_dir.exists():
        shutil.copytree(cms_assets_dir, out / "cms_assets", dirs_exist_ok=True)

    environment = jinja2.Environment(loader=jinja2.FileSystemLoader(src))

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
        if not isinstance(image, dict):
            return []

        candidates: list[tuple[int, str]] = []
        responsive_paths = image.get("responsive_asset_paths")

        if isinstance(responsive_paths, dict):
            for width_key, path in responsive_paths.items():
                if not path:
                    continue
                try:
                    width = int(width_key)
                except TypeError, ValueError:
                    continue
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
            width = image.get("width")
            if not isinstance(width, int):
                try:
                    width = int(width)
                except TypeError, ValueError:
                    width = 1000
            candidates.append((max(1, width), str(asset_path)))

        return candidates

    def image_default_src_filter(image: dict | None) -> str:
        candidates = _responsive_candidates(image)
        if candidates:
            return candidates[-1][1]

        if not isinstance(image, dict):
            return ""

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

        try:
            target = int(target_width)
        except TypeError, ValueError:
            target = candidates[-1][0]

        eligible = [item for item in candidates if item[0] <= target]
        if eligible:
            return eligible[-1][1]

        return candidates[0][1]

    def markdown_filter(content: str) -> str:
        markdown_converter = markdown.Markdown(
            extensions=[
                "markdown.extensions.fenced_code",
                "markdown.extensions.tables",
                "markdown.extensions.sane_lists",
                "markdown.extensions.extra",
            ],
            output_format="html",
        )
        return markdown_converter.convert(content or "")

    environment.filters["markdown"] = markdown_filter
    environment.filters["asset_url"] = asset_url_filter
    environment.filters["image_default_src"] = image_default_src_filter
    environment.filters["image_srcset"] = image_srcset_filter
    environment.filters["image_for_width"] = image_for_width_filter

    projects = load_projects()
    inline_global_css = (site / "global.css").read_text()
    context = {
        "build_timestamp": int(datetime.datetime.now(datetime.UTC).timestamp()),
        "build_year": datetime.datetime.now(datetime.UTC).year,
        "site_url": site_url,
        "projects": projects,
        "inline_global_css": inline_global_css,
    }

    for path in site.iterdir():
        process_path(path, context, environment)

    render_project_pages(environment, context)
    write_sitemap_and_robots(projects=projects, build_date=build_date)
