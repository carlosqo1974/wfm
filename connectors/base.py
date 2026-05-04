from abc import ABC, abstractmethod
from typing import Tuple, Dict, Any


class BaseConnector(ABC):
    @abstractmethod
    def test_connection(self) -> Tuple[bool, str]:
        """Returns (success, message)."""
        pass

    @abstractmethod
    def execute_query(self, sql: str, max_rows: int = 1000) -> Dict[str, Any]:
        """Returns {columns: list[str], rows: list[list], count: int}."""
        pass
