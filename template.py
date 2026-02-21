import datetime
import json
import pathlib
import shutil

import jinja2
import markdown


root_dir = pathlib.Path(__file__).resolve().parent
src = root_dir / "src"
site = src / "site"
out = root_dir / "out"
cms_data_dir = root_dir / "cms_data"
cms_assets_dir = cms_data_dir / "assets"


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

    projects: list[dict] = []
    for post in posts:
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

        main_page_image_id = post.get("main_page_image")

        main_image = files_map.get(str(main_page_image_id)) if main_page_image_id else None

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

        cover_image = main_image or (carousel_images[0] if carousel_images else None)
        if not cover_image and other_images:
            cover_image = other_images[0]

        projects.append(
            {
                "id": post_id,
                "title": title,
                "url": project_url,
                "localization": post.get("localization"),
                "authors": post.get("authors"),
                "project_status": post.get("project_status"),
                "surface": post.get("surface"),
                "long_description": post.get("long_description") or "",
                "cover_image": cover_image,
                "main_image": main_image,
                "carousel_images": carousel_images,
                "other_images": other_images,
            }
        )

    return projects


def render_project_pages(environment: jinja2.Environment, context: dict) -> None:
    detail_template = environment.get_template("components/project_detail.html")

    for project in context["projects"]:
        destination = out / "projekty" / project["url"] / "index.html"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(detail_template.render({**context, "project": project}))


if __name__ == "__main__":
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(exist_ok=True)

    if cms_assets_dir.exists():
        shutil.copytree(cms_assets_dir, out / "cms_assets", dirs_exist_ok=True)

    environment = jinja2.Environment(loader=jinja2.FileSystemLoader(src))

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

    projects = load_projects()
    context = {
        "build_timestamp": int(datetime.datetime.now(datetime.UTC).timestamp()),
        "build_year": datetime.datetime.now(datetime.UTC).year,
        "projects": projects,
    }

    for path in site.iterdir():
        process_path(path, context, environment)

    render_project_pages(environment, context)
