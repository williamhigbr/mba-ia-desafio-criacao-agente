"""Caminhos e constantes do projeto.

Os módulos leem estes atributos no momento do uso (``config.CONDOMINIO_DB``),
o que permite aos testes apontar para bancos temporários.
"""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Estado inicial do condomínio (somente leitura, nunca alterado).
DADOS_DIR = BASE_DIR / "dados"

# Bancos SQLite locais (fora do Git). Arquivos separados para que sessões do
# ADK e dados do condomínio não disputem o mesmo lock de escrita.
VAR_DIR = BASE_DIR / "var"
CONDOMINIO_DB = VAR_DIR / "condominio.db"
SESSOES_DB = VAR_DIR / "sessoes.db"

APP_NAME = "aurora"
