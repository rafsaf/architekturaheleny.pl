import json
import os
import pathlib
import re
import shutil
import hashlib
import urllib.error
import urllib.parse
import urllib.request

from dotenv import load_dotenv
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)


CMS_BASE_URL = "https://cms.rafsaf.pl"
CMS_OAS_PATH = "/server/specs/oas"
COLLECTIONS = [
    "architekturahelenypl_post",
    "architekturahelenypl_post_files",
    "architekturahelenypl_post_files_1",
    "architekturahelenypl_post_files_2",
    "architekturahelenypl_data",
]


ROOT_DIR = pathlib.Path(__file__).resolve().parent.parent
CMS_DATA_DIR = ROOT_DIR / "cms_data"
ITEMS_DIR = CMS_DATA_DIR / "items"
FILES_META_DIR = CMS_DATA_DIR / "files"
ASSETS_DIR = CMS_DATA_DIR / "assets"

load_dotenv(ROOT_DIR / ".env")
CMS_TOKEN = os.getenv("CMS_TOKEN")

if not CMS_TOKEN:
    raise RuntimeError("Missing CMS_TOKEN. Add it to .env file.")

REQUEST_HEADERS = {
    "Accept": "application/json",
    "Authorization": f"Bearer {CMS_TOKEN}",
}

PLACEHOLDER_VARIANT = {"width": 24, "quality": 1, "format": "avif"}
RESPONSIVE_IMAGE_WIDTHS_QUALITY = [
    (480, 65),
    (800, 65),
    (1000, 65),
    (1200, 85),
    (1600, 85),
    (2000, 85),
]


UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def sanitize_filename_stem(stem: str) -> str:
    cleaned = re.sub(r"\s+", "-", (stem or "").strip())
    cleaned = re.sub(r"[^0-9A-Za-z._-]", "-", cleaned)
    cleaned = re.sub(r"-+", "-", cleaned)
    cleaned = cleaned.strip("-._")
    return cleaned or "file"


def sanitize_download_filename(filename_download: str) -> str:
    safe_name = pathlib.Path(filename_download).name
    parsed = pathlib.Path(safe_name)
    stem = sanitize_filename_stem(parsed.stem)
    suffix = re.sub(r"[^0-9A-Za-z.]", "", parsed.suffix.lower())
    return f"{stem}{suffix}"


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((urllib.error.URLError, urllib.error.HTTPError)),
    reraise=True,
)
def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers=REQUEST_HEADERS)
    with urllib.request.urlopen(request) as response:
        payload = response.read().decode("utf-8")
    return json.loads(payload)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((urllib.error.URLError, urllib.error.HTTPError)),
    reraise=True,
)
def download_binary(url: str, destination: pathlib.Path) -> None:
    request = urllib.request.Request(url, headers=REQUEST_HEADERS)
    with urllib.request.urlopen(request) as response:
        content = response.read()

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)


def ensure_directories() -> None:
    if CMS_DATA_DIR.exists():
        shutil.rmtree(CMS_DATA_DIR)

    ITEMS_DIR.mkdir(parents=True, exist_ok=True)
    FILES_META_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)


def download_oas_spec() -> None:
    oas_url = f"{CMS_BASE_URL}{CMS_OAS_PATH}"
    oas_payload = fetch_json(oas_url)
    (CMS_DATA_DIR / "server_specs_oas.json").write_text(
        json.dumps(oas_payload, indent=2, ensure_ascii=False)
    )


def collect_file_ids_for_published_posts(payloads: dict[str, dict]) -> set[str]:
    file_ids: set[str] = set()

    posts = payloads.get("architekturahelenypl_post", {}).get("data", [])
    published_post_ids = {post["id"] for post in posts if post["status"] == "published"}

    for post in posts:
        post_id = post["id"]
        if post_id not in published_post_ids:
            continue
        main_page_image = post.get("main_page_image")
        if main_page_image and UUID_PATTERN.match(main_page_image):
            file_ids.add(main_page_image)

    for collection_name in (
        "architekturahelenypl_post_files",
        "architekturahelenypl_post_files_1",
        "architekturahelenypl_post_files_2",
    ):
        relations = payloads.get(collection_name, {}).get("data", [])
        for relation in relations:
            post_id = relation.get("architekturahelenypl_post_id")
            if post_id not in published_post_ids:
                continue

            directus_file_id = relation.get("directus_files_id")
            if directus_file_id and UUID_PATTERN.match(directus_file_id):
                file_ids.add(directus_file_id)

    return file_ids


def get_asset_filename(file_id: str, file_meta: dict) -> str:
    short_hash = hashlib.md5(file_id.encode("utf-8")).hexdigest()[:8]
    filename_download = file_meta["filename_download"]
    safe_filename = sanitize_download_filename(filename_download)
    return f"{file_id}-{short_hash}__{safe_filename}"


def get_avif_asset_filename(file_id: str, file_meta: dict) -> str:
    short_hash = hashlib.md5(file_id.encode("utf-8")).hexdigest()[:8]
    filename_download = file_meta["filename_download"]
    safe_filename = sanitize_download_filename(filename_download)
    stem = pathlib.Path(safe_filename).stem
    return f"{file_id}-{short_hash}__{stem}.avif"


def get_variant_filename(
    file_id: str, file_meta: dict, variant: str, extension: str
) -> str:
    short_hash = hashlib.md5(f"{file_id}:{variant}".encode("utf-8")).hexdigest()[:8]
    filename_download = file_meta["filename_download"]
    safe_filename = sanitize_download_filename(filename_download)
    stem = pathlib.Path(safe_filename).stem
    return f"{file_id}-{short_hash}__{stem}-{variant}.{extension}"


