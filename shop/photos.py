"""Product photos: resize/clean uploads, match files to products by SKU, bulk uploads (files, folders, zip),
and photos pasted into a factory's Excel product list.

Naming rule for bulk uploads (case doesn't matter):
    TILE-600.jpg              -> main photo of SKU TILE-600
    TILE-600-2.jpg / _2 / (2) -> 2nd photo, 3rd photo, ... (shown as a gallery on the product page)
"""
import io
import re
import secrets
import zipfile
import zlib
from pathlib import Path, PurePosixPath

from flask import current_app

from .importer import find_header, pick_sheet

MAX_SIDE, THUMB_SIDE = 1600, 600
MAX_IMAGE_BYTES = 30 * 1024 * 1024
MAX_ZIP_FILES, MAX_ZIP_TOTAL = 1000, 1024 * 1024 * 1024  # zip bombs; and stays under Cloudflare's 100 s
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".heif"}
_SUFFIX = re.compile(r"^(?P<sku>.+?)(?:[\s_\-]+\(?(?P<n>\d{1,2})\)?|\s*\((?P<p>\d{1,2})\))$")


# ---------------------------------------------------------------- images


def save_image(raw):
    """Validate, orient, shrink and store an image. Returns its URL (/media/<name>.jpg). Raises ValueError."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    _enable_heic()
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("Image is larger than 30 MB")
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "LA", "P", "PA"):
            img = img.convert("RGBA")
            background = Image.new("RGB", img.size, "white")
            background.paste(img, mask=img.split()[-1])
            img = background
        else:
            img = img.convert("RGB")
    except Image.DecompressionBombError as exc:
        raise ValueError("Image is too large (too many pixels)") from exc
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, EOFError) as exc:  # Pillow's ways of failing
        raise ValueError("Not an image we can read. Use JPG, PNG, WEBP or HEIC") from exc
    folder = Path(current_app.config["UPLOAD_FOLDER"])
    name = secrets.token_hex(8)
    large = img.copy()
    large.thumbnail((MAX_SIDE, MAX_SIDE))
    large.save(folder / f"{name}.jpg", "JPEG", quality=85, optimize=True, progressive=True)
    img.thumbnail((THUMB_SIDE, THUMB_SIDE))
    img.save(folder / f"{name}-sm.jpg", "JPEG", quality=80, optimize=True, progressive=True)
    return f"/media/{name}.jpg"


def _enable_heic():
    """iPhone photos (.heic) — when pillow-heif is installed."""
    global _heic_ready
    if not _heic_ready:
        _heic_ready = True
        try:
            from pillow_heif import register_heif_opener

            register_heif_opener()
        except ImportError:
            pass


_heic_ready = False


def thumb_url(url):
    """Smaller version for product cards, when we made one."""
    if url and url.startswith("/media/") and url.endswith(".jpg") and not url.endswith("-sm.jpg"):
        small = url[:-4] + "-sm.jpg"
        if (Path(current_app.config["UPLOAD_FOLDER"]) / small.rsplit("/", 1)[-1]).exists():
            return small
    return url


def delete_media(url):
    if url and url.startswith("/media/"):
        folder = Path(current_app.config["UPLOAD_FOLDER"])
        name = url.rsplit("/", 1)[-1]
        for candidate in (name, name[:-4] + "-sm.jpg" if name.endswith(".jpg") else None):
            if candidate and "/" not in candidate and (folder / candidate).is_file():
                (folder / candidate).unlink()


def set_photo(db, product_id, position, url):
    """Position 1 = main photo; 2+ = gallery. Replaces (and deletes) whatever was there."""
    if position <= 1:
        old = db.execute("SELECT image_url FROM products WHERE id = ?", (product_id,)).fetchone()["image_url"]
        db.execute("UPDATE products SET image_url = ?, updated_at = datetime('now') WHERE id = ?", (url, product_id))
    else:
        row = db.execute("SELECT url FROM product_images WHERE product_id = ? AND position = ?",
                         (product_id, position)).fetchone()
        old = row["url"] if row else None
        db.execute("INSERT INTO product_images (product_id, position, url) VALUES (?, ?, ?) "
                   "ON CONFLICT (product_id, position) DO UPDATE SET url = excluded.url", (product_id, position, url))
    if old and old != url:
        delete_media(old)


def remove_photo(db, product_id, position):
    row = db.execute("SELECT url FROM product_images WHERE product_id = ? AND position = ?",
                     (product_id, position)).fetchone()
    if row:
        db.execute("DELETE FROM product_images WHERE product_id = ? AND position = ?", (product_id, position))
        delete_media(row["url"])


def gallery(db, product_id):
    return db.execute("SELECT position, url FROM product_images WHERE product_id = ? ORDER BY position",
                      (product_id,)).fetchall()


# ---------------------------------------------------------------- matching files to SKUs


def _product_by_sku(db, sku):
    return db.execute("SELECT id, sku FROM products WHERE sku = ? COLLATE NOCASE", (sku.strip(),)).fetchone()


def match_filename(db, filename):
    """'TILE-600-2.jpg' -> (product, 2). Exact SKU matches win, so SKUs that end in -2 still work."""
    stem = PurePosixPath(filename.replace("\\", "/")).stem.strip()
    product = _product_by_sku(db, stem)
    if product:
        return product, 1
    m = _SUFFIX.match(stem)
    if m:
        product = _product_by_sku(db, m.group("sku"))
        if product:
            return product, max(int(m.group("n") or m.group("p")), 1)
    return None, None


def attach_file(db, filename, raw):
    """One uploaded image -> {file, sku, position, status: ok|unmatched|error, message}."""
    result = {"file": filename, "sku": None, "position": None}
    product, position = match_filename(db, filename)
    if product is None:
        return {**result, "status": "unmatched", "message": "No product with this SKU. Name the file after the SKU, "
                                                            "e.g. TILE-600.jpg (and TILE-600-2.jpg for more photos)"}
    try:
        url = save_image(raw)
    except ValueError as exc:
        return {**result, "sku": product["sku"], "status": "error", "message": str(exc)}
    set_photo(db, product["id"], position, url)
    db.commit()
    label = "main photo" if position == 1 else f"photo {position}"
    return {**result, "sku": product["sku"], "position": position, "status": "ok", "message": f"Saved as {label}"}


def attach_zip(db, raw):
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        return [{"file": "(zip)", "sku": None, "position": None, "status": "error", "message": "Not a valid .zip file"}]
    entries = [e for e in archive.infolist() if not e.is_dir()
               and not e.filename.startswith("__MACOSX/") and not PurePosixPath(e.filename).name.startswith(".")
               and PurePosixPath(e.filename).suffix.lower() in IMAGE_SUFFIXES]
    if len(entries) > MAX_ZIP_FILES or sum(e.file_size for e in entries) > MAX_ZIP_TOTAL:
        return [{"file": "(zip)", "sku": None, "position": None, "status": "error",
                 "message": f"Zip is too big: split it into parts of up to {MAX_ZIP_FILES} photos"}]
    results = []
    for entry in entries:
        name = PurePosixPath(entry.filename).name
        if entry.file_size > MAX_IMAGE_BYTES:
            results.append({"file": name, "sku": None, "position": None, "status": "error",
                            "message": "Image is larger than 30 MB"})
            continue
        try:
            raw = archive.read(entry)
        except (zipfile.BadZipFile, zlib.error, OSError, NotImplementedError, RuntimeError) as exc:
            results.append({"file": name, "sku": None, "position": None, "status": "error",
                            "message": f"Couldn't unzip this file ({exc.__class__.__name__})"})
            continue
        results.append(attach_file(db, name, raw))
    return results


# ---------------------------------------------------------------- photos pasted into Excel


def excel_photos(db, raw, rows):
    """Attach pictures pasted into an .xlsx product list to the SKU on the row they sit on.
    The first picture on a row becomes the main photo, the next ones photos 2, 3, ..."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            if not any(n.startswith("xl/media/") for n in z.namelist()):
                return []
    except zipfile.BadZipFile:
        return []
    header_index, headers = find_header(rows)
    if header_index is None:
        return []
    sku_col = headers.index("sku")
    from openpyxl import load_workbook

    try:
        workbook = load_workbook(io.BytesIO(raw))
    except Exception:  # pictures are a bonus: never fail the import because of them
        current_app.logger.exception("Could not read pictures from spreadsheet")
        return []
    sheet = workbook[pick_sheet(workbook.sheetnames)]
    by_row = {}
    for image in getattr(sheet, "_images", []):
        anchor = getattr(image.anchor, "_from", None)
        if anchor is None:
            continue
        by_row.setdefault(anchor.row, []).append((anchor.col, image))
    results = []
    for row_index in sorted(by_row):
        if row_index <= header_index or row_index >= len(rows) or sku_col >= len(rows[row_index]):
            continue
        sku = rows[row_index][sku_col]
        product = _product_by_sku(db, sku) if sku else None
        for position, (_col, image) in enumerate(sorted(by_row[row_index], key=lambda item: item[0]), start=1):
            name = f"row {row_index + 1} picture {position}"
            if product is None:
                results.append({"file": name, "sku": sku, "position": None, "status": "unmatched",
                                "message": "No SKU on this row"})
                continue
            try:
                url = save_image(image._data())
            except ValueError as exc:
                results.append({"file": name, "sku": product["sku"], "position": None, "status": "error",
                                "message": str(exc)})
                continue
            set_photo(db, product["id"], position, url)
            results.append({"file": name, "sku": product["sku"], "position": position, "status": "ok",
                            "message": "Saved from spreadsheet"})
    db.commit()
    return results
