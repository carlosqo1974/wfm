import re
from typing import Tuple, Dict, Any
from .base import BaseConnector


def _require_pymysql():
    try:
        import pymysql
        return pymysql
    except ImportError:
        raise RuntimeError("pymysql no instalado. Ejecuta: pip3 install pymysql")


def _validate_select(sql: str) -> None:
    """Only SELECT / WITH (CTE) queries are allowed."""
    cleaned = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    cleaned = re.sub(r"--[^\n]*", "", cleaned)
    tokens = cleaned.strip().split()
    if not tokens or tokens[0].upper() not in ("SELECT", "WITH"):
        raise ValueError("Solo se permiten consultas SELECT o CTEs (WITH …).")


def _serialize_row(row) -> list:
    out = []
    for v in row:
        if v is None:
            out.append(None)
        elif hasattr(v, "isoformat"):   # date / datetime / time
            out.append(v.isoformat())
        else:
            try:
                f = float(v)
                # keep int look for whole numbers
                out.append(int(f) if f == int(f) else f)
            except (TypeError, ValueError):
                out.append(str(v))
    return out


class MySQLConnector(BaseConnector):
    def __init__(self, host: str, port: int, database: str, username: str, password: str):
        self.host = host
        self.port = int(port)
        self.database = database
        self.username = username
        self.password = password

    def _connect(self):
        pymysql = _require_pymysql()
        return pymysql.connect(
            host=self.host,
            port=self.port,
            database=self.database,
            user=self.username,
            password=self.password,
            connect_timeout=10,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.Cursor,
        )

    def test_connection(self) -> Tuple[bool, str]:
        try:
            conn = self._connect()
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            conn.close()
            return True, "Conexión exitosa"
        except Exception as exc:
            return False, str(exc)

    def execute_query(self, sql: str, max_rows: int = 1000) -> Dict[str, Any]:
        _validate_select(sql)
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                columns = [d[0] for d in (cur.description or [])]
                raw_rows = cur.fetchmany(max_rows)
                rows = [_serialize_row(r) for r in raw_rows]
            return {"columns": columns, "rows": rows, "count": len(rows)}
        finally:
            conn.close()