def pick_variant_path(variants: dict[int, str], target_width: int) -> str:
    if not variants:
        return ""

    eligible = [width for width in variants if width <= target_width]
    if eligible:
        return variants[max(eligible)]

    return variants[min(variants)]


def build_asset_url(file_id: str, params: dict | None = None) -> str:
    if not params:
        return f"{CMS_BASE_URL}/assets/{file_id}"

    query = urllib.parse.urlencode(params)
    return f"{CMS_BASE_URL}/assets/{file_id}?{query}"


def get_effective_variant_params(variant_params: dict, file_meta: dict) -> dict:
    params = dict(variant_params)
    original_width = file_meta["width"]

    if "width" in params and params["width"] > original_width:
        params["width"] = original_width

    return params


def main() -> None:
    ensure_directories()
    download_oas_spec()

    payloads: dict[str, dict] = {}
    for collection in COLLECTIONS:
        query = urllib.parse.urlencode({"limit": -1, "fields": "*"})
        items_url = f"{CMS_BASE_URL}/items/{collection}?{query}"
        items_payload = fetch_json(items_url)
        payloads[collection] = items_payload

        (ITEMS_DIR / f"{collection}.json").write_text(
            json.dumps(items_payload, indent=2, ensure_ascii=False)
        )

    all_file_ids = collect_file_ids_for_published_posts(payloads)

    files_index = {}
    for file_id in sorted(all_file_ids):
        meta_url = f"{CMS_BASE_URL}/files/{file_id}"
        meta_payload = fetch_json(meta_url)
        meta_data = meta_payload["data"]

        (FILES_META_DIR / f"{file_id}.json").write_text(
            json.dumps(meta_payload, indent=2, ensure_ascii=False)
        )

        file_type = meta_data["type"]
        original_filename = get_asset_filename(file_id=file_id, file_meta=meta_data)

        variant_paths: dict[str, str] = {}
        variant_formats: dict[str, str] = {}
        responsive_asset_paths: dict[int, str] = {}
        original_width = meta_data["width"]

        if file_type.startswith("image/"):
            placeholder_params = get_effective_variant_params(
                PLACEHOLDER_VARIANT, meta_data
            )
            placeholder_filename = get_variant_filename(
                file_id=file_id,
                file_meta=meta_data,
                variant="placeholder",
                extension="avif",
            )
            placeholder_path = CMS_DATA_DIR / "assets" / placeholder_filename
            download_binary(
                build_asset_url(file_id=file_id, params=placeholder_params),
                placeholder_path,
            )
            variant_paths["placeholder"] = f"/cms_assets/{placeholder_filename}"
            variant_formats["placeholder"] = "avif"

            for width, quality in RESPONSIVE_IMAGE_WIDTHS_QUALITY:
                if width > original_width:
                    continue

                variant_name = f"{width}w"
                variant_params = {
                    "width": width,
                    "quality": quality,
                    "format": "avif",
                }
                effective_params = get_effective_variant_params(
                    variant_params, meta_data
                )

                avif_filename = get_variant_filename(
                    file_id=file_id,
                    file_meta=meta_data,
                    variant=variant_name,
                    extension="avif",
                )
                avif_path = CMS_DATA_DIR / "assets" / avif_filename
                download_binary(
                    build_asset_url(file_id=file_id, params=effective_params),
                    avif_path,
                )
                responsive_asset_paths[width] = f"/cms_assets/{avif_filename}"
                variant_formats[variant_name] = "avif"
        else:
            fallback_relative = pathlib.Path("assets") / original_filename
            fallback_path = CMS_DATA_DIR / fallback_relative
            download_binary(build_asset_url(file_id=file_id), fallback_path)

            fallback_url = f"/cms_assets/{original_filename}"
            variant_paths = {
                "placeholder": fallback_url,
            }
            responsive_asset_paths = {800: fallback_url}
            variant_formats = {
                "placeholder": "original",
                "800w": "original",
            }

        unique_formats = set(variant_formats.values())
        if unique_formats == {"avif"}:
            asset_format = "avif"
        elif unique_formats == {"original"}:
            asset_format = "original"
        else:
            asset_format = "mixed"

        sorted_widths = sorted(responsive_asset_paths)
        largest_width = sorted_widths[-1]
        largest_asset_path = responsive_asset_paths[largest_width]
        filename = pathlib.Path(largest_asset_path).name

        mobile_asset_path = pick_variant_path(responsive_asset_paths, 800)
        desktop_asset_path = largest_asset_path

        files_index[file_id] = {
            "id": file_id,
            "filename": filename,
            "filename_download": meta_data["filename_download"],
            "title": meta_data["title"],
            "width": meta_data["width"],
            "height": meta_data["height"],
            "type": meta_data["type"],
            "filesize": meta_data["filesize"],
            "asset_format": asset_format,
            "asset_path": largest_asset_path,
            "placeholder_asset_path": variant_paths["placeholder"],
            "mobile_asset_path": mobile_asset_path,
            "desktop_asset_path": desktop_asset_path,
            "largest_asset_path": largest_asset_path,
            "responsive_asset_paths": {
                str(width): path
                for width, path in sorted(responsive_asset_paths.items())
            },
        }

    (CMS_DATA_DIR / "files_index.json").write_text(
        json.dumps(files_index, indent=2, ensure_ascii=False)
    )


if __name__ == "__main__":
    main()
