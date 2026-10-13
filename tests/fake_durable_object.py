"""An imitation of a Cloudflare Durable Object's SQLite storage (ctx.storage), backed by sqlite3.

It follows the JavaScript API the shop uses (sql.exec() returning a cursor with columnNames and
raw().toArray(), and transactionSync()), including its rules: every statement is saved at once,
BEGIN/SAVEPOINT are not allowed, and only transactionSync() can roll back.
Run the whole test suite on it with:  SHOP_TEST_DB=durable-object pytest
"""
import sqlite3


class _JsList(list):
    def to_py(self):
        return list(self)


class _Raw:
    def __init__(self, rows):
        self._rows = rows

    def toArray(self):  # noqa: N802 (JavaScript name)
        return [_JsList(r) for r in self._rows]


class _Cursor:
    def __init__(self, columns, rows):
        self.columnNames = _JsList(columns)
        self._rows = rows

    def raw(self):
        return _Raw(self._rows)


class FakeSql:
    def __init__(self, path):
        self.conn = sqlite3.connect(path, isolation_level=None)  # autocommit, like a Durable Object
        self.conn.execute("PRAGMA foreign_keys = ON")

    def exec(self, query, *bindings):
        upper = query.lstrip().upper()
        if upper.startswith(("BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE")):
            raise RuntimeError("Durable Objects don't allow transaction statements in sql.exec()")
        if not bindings and sqlite3.complete_statement(query) and query.strip().rstrip(";").count(";"):
            self.conn.executescript(query)
            return _Cursor([], [])
        cur = self.conn.execute(query, bindings)
        columns = [d[0] for d in cur.description] if cur.description else []
        return _Cursor(columns, cur.fetchall())


class FakeStorage:
    def __init__(self, path):
        self.sql = FakeSql(path)
        self._depth = 0

    def transactionSync(self, callback):  # noqa: N802 (JavaScript name)
        name = f"t{self._depth}"
        self._depth += 1
        self.sql.conn.execute(f"SAVEPOINT {name}")
        try:
            callback()
        except BaseException:
            self.sql.conn.execute(f"ROLLBACK TO {name}")
            self.sql.conn.execute(f"RELEASE {name}")
            raise
        finally:
            self._depth -= 1
        self.sql.conn.execute(f"RELEASE {name}")
