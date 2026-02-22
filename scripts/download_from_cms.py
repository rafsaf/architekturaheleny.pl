import json
import os
import pathlib
import re
import shutil
import hashlib
import time
import urllib.error
import urllib.parse
import urllib.request

from dotenv import load_dotenv


CMS_BASE_URL = "https://cms.rafsaf.pl"
COLLECTIONS = [
    "architekturahelenypl_post",
    "architekturahelenypl_post_files",
    "architekturahelenypl_post_files_1",
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
RESPONSIVE_IMAGE_WIDTHS = [480, 800, 1000, 1200, 1600, 2000]
RETRY_DELAYS_SECONDS = (1, 5)


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


def sanitize_download_filename(filename_download: str | None) -> str:
    if not filename_download:
        return ""

    safe_name = pathlib.Path(filename_download).name
    if not safe_name:
        return ""

    parsed = pathlib.Path(safe_name)
    stem = sanitize_filename_stem(parsed.stem)
    suffix = re.sub(r"[^0-9A-Za-z.]", "", parsed.suffix.lower())
    return f"{stem}{suffix}"


def should_retry_http_error(error: urllib.error.HTTPError) -> bool:
    return error.code in {408, 429} or 500 <= error.code <= 599


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers=REQUEST_HEADERS)

    for attempt in range(len(RETRY_DELAYS_SECONDS) + 1):
        try:
            with urllib.request.urlopen(request) as response:
                payload = response.read().decode("utf-8")
            return json.loads(payload)
        except urllib.error.HTTPError as error:
            if attempt < len(RETRY_DELAYS_SECONDS) and should_retry_http_error(error):
                time.sleep(RETRY_DELAYS_SECONDS[attempt])
                continue
            raise
        except urllib.error.URLError, TimeoutError, ConnectionError:
            if attempt < len(RETRY_DELAYS_SECONDS):
                time.sleep(RETRY_DELAYS_SECONDS[attempt])
                continue
            raise

    raise RuntimeError("Failed to fetch JSON after retries")


def download_binary(url: str, destination: pathlib.Path) -> None:
    request = urllib.request.Request(url, headers=REQUEST_HEADERS)

    for attempt in range(len(RETRY_DELAYS_SECONDS) + 1):
        try:
            with urllib.request.urlopen(request) as response:
                content = response.read()
            break
        except urllib.error.HTTPError as error:
            if attempt < len(RETRY_DELAYS_SECONDS) and should_retry_http_error(error):
                time.sleep(RETRY_DELAYS_SECONDS[attempt])
                continue
            raise
        except urllib.error.URLError, TimeoutError, ConnectionError:
            if attempt < len(RETRY_DELAYS_SECONDS):
                time.sleep(RETRY_DELAYS_SECONDS[attempt])
                continue
            raise
    else:
        raise RuntimeError("Failed to download binary after retries")

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)


def ensure_directories() -> None:
    if CMS_DATA_DIR.exists():
        shutil.rmtree(CMS_DATA_DIR)

    ITEMS_DIR.mkdir(parents=True, exist_ok=True)
    FILES_META_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)


def collect_file_ids_for_published_posts(payloads: dict[str, dict]) -> set[str]:
    file_ids: set[str] = set()

    posts = payloads.get("architekturahelenypl_post", {}).get("data", [])
    published_post_ids = {
        post.get("id")
        for post in posts
        if post.get("status") == "published" and post.get("id") is not None
    }

    for post in posts:
        if post.get("id") not in published_post_ids:
            continue
        main_page_image = post.get("main_page_image")
        if isinstance(main_page_image, str) and UUID_PATTERN.match(main_page_image):
            file_ids.add(main_page_image)

    for collection_name in (
        "architekturahelenypl_post_files",
        "architekturahelenypl_post_files_1",
    ):
        relations = payloads.get(collection_name, {}).get("data", [])
        for relation in relations:
            post_id = relation.get("architekturahelenypl_post_id")
            if post_id not in published_post_ids:
                continue

            directus_file_id = relation.get("directus_files_id")
            if isinstance(directus_file_id, str) and UUID_PATTERN.match(
                directus_file_id
            ):
                file_ids.add(directus_file_id)

    return file_ids


def get_asset_filename(file_id: str, file_meta: dict) -> str:
    short_hash = hashlib.md5(file_id.encode("utf-8")).hexdigest()[:8]
    default_name = file_id
    filename_download = file_meta.get("filename_download")
    if not filename_download:
        return f"{default_name}-{short_hash}"

    safe_filename = sanitize_download_filename(filename_download)
    if not safe_filename:
        return f"{default_name}-{short_hash}"
    return f"{file_id}-{short_hash}__{safe_filename}"


def get_avif_asset_filename(file_id: str, file_meta: dict) -> str:
    short_hash = hashlib.md5(file_id.encode("utf-8")).hexdigest()[:8]
    filename_download = file_meta.get("filename_download")
    if not filename_download:
        return f"{file_id}-{short_hash}.avif"

    safe_filename = sanitize_download_filename(filename_download)
    stem = pathlib.Path(safe_filename).stem
    if not stem:
        return f"{file_id}-{short_hash}.avif"
    return f"{file_id}-{short_hash}__{stem}.avif"


def get_variant_filename(
    file_id: str, file_meta: dict, variant: str, extension: str
) -> str:
    short_hash = hashlib.md5(f"{file_id}:{variant}".encode("utf-8")).hexdigest()[:8]
    filename_download = file_meta.get("filename_download")
    if not filename_download:
        return f"{file_id}-{short_hash}-{variant}.{extension}"

    safe_filename = sanitize_download_filename(filename_download)
    stem = pathlib.Path(safe_filename).stem
    if not stem:
        return f"{file_id}-{short_hash}-{variant}.{extension}"

    return f"{file_id}-{short_hash}__{stem}-{variant}.{extension}"


