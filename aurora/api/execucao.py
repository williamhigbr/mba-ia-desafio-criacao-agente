"""Execução do Runner e montagem da resposta das rotas de conversa."""

from typing import Any

from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.sessions import Session
from google.genai import types

from aurora import config
from aurora.api.confirmacoes import CONFIRMACAO, Pendencia, confirmacoes_pendentes


def _texto_da_resposta(eventos: list[Event]) -> str:
    """Textos finais dos agentes nesta execução (sem pensamentos nem parciais)."""
    trechos = []
    for evento in eventos:
        if evento.author == "user" or evento.partial or not evento.content:
            continue
        for parte in evento.content.parts or []:
            if parte.text and not parte.thought:
                trechos.append(parte.text.strip())
    return "\n\n".join(t for t in trechos if t)


async def _carregar(runner: Runner, sessao: Session) -> Session:
    atual = await runner.session_service.get_session(
        app_name=config.APP_NAME, user_id=sessao.user_id, session_id=sessao.id
    )
    assert atual is not None
    return atual


async def _executar(
    runner: Runner,
    sessao: Session,
    mensagem: types.Content,
    invocation_id: str | None = None,
) -> dict[str, Any]:
    eventos = [
        evento
        async for evento in runner.run_async(
            user_id=sessao.user_id,
            session_id=sessao.id,
            invocation_id=invocation_id,
            new_message=mensagem,
        )
    ]
    atual = await _carregar(runner, sessao)
    return {
        "resposta": _texto_da_resposta(eventos),
        "confirmacoes_pendentes": [p.para_contrato() for p in confirmacoes_pendentes(atual.events)],
    }


async def enviar_mensagem(runner: Runner, sessao: Session, texto: str) -> dict[str, Any]:
    mensagem = types.Content(role="user", parts=[types.Part(text=texto)])
    return await _executar(runner, sessao, mensagem)


async def responder_confirmacao(
    runner: Runner, sessao: Session, pendencia: Pendencia, confirmado: bool
) -> dict[str, Any]:
    """Devolve ao ADK a resposta do morador, no formato que o ADK espera.

    O Runner reexecuta a tool original no agente que pediu a confirmação, com
    ``tool_context.tool_confirmation.confirmed = confirmado``.
    """
    resposta = types.FunctionResponse(
        id=pendencia.id, name=CONFIRMACAO, response={"confirmed": confirmado}
    )
    mensagem = types.Content(role="user", parts=[types.Part(function_response=resposta)])
    return await _executar(runner, sessao, mensagem, invocation_id=pendencia.invocation_id)
