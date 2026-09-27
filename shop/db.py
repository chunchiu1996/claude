import sqlite3

import click
from flask import current_app, g


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"], timeout=10)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


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
]


def init_db():
    db = get_db()
    db.execute("PRAGMA journal_mode = WAL")
    db.executescript(current_app.open_resource("schema.sql").read().decode("utf-8"))
    for table, column, definition in MIGRATIONS:
        if column not in {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_inquiries_reference ON inquiries(reference)")
    db.commit()


@click.command("seed-demo")
def seed_demo_command():
    """Load demo suppliers, categories, products and orders."""
    from .seed import seed_demo

    if get_db().execute("SELECT 1 FROM products LIMIT 1").fetchone():
        raise click.ClickException("Database already has products; refusing to add demo data.")
    seed_demo(get_db())
    click.echo("Demo catalog loaded.")


def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(seed_demo_command)
