import pytest

from aurora import config, restaurar


@pytest.fixture
def banco(tmp_path, monkeypatch):
    """Banco temporário restaurado a partir de dados/*.json."""
    monkeypatch.setattr(config, "CONDOMINIO_DB", tmp_path / "condominio.db")
    monkeypatch.setattr(config, "SESSOES_DB", tmp_path / "sessoes.db")
    restaurar.restaurar()
    return tmp_path
