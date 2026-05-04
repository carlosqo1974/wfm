from .mysql_connector import MySQLConnector


def get_connector(db_type: str, host: str, port: int, database: str, username: str, password: str):
    if db_type == "mysql":
        return MySQLConnector(host, port, database, username, password)
    raise ValueError(f"Tipo de conector no soportado: {db_type}")
