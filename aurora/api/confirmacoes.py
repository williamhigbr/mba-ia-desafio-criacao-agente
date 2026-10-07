"""Confirmações pendentes, derivadas dos eventos persistidos da sessão.

Uma confirmação está pendente quando existe o function call
``adk_request_confirmation`` e ainda não existe o function response com o
mesmo id. A sessão (SQLite) é a única fonte da verdade: as pendências
sobrevivem ao reinício da API sem nenhum estado paralelo, e uma confirmação
já respondida deixa de estar pendente no mesmo instante em que a resposta é
gravada.
"""

from dataclasses import dataclass
from typing import Any

from google.adk.events import Event

CONFIRMACAO = "adk_request_confirmation"


@dataclass(frozen=True)
class Pendencia:
    id: str
    invocation_id: str  # uso interno (retomada); não vai para a resposta da API
    acao: str
    detalhes: dict[str, Any]

    def para_contrato(self) -> dict[str, Any]:
        return {"id": self.id, "acao": self.acao, "detalhes": self.detalhes}


def _detalhes(args: dict[str, Any]) -> dict[str, Any]:
    """Argumentos da tool que será executada + payload que a tool anexou (ex.: taxa)."""
    original = args.get("originalFunctionCall") or {}
    detalhes = dict(original.get("args") or {})
    payload = (args.get("toolConfirmation") or {}).get("payload")
    if isinstance(payload, dict):
        detalhes.update({k: v for k, v in payload.items() if k not in detalhes})
    return detalhes


def confirmacoes_pendentes(eventos: list[Event]) -> list[Pendencia]:
    pedidos: dict[str, Pendencia] = {}
    respondidos: set[str] = set()
    for evento in eventos:
        for chamada in evento.get_function_calls():
            if chamada.name == CONFIRMACAO and chamada.id:
                args = chamada.args or {}
                pedidos[chamada.id] = Pendencia(
                    id=chamada.id,
                    invocation_id=evento.invocation_id,
                    acao=(args.get("originalFunctionCall") or {}).get("name", ""),
                    detalhes=_detalhes(args),
                )
        for resposta in evento.get_function_responses():
            if resposta.name == CONFIRMACAO and resposta.id:
                respondidos.add(resposta.id)
    return [p for id_, p in pedidos.items() if id_ not in respondidos]
