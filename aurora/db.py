"""Conexão com o SQLite do condomínio e schema.

Decisões:
- Uma conexão por operação: nada de conexão compartilhada entre requisições.
- WAL + busy_timeout: leitores não bloqueiam o escritor e escritores esperam
  o lock em vez de falhar com "database is locked".
- Escritas em ``BEGIN IMMEDIATE``: o lock de escrita é pego no início da
  transação, então duas escritas concorrentes são serializadas pelo banco.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from aurora import config

BUSY_TIMEOUT_MS = 5000

# Instruções separadas (e não executescript) porque executescript faz COMMIT
# implícito e quebraria a transação do comando de restauração.
SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS reservas (
        codigo              TEXT PRIMARY KEY,                -- nunca reutilizado (regra 5)
        apartamento         TEXT NOT NULL,
        area                TEXT NOT NULL,
        data                TEXT NOT NULL,                   -- AAAA-MM-DD
        status              TEXT NOT NULL DEFAULT 'ativa'
                            CHECK (status IN ('ativa', 'cancelada')),
        chave_idempotencia  TEXT UNIQUE                      -- function_call_id da tool
    )
    """,
    # Garantia 5 + regra 1: no máximo uma reserva ATIVA por área e data.
    # Vale no instante do INSERT, inclusive com gravações simultâneas.
    # Parcial para que reservas canceladas não bloqueiem a data.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_reserva_ativa
        ON reservas (area, data) WHERE status = 'ativa'
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_reservas_apartamento
        ON reservas (apartamento, status)
    """,
    """
    CREATE TABLE IF NOT EXISTS visitantes (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        apartamento         TEXT NOT NULL,
        nome                TEXT NOT NULL,
        data                TEXT NOT NULL,
        chave_idempotencia  TEXT UNIQUE
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_visitantes_apartamento
        ON visitantes (apartamento)
    """,
    # Mapa session_id -> apartamento, definido uma única vez na criação da sessão.
    """
    CREATE TABLE IF NOT EXISTS sessoes (
        session_id   TEXT PRIMARY KEY,
        apartamento  TEXT NOT NULL
    )
    """,
)


def _abrir() -> sqlite3.Connection:
    caminho = config.CONDOMINIO_DB
    caminho.parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None: o módulo sqlite3 não abre transações sozinho;
    # quem controla BEGIN/COMMIT é ``transacao()``.
    conn = sqlite3.connect(caminho, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    return conn


@contextmanager
def conectar() -> Iterator[sqlite3.Connection]:
    """Conexão para leituras (autocommit)."""
    conn = _abrir()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transacao() -> Iterator[sqlite3.Connection]:
    """Transação de escrita com lock pego no início (BEGIN IMMEDIATE)."""
    with conectar() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")


def criar_schema(conn: sqlite3.Connection) -> None:
    for instrucao in SCHEMA:
        conn.execute(instrucao)


def garantir_schema() -> None:
    """Cria as tabelas se ainda não existirem (idempotente)."""
    with transacao() as conn:
        criar_schema(conn)