def parse_width(value) -> int | None:
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def pick_variant_path(variants: dict[int, str], target_width: int) -> str:
    if not variants:
        return ""

    eligible = [width for width in variants if width <= target_width]
    if eligible:
        return variants[max(eligible)]

    return variants[min(variants)]


def quality_for_width(width: int) -> int:
    if width >= 1200:
        return 85
    return 65


def build_asset_url(file_id: str, params: dict | None = None) -> str:
    if not params:
        return f"{CMS_BASE_URL}/assets/{file_id}"

    query = urllib.parse.urlencode(params)
    return f"{CMS_BASE_URL}/assets/{file_id}?{query}"


def get_effective_variant_params(variant_params: dict, file_meta: dict) -> dict:
    params = dict(variant_params)
    original_width = file_meta.get("width")

    if isinstance(original_width, str) and original_width.isdigit():
        original_width = int(original_width)

    if isinstance(original_width, int) and original_width > 0 and "width" in params:
        requested_width = params.get("width")
        if isinstance(requested_width, int) and requested_width > original_width:
            params["width"] = original_width

    return params


def main() -> None:
    ensure_directories()

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
        meta_data = meta_payload.get("data", {})

        (FILES_META_DIR / f"{file_id}.json").write_text(
            json.dumps(meta_payload, indent=2, ensure_ascii=False)
        )

        file_type = meta_data.get("type") or ""
        original_filename = get_asset_filename(file_id=file_id, file_meta=meta_data)
        original_extension = pathlib.Path(original_filename).suffix.lstrip(".") or "bin"

        variant_paths: dict[str, str] = {}
        variant_formats: dict[str, str] = {}
        responsive_asset_paths: dict[int, str] = {}
        original_width = parse_width(meta_data.get("width"))

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

            try:
                download_binary(
                    build_asset_url(file_id=file_id, params=placeholder_params),
                    placeholder_path,
                )
                variant_paths["placeholder"] = f"/cms_assets/{placeholder_filename}"
                variant_formats["placeholder"] = "avif"
            except urllib.error.HTTPError:
                fallback_filename = get_variant_filename(
                    file_id=file_id,
                    file_meta=meta_data,
                    variant="placeholder",
                    extension=original_extension,
                )
                fallback_path = CMS_DATA_DIR / "assets" / fallback_filename
                fallback_params = {
                    key: value
                    for key, value in placeholder_params.items()
                    if key != "format"
                }
                download_binary(
                    build_asset_url(file_id=file_id, params=fallback_params),
                    fallback_path,
                )
                variant_paths["placeholder"] = f"/cms_assets/{fallback_filename}"
                variant_formats["placeholder"] = "original"

            for width in RESPONSIVE_IMAGE_WIDTHS:
                if original_width and width > original_width:
                    continue

                variant_name = f"{width}w"
                variant_params = {
                    "width": width,
                    "quality": quality_for_width(width),
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

                try:
                    download_binary(
                        build_asset_url(file_id=file_id, params=effective_params),
                        avif_path,
                    )
                    responsive_asset_paths[width] = f"/cms_assets/{avif_filename}"
                    variant_formats[variant_name] = "avif"
                except urllib.error.HTTPError:
                    fallback_filename = get_variant_filename(
                        file_id=file_id,
                        file_meta=meta_data,
                        variant=variant_name,
                        extension=original_extension,
                    )
                    fallback_path = CMS_DATA_DIR / "assets" / fallback_filename

                    fallback_params = {
                        key: value
                        for key, value in effective_params.items()
                        if key != "format"
                    }
                    download_binary(
                        build_asset_url(file_id=file_id, params=fallback_params),
                        fallback_path,
                    )
                    responsive_asset_paths[width] = f"/cms_assets/{fallback_filename}"
                    variant_formats[variant_name] = "original"

            if not responsive_asset_paths:
                fallback_width = original_width or 800
                variant_name = f"{fallback_width}w"
                variant_params = {
                    "width": fallback_width,
                    "quality": quality_for_width(fallback_width),
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

                try:
                    download_binary(
                        build_asset_url(file_id=file_id, params=effective_params),
                        avif_path,
                    )
                    responsive_asset_paths[fallback_width] = (
                        f"/cms_assets/{avif_filename}"
                    )
                    variant_formats[variant_name] = "avif"
                except urllib.error.HTTPError:
                    fallback_filename = get_variant_filename(
                        file_id=file_id,
                        file_meta=meta_data,
                        variant=variant_name,
                        extension=original_extension,
                    )
                    fallback_path = CMS_DATA_DIR / "assets" / fallback_filename
                    fallback_params = {
                        key: value
                        for key, value in effective_params.items()
                        if key != "format"
                    }
                    download_binary(
                        build_asset_url(file_id=file_id, params=fallback_params),
                        fallback_path,
                    )
                    responsive_asset_paths[fallback_width] = (
                        f"/cms_assets/{fallback_filename}"
                    )
                    variant_formats[variant_name] = "original"
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
            "filename_download": meta_data.get("filename_download"),
            "title": meta_data.get("title"),
            "width": meta_data.get("width"),
            "height": meta_data.get("height"),
            "type": meta_data.get("type"),
            "filesize": meta_data.get("filesize"),
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
