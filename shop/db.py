import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import click
from flask import current_app, g
from flask.cli import with_appcontext

from . import cloud


def get_db():
    if "db" not in g:
        g.db = connect()
    return g.db


def connect():
    if cloud.ON_WORKERS:
        return DurableObjectDB(cloud.storage())
    db = sqlite3.connect(current_app.config["DATABASE"], timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def atomic(db, work):
    """Run work() as one transaction: all its writes are saved together, or none are if it raises."""
    if isinstance(db, DurableObjectDB):
        return db.transaction(work)
    try:
        result = work()
        db.commit()
        return result
    except BaseException:
        db.rollback()
        raise


# ---------------------------------------------------------------- Cloudflare Durable Object storage


class Row:
    """Behaves like sqlite3.Row: row["name"], row[0], dict(row), row.keys()."""

    __slots__ = ("_index", "_values")

    def __init__(self, columns, values):
        self._index, self._values = columns, tuple(values)

    def __getitem__(self, key):
        if isinstance(key, str):
            try:
                return self._values[self._index[key]]
            except KeyError:
                raise IndexError(f"No such column: {key}") from None
        return self._values[key]

    def keys(self):
        return list(self._index)

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __eq__(self, other):
        return isinstance(other, Row) and self._values == other._values and self.keys() == other.keys()

    def __hash__(self):
        return hash(self._values)

    def __repr__(self):
        return f"Row({dict(zip(self._index, self._values, strict=True))!r})"


class Cursor:
    def __init__(self, rows, rowcount=-1, lastrowid=None):
        self._rows, self.rowcount, self.lastrowid = rows, rowcount, lastrowid

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows

    def __iter__(self):
        return iter(self.fetchall())


_WRITE = ("INSERT", "UPDATE", "DELETE", "REPLACE")


def _js_null():
    from pyodide.ffi import jsnull

    return jsnull


def _js_callable(fn):
    """(JavaScript function wrapping fn, cleanup)."""
    from pyodide.ffi import create_proxy

    proxy = create_proxy(fn)
    return proxy, proxy.destroy


def _js_param(value):
    if value is None:
        return _js_null()  # Python None would arrive in JavaScript as `undefined`, which SQLite rejects
    if isinstance(value, bool):
        return int(value)
    return value


class DurableObjectDB:
    """The parts of the sqlite3 connection API the shop uses, on a Durable Object's SQLite storage.
    Writes are saved automatically; atomic() groups several into one transaction."""

    def __init__(self, storage):
        self.storage, self.sql = storage, storage.sql

    def _exec(self, sql, params=()):
        return self.sql.exec(sql, *[_js_param(p) for p in params])

    def execute(self, sql, params=()):
        statement = sql.strip()
        if statement.upper().startswith("PRAGMA FOREIGN_KEYS") or statement.upper().startswith("PRAGMA JOURNAL_MODE"):
            return Cursor([])  # always on / managed by Cloudflare
        cursor = self._exec(statement, params)
        columns = {name: i for i, name in enumerate(cursor.columnNames.to_py())}
        rows = [Row(columns, values.to_py()) for values in cursor.raw().toArray()]
        if not statement.upper().startswith(_WRITE):
            return Cursor(rows)
        changes, last_id = self.sql.exec("SELECT changes(), last_insert_rowid()").raw().toArray()[0].to_py()
        return Cursor(rows, rowcount=changes, lastrowid=last_id)

    def executemany(self, sql, seq_of_params):
        for params in seq_of_params:
            self.execute(sql, params)

    def executescript(self, script):
        self.sql.exec(script)

    def transaction(self, work):
        outcome = {}

        def run():
            try:
                outcome["result"] = work()
            except BaseException as exc:  # keep the original exception (JavaScript would wrap it)
                outcome["error"] = exc
                raise

        callback, cleanup = _js_callable(run)
        try:
            self.storage.transactionSync(callback)
        except BaseException:
            if "error" in outcome:
                raise outcome["error"] from None
            raise
        finally:
            cleanup()
        return outcome.get("result")

    def commit(self):
        """Durable Object writes are saved automatically."""

    def rollback(self):
        """Only atomic() can undo writes on Cloudflare: use it where a rollback matters."""

    def close(self):
        pass


def close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


# (table, column, definition) added after the first release; applied to older databases on startup.
MIGRATIONS = [
    ("products", "quote_only", "INTEGER NOT NULL DEFAULT 0"),
    ("inquiries", "reference", "TEXT"),
    ("inquiries", "access_token", "TEXT"),
    ("inquiries", "contact_pref", "TEXT"),
    ("inquiries", "source", "TEXT"),
    ("orders", "invoice_sent_at", "TEXT"),
]


def init_db():
    db = get_db()
    db.execute("PRAGMA journal_mode = WAL")  # (ignored on Cloudflare)
    db.executescript(current_app.open_resource("schema.sql").read().decode("utf-8"))
    for table, column, definition in MIGRATIONS:
        if column not in {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_inquiries_reference ON inquiries(reference)")
    db.commit()


def stored_value(key, make):
    """A value kept in the database, created once with make()."""
    db = get_db()
    row = db.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    if row:
        return row["value"]
    value = make()
    db.execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)", (key, value))
    db.commit()
    return db.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()["value"]


@click.command("seed-demo")
@with_appcontext
def seed_demo_command():
    """Load demo suppliers, categories, products and orders."""
    from .seed import seed_demo

    if get_db().execute("SELECT 1 FROM products LIMIT 1").fetchone():
        raise click.ClickException("Database already has products; refusing to add demo data.")
    seed_demo(get_db())
    click.echo("Demo catalog loaded.")


@click.command("backup-db")
@click.option("--keep", default=14, show_default=True, help="How many daily snapshots to keep.")
@with_appcontext
def backup_db_command(keep):
    """Write a consistent snapshot of the database to <data dir>/backups/ and delete the oldest ones."""
    folder = Path(current_app.config["DATABASE"]).parent / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"shop-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.db"
    snapshot = sqlite3.connect(target)
    with snapshot:
        get_db().backup(snapshot)  # safe while the shop is running
    snapshot.close()
    for old in sorted(folder.glob("shop-*.db"))[:-keep]:
        old.unlink()
    click.echo(f"Backed up to {target}")


def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(seed_demo_command)
    app.cli.add_command(backup_db_command)
