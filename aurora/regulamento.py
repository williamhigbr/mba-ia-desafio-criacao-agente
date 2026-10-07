"""Leitura do regulamento interno (dados/regulamento.md) por capítulo.

O regulamento nunca é carregado inteiro no contexto de um agente: o
especialista lê só o capítulo de que precisa (Garantia 4).
"""

import re
from functools import cache

from aurora import config

_CABECALHO = re.compile(r"^## Capítulo ([IVXLC]+): (.+)$", re.MULTILINE)


@cache
def _capitulos() -> tuple[dict, ...]:
    texto = (config.DADOS_DIR / "regulamento.md").read_text(encoding="utf-8")
    cabecalhos = list(_CABECALHO.finditer(texto))
    capitulos = []
    for ordem, cabecalho in enumerate(cabecalhos, start=1):
        fim = cabecalhos[ordem].start() if ordem < len(cabecalhos) else len(texto)
        capitulos.append(
            {
                "numero": ordem,
                "romano": cabecalho.group(1),
                "titulo": cabecalho.group(2).strip(),
                "texto": texto[cabecalho.end() : fim].strip(),
            }
        )
    return tuple(capitulos)


def listar_capitulos() -> list[dict]:
    """Só número e título de cada capítulo, sem o texto."""
    return [
        {"numero": c["numero"], "romano": c["romano"], "titulo": c["titulo"]}
        for c in _capitulos()
    ]


def obter_capitulo(numero: int) -> dict | None:
    for capitulo in _capitulos():
        if capitulo["numero"] == numero:
            return dict(capitulo)
    return None
