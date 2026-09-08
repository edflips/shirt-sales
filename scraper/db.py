"""A thin sqlite3-compatibility shim over the raw libsql (Turso) client.

libsql's Python bindings advertise a "sqlite3-compatible" API but, measured
directly (2026-09-08), fall short of what this project's SQL relies on:

- No row_factory — fetchone/fetchall always return plain tuples, never
  dict-like rows, even though every query in store.py/mine.py reads results
  with `row["column"]`.
- Only positional `?` parameters work. `:name`, `@name`, and `$name` style
  placeholders all raise "Expected a list or tuple for parameters" — but our
  INSERT/UPDATE statements are written with `:name` placeholders bound
  against dicts throughout (matches the original sqlite3 code, which does
  support that style).

`cursor.description` is populated in the standard (name, None, None, ...)
shape, which is enough to reconstruct both: translate `:name` tokens to `?`
positionally before handing the query to libsql, and wrap returned rows in a
dict built from the column names. This lets every existing query in
store.py/mine.py keep its exact SQL text unchanged.

Read-only analytics code (analysis/export.py, analysis/visualise.py) uses
pandas' read_sql_query, which works fine against the *raw*, unshimmed libsql
connection (confirmed directly — correct dtypes, no shim needed) since those
queries use no parameters and don't need dict-style row access. Use
`connection.raw` for those call sites rather than routing pandas through
this shim, which does not implement a full DBAPI2 `cursor()`.
"""
from __future__ import annotations

import re

import libsql

_NAMED_PARAM_RE = re.compile(r":(\w+)")


def _translate(sql: str, params):
    if params is None:
        return sql, []
    if isinstance(params, dict):
        names = _NAMED_PARAM_RE.findall(sql)
        return _NAMED_PARAM_RE.sub("?", sql), [params[n] for n in names]
    return sql, list(params)


class Row(dict):
    """dict by column name, but also supports sqlite3.Row's positional int
    access (fetchone()[0]) — a dict subclass so dict(row)/**row still work,
    with __getitem__ special-cased for int keys.
    """

    def __init__(self, columns, values):
        super().__init__(zip(columns, values))
        self._values = tuple(values)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


class Cursor:
    def __init__(self, raw_cursor):
        self._c = raw_cursor
        self.lastrowid = getattr(raw_cursor, "lastrowid", None)
        self.rowcount = getattr(raw_cursor, "rowcount", None)

    def _columns(self):
        return [d[0] for d in (self._c.description or [])]

    def fetchone(self):
        row = self._c.fetchone()
        return None if row is None else Row(self._columns(), row)

    def fetchall(self):
        columns = self._columns()
        return [Row(columns, r) for r in self._c.fetchall()]


class Connection:
    """Connects to a Turso database as an embedded replica: a local file that
    syncs with the remote. Reads hit the local copy; writes go straight to
    the remote leader. `.raw` exposes the underlying libsql connection for
    code (pandas) that doesn't need the :name/dict-row translation below.
    """

    def __init__(self, local_path: str, sync_url: str, auth_token: str):
        self.raw = libsql.connect(local_path, sync_url=sync_url, auth_token=auth_token)
        self.raw.sync()

    def execute(self, sql, params=None) -> Cursor:
        sql2, plist = _translate(sql, params)
        return Cursor(self.raw.execute(sql2, plist))

    def executemany(self, sql, seq_of_params) -> Cursor:
        seq_of_params = list(seq_of_params)
        if seq_of_params and isinstance(seq_of_params[0], dict):
            names = _NAMED_PARAM_RE.findall(sql)
            sql2 = _NAMED_PARAM_RE.sub("?", sql)
            plist = [[p[n] for n in names] for p in seq_of_params]
        else:
            sql2 = sql
            plist = [list(p) for p in seq_of_params]
        return Cursor(self.raw.executemany(sql2, plist))

    def executescript(self, sql):
        return self.raw.executescript(sql)

    def commit(self):
        return self.raw.commit()

    def sync(self):
        return self.raw.sync()

    def close(self):
        return self.raw.close()


def connect(local_path: str, sync_url: str, auth_token: str) -> Connection:
    return Connection(local_path, sync_url, auth_token)
