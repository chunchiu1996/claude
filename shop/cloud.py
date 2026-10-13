"""Where the shop runs: a normal server (Docker / gunicorn) or Cloudflare Workers.

On Cloudflare the whole app runs inside one Durable Object: the database is the Durable Object's
built-in SQLite, product photos live in an R2 bucket, and email goes through Cloudflare Email Service.
`worker.py` calls bind() before the first request. Everywhere else ON_WORKERS is False and nothing
here is used.
"""
import sys

ON_WORKERS = sys.platform == "emscripten"

_env = None
_storage = None


def raw(obj):
    """The plain JavaScript object behind the Python Workers SDK's convenience wrappers."""
    try:
        from pyodide.ffi import JsProxy
    except ImportError:  # plain Python (tests)
        JsProxy = ()
    if JsProxy and isinstance(obj, JsProxy):  # already plain JavaScript (RPC stubs answer any attribute name)
        return obj
    for attr in ("_binding", "_env", "_ctx"):
        inner = getattr(obj, attr, None)
        if inner is not None:
            return inner
    return obj


def bind(env, storage):
    global _env, _storage
    _env, _storage = raw(env), raw(storage)


def env():
    return _env


def storage():
    return _storage


def binding(name):
    """A Cloudflare binding (R2 bucket, email sender…) or None when it isn't configured."""
    return raw(getattr(_env, name, None)) if _env is not None else None


def setting(name):
    """A plain-text variable or secret from the Worker's settings, or None."""
    value = binding(name)
    return value if isinstance(value, str) else None


def wait(promise):
    """Wait for a JavaScript promise from synchronous (Flask) code."""
    import js
    from pyodide.ffi import JsProxy, run_sync

    if isinstance(promise, JsProxy):
        # Calls to RPC bindings (like the email sender) return an "RPC promise", which is a function
        # object that run_sync() refuses: turn it into an ordinary Promise first.
        promise = js.Promise.resolve(promise)
    return run_sync(promise)


def js_object(mapping):
    """A plain JavaScript object, for calling Cloudflare APIs that take options."""
    import js
    from pyodide.ffi import to_js

    return to_js(mapping, dict_converter=js.Object.fromEntries)


def js_json(mapping):
    """A plain JavaScript object made through JSON: what bindings like the email sender expect."""
    import json

    import js

    return js.JSON.parse(json.dumps(mapping))


def describe_error(exc):
    """The JavaScript error message and stack behind a failed Cloudflare call, for the logs."""
    js_error = getattr(exc, "js_error", None)
    parts = [f"{type(exc).__name__}: {exc}"]
    for attr in ("code", "stack"):
        value = getattr(js_error, attr, None) if js_error is not None else None
        if value:
            parts.append(f"{attr}={value}")
    return " | ".join(parts)


def js_bytes(raw):
    from js import Uint8Array

    return Uint8Array.new(raw)


def py_bytes(array_buffer):
    return bytes(array_buffer.to_py())


def http_request(method, url, headers=None, body=None):
    """Make an HTTPS request with fetch(). Returns (status, body bytes)."""
    import js

    options = {"method": method, "headers": js_object(headers or {})}
    if body is not None:
        options["body"] = body if isinstance(body, str) else js_bytes(body)
    response = wait(js.fetch(url, js_object(options)))
    return response.status, py_bytes(wait(response.arrayBuffer()))
