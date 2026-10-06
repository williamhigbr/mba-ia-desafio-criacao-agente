"""Tools do especialista de visitantes.

Autorizar visitante libera acesso ao prédio, então a tool SEMPRE exige
confirmação (Garantia 1). Aqui se usa a forma booleana do ADK,
``FunctionTool(..., require_confirmation=True)``: o próprio ADK intercepta a
chamada e só executa a função depois da aprovação pela rota de confirmações.
"""

import asyncio

from google.adk.tools import FunctionTool, ToolContext

from aurora import repositorio
from aurora.repositorio import ErroDominio
from aurora.tools._contexto import apartamento_da_sessao


async def listar_meus_visitantes(tool_context: ToolContext) -> dict:
    """Lista as autorizações de visita do apartamento do morador desta conversa."""
    apartamento = apartamento_da_sessao(tool_context)
    visitantes = await asyncio.to_thread(repositorio.listar_visitantes, apartamento)
    return {"status": "ok", "visitantes": visitantes}


async def autorizar_visitante(nome: str, data: str, tool_context: ToolContext) -> dict:
    """Autoriza a entrada de um visitante no prédio para o apartamento do morador.

    A autorização só é registrada depois que o morador aprovar pelo sistema de
    confirmações.

    Args:
        nome: nome completo do visitante.
        data: data da visita no formato AAAA-MM-DD.
    """
    # Só chega aqui com tool_confirmation.confirmed == True (FunctionTool).
    apartamento = apartamento_da_sessao(tool_context)
    try:
        autorizado = await asyncio.to_thread(
            repositorio.autorizar_visitante,
            apartamento,
            nome,
            data,
            tool_context.function_call_id,
        )
    except ErroDominio as erro:
        return {"status": "erro", "mensagem": str(erro)}
    return {"status": "autorizado", **autorizado}


autorizar_visitante_tool = FunctionTool(autorizar_visitante, require_confirmation=True)

TOOLS = [listar_meus_visitantes, autorizar_visitante_tool]
