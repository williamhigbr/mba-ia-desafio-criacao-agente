"""Caminhos e constantes do projeto.

Os módulos leem estes atributos no momento do uso (``config.CONDOMINIO_DB``),
o que permite aos testes apontar para bancos temporários.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Variáveis do .env (GOOGLE_API_KEY, AURORA_MODELO...). Não sobrescreve as que
# já estão no ambiente.
load_dotenv(BASE_DIR / ".env", override=False)

# Estado inicial do condomínio (somente leitura, nunca alterado).
DADOS_DIR = BASE_DIR / "dados"

# Bancos SQLite locais (fora do Git). Arquivos separados para que sessões do
# ADK e dados do condomínio não disputem o mesmo lock de escrita.
VAR_DIR = BASE_DIR / "var"
CONDOMINIO_DB = VAR_DIR / "condominio.db"
SESSOES_DB = VAR_DIR / "sessoes.db"

# Igual ao nome da pasta do pacote: o `adk web` usa o nome da pasta como app.
APP_NAME = "aurora"

# Modelo Gemini de todos os agentes. Alias da família Flash por padrão;
# fixe uma versão específica no .env se preferir.
MODELO = os.getenv("AURORA_MODELO") or "gemini-flash-latest"
