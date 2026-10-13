"""Uploaded files (product photos): a folder on the server, or an R2 bucket on Cloudflare."""
import mimetypes
from pathlib import Path

from flask import current_app

from . import cloud


def _safe(name):
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise ValueError(f"Bad file name: {name!r}")
    return name


def _path(name):
    return Path(current_app.config["UPLOAD_FOLDER"]) / _safe(name)


def _bucket():
    bucket = cloud.binding("MEDIA")
    if bucket is None:
        raise RuntimeError("The MEDIA R2 bucket isn't connected to this Worker (see wrangler.jsonc).")
    return bucket


def put(name, raw, content_type="image/jpeg"):
    if cloud.ON_WORKERS:
        options = cloud.js_object({"httpMetadata": {"contentType": content_type}})
        cloud.wait(_bucket().put(_safe(name), cloud.js_bytes(raw), options))
    else:
        _path(name).write_bytes(raw)


def get(name):
    """(bytes, content type), or None if there's no such file."""
    if cloud.ON_WORKERS:
        found = cloud.wait(_bucket().get(_safe(name)))
        if found is None:
            return None
        content_type = getattr(found.httpMetadata, "contentType", None) or "application/octet-stream"
        return cloud.py_bytes(cloud.wait(found.arrayBuffer())), content_type
    path = _path(name)
    if not path.is_file():
        return None
    return path.read_bytes(), mimetypes.guess_type(name)[0] or "application/octet-stream"


def exists(name):
    if cloud.ON_WORKERS:
        return cloud.wait(_bucket().head(_safe(name))) is not None
    return _path(name).is_file()


def delete(name):
    if cloud.ON_WORKERS:
        cloud.wait(_bucket().delete(_safe(name)))
    else:
        path = _path(name)
        if path.is_file():
            path.unlink()
