"""Tools do especialista de reservas.

- Garantia 1: ``reservar_area`` pede confirmação quando a área tem taxa > 0.
  Quem decide é o código, pela taxa em dados/areas.json, não o modelo.
- Garantia 2: o apartamento vem da sessão; retornos nunca revelam o dono de
  uma reserva de outro apartamento.
- Garantia 5: a gravação é decidida pelo índice único no INSERT
  (``repositorio.criar_reserva``); a consulta prévia é só para a conversa.

As chamadas ao repositório (SQLite síncrono) rodam em ``asyncio.to_thread``
para não bloquear o event loop enquanto uma escrita espera o lock.
"""

import asyncio

from google.adk.tools import ToolContext

from aurora import repositorio
from aurora.repositorio import DataOcupada, ErroDominio
from aurora.tools._contexto import apartamento_da_sessao

_MSG_INDISPONIVEL = "Essa área já está reservada nessa data. Sugira outra data ao morador."


def _ids_validos() -> str:
    return ", ".join(a["id"] for a in repositorio.listar_areas())


def _erro(mensagem: str) -> dict:
    return {"status": "erro", "mensagem": mensagem}


def _erro_area() -> dict:
    return _erro(f"Área inexistente. Use um destes ids: {_ids_validos()}.")


def _resumo_area(info: dict) -> dict:
    return {
        "area": info["id"],
        "nome": info["nome"],
        "taxa": info["taxa"],
        "gera_cobranca": info["taxa"] > 0,
    }


async def _indisponivel(apartamento: str, area: str, data: str) -> dict:
    """Data ocupada: só diz se a reserva é do próprio morador, nunca de quem é."""
    proprias = await asyncio.to_thread(repositorio.listar_reservas, apartamento)
    for reserva in proprias:
        if reserva["area"] == area and reserva["data"] == data:
            return {
                "status": "ja_reservada",
                "mensagem": "O morador já tem esta reserva.",
                **reserva,
            }
    return {"status": "indisponivel", "area": area, "data": data, "mensagem": _MSG_INDISPONIVEL}


async def listar_areas() -> dict:
    """Lista as áreas comuns que podem ser reservadas, com a taxa de cada uma.

    Use para descobrir o id correto de uma área ou se ela gera cobrança.
    """
    return {"status": "ok", "areas": [_resumo_area(a) for a in repositorio.listar_areas()]}


async def listar_minhas_reservas(tool_context: ToolContext) -> dict:
    """Lista as reservas ativas do apartamento do morador desta conversa."""
    apartamento = apartamento_da_sessao(tool_context)
    reservas = await asyncio.to_thread(repositorio.listar_reservas, apartamento)
    return {"status": "ok", "reservas": reservas}


async def consultar_disponibilidade(area: str, data: str) -> dict:
    """Informa se uma área comum está livre em uma data.

    Args:
        area: id da área (salao-de-festas, churrasqueira ou quadra).
        data: data no formato AAAA-MM-DD.
    """
    if not repositorio.area_existe(area):
        return _erro_area()
    try:
        livre = await asyncio.to_thread(repositorio.data_livre, area, data)
    except ErroDominio as erro:
        return _erro(str(erro))
    return {"status": "ok", "area": area, "data": data, "livre": livre}


async def reservar_area(area: str, data: str, tool_context: ToolContext) -> dict:
    """Reserva uma área comum para o apartamento do morador desta conversa.

    Se a área tiver taxa, a reserva só é gravada depois que o morador aprovar
    a cobrança pelo sistema de confirmações; até lá ela fica pendente.

    Args:
        area: id da área (salao-de-festas, churrasqueira ou quadra).
        data: data no formato AAAA-MM-DD.
    """
    apartamento = apartamento_da_sessao(tool_context)
    info = repositorio.obter_area(area)
    if info is None:
        return _erro_area()
    try:
        repositorio.validar_data(data)
    except ErroDominio as erro:
        return _erro(str(erro))

    gera_cobranca = info["taxa"] > 0
    # Só existe quando o ADK reexecuta a tool depois de uma resposta enviada
    # pela rota de confirmações. Nada que o morador escreva no chat cria isso.
    confirmacao = tool_context.tool_confirmation

    if gera_cobranca and confirmacao is not None and not confirmacao.confirmed:
        return {"status": "negado", "mensagem": "O morador recusou a cobrança. Nada foi reservado."}

    if confirmacao is None:
        # Consulta prévia: evita pedir confirmação para uma data já ocupada.
        # Não garante nada; quem decide é o INSERT mais abaixo.
        if not await asyncio.to_thread(repositorio.data_livre, area, data):
            return await _indisponivel(apartamento, area, data)

        if gera_cobranca:
            tool_context.request_confirmation(
                hint=(
                    f"Reservar {info['nome']} em {data} gera cobrança de "
                    f"R$ {info['taxa']:.2f}. Aprova?"
                ),
                payload={"area": area, "data": data, "taxa": info["taxa"]},
            )
            # Mesmo comportamento do FunctionTool(require_confirmation=True):
            # a execução para aqui, sem o modelo resumir o retorno.
            tool_context.actions.skip_summarization = True
            return {
                "status": "aguardando_confirmacao",
                "mensagem": "A reserva gera cobrança e aguarda a aprovação do morador.",
            }

    try:
        # function_call_id é o mesmo na reexecução após a confirmação, então
        # uma segunda execução da mesma chamada não grava outra reserva.
        codigo = await asyncio.to_thread(
            repositorio.criar_reserva, apartamento, area, data, tool_context.function_call_id
        )
    except DataOcupada:
        return await _indisponivel(apartamento, area, data)
    except ErroDominio as erro:
        return _erro(str(erro))

    return {
        "status": "reservada",
        "codigo": codigo,
        "area": area,
        "nome": info["nome"],
        "data": data,
        "cobranca": info["taxa"],
    }


async def cancelar_reserva(
    tool_context: ToolContext, codigo: str = "", area: str = "", data: str = ""
) -> dict:
    """Cancela uma reserva do apartamento do morador desta conversa.

    Informe o código da reserva OU a área e a data.

    Args:
        codigo: código da reserva (ex.: RSV-1234), se o morador souber.
        area: id da área (salao-de-festas, churrasqueira ou quadra).
        data: data da reserva no formato AAAA-MM-DD.
    """
    apartamento = apartamento_da_sessao(tool_context)
    try:
        cancelada = await asyncio.to_thread(
            repositorio.cancelar_reserva,
            apartamento,
            codigo=codigo or None,
            area=area or None,
            data=data or None,
        )
    except ErroDominio as erro:
        return _erro(str(erro))

    if cancelada is None:
        # Mesma resposta para "não existe" e "é de outro apartamento".
        return {
            "status": "nao_encontrada",
            "mensagem": "Não há reserva ativa com esses dados entre as reservas do morador.",
        }
    return {"status": "cancelada", **cancelada}


TOOLS = [listar_areas, listar_minhas_reservas, consultar_disponibilidade, reservar_area, cancelar_reserva]
