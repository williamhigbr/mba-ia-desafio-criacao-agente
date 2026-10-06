"""Agentes do assistente.

Cada módulo expõe uma função ``criar_*`` que monta o agente do zero. Um agente
do ADK só pode ter um pai, então montar de novo (em vez de reaproveitar uma
instância global) permite criar árvores independentes, por exemplo nos testes
com um modelo falso.

``modelo_para(nome_do_agente)`` decide o modelo de cada agente. O padrão é o
Gemini configurado em ``AURORA_MODELO``.

Atenção ao escrever instruções: o ADK trata ``{nome}`` como variável do state
da sessão. As instruções daqui não usam chaves.
"""

from collections.abc import Callable

from google.adk.models import BaseLlm, Gemini
from google.genai import types

from aurora import config

ModeloPara = Callable[[str], BaseLlm | str]

# 429 (limite do AI Studio) e 5xx transitórios: o cliente tenta de novo com
# backoff exponencial antes de a falha chegar ao agente.
_RETRY = types.HttpRetryOptions(
    attempts=5,
    initial_delay=2.0,
    max_delay=30.0,
    exp_base=2.0,
    http_status_codes=[429, 500, 502, 503, 504],
)


def modelo_padrao(nome_do_agente: str) -> BaseLlm:
    return Gemini(model=config.MODELO, retry_options=_RETRY)
