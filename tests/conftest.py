"""Общие фикстуры для тестов."""
import pytest
from src.database.db import init_db


@pytest.fixture(autouse=True)
def in_memory_db():
    """Каждый тест получает чистую in-memory БД."""
    init_db("sqlite:///:memory:")
