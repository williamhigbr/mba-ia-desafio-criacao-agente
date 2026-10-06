"""Restaura o condomínio ao estado de ``dados/*.json``.

- Recria o schema e recarrega reservas e visitantes dos arquivos originais.
- Apaga também as sessões: o mapa session_id -> apartamento e o banco de
  sessões do ADK (``var/sessoes.db``). Rode com a API parada.

Uso: ``uv run aurora-restaurar``
"""

import json

from aurora import config, db

_TABELAS = ("reservas", "visitantes", "sessoes")


def _ler(nome: str) -> list[dict]:
    with open(config.DADOS_DIR / nome, encoding="utf-8") as arquivo:
        return json.load(arquivo)


def _apagar_banco_de_sessoes() -> None:
    base = config.SESSOES_DB
    for caminho in (base, base.with_name(base.name + "-wal"), base.with_name(base.name + "-shm")):
        caminho.unlink(missing_ok=True)


def restaurar() -> dict[str, int]:
    reservas = _ler("reservas.json")
    visitantes = _ler("visitantes.json")

    _apagar_banco_de_sessoes()

    with db.transacao() as conn:
        for tabela in _TABELAS:
            conn.execute(f"DROP TABLE IF EXISTS {tabela}")
        db.criar_schema(conn)
        conn.executemany(
            "INSERT INTO reservas (codigo, apartamento, area, data) VALUES (?, ?, ?, ?)",
            [(r["codigo"], r["apartamento"], r["area"], r["data"]) for r in reservas],
        )
        conn.executemany(
            "INSERT INTO visitantes (apartamento, nome, data) VALUES (?, ?, ?)",
            [(v["apartamento"], v["nome"], v["data"]) for v in visitantes],
        )

    return {"reservas": len(reservas), "visitantes": len(visitantes)}


def main() -> None:
    totais = restaurar()
    print(
        f"Dados restaurados em {config.CONDOMINIO_DB}: "
        f"{totais['reservas']} reservas, {totais['visitantes']} visitantes. "
        "Sessões apagadas."
    )


if __name__ == "__main__":
    main()
