"""Testes das tools sem LLM.

As tools são chamadas pelo ``FunctionTool.run_async`` do próprio ADK, o mesmo
caminho usado quando o modelo faz um function call. Só o ToolContext é falso:
ele simula o que a sessão fornece (user_id, function_call_id) e o que o ADK
preenche na reexecução após uma confirmação (tool_confirmation).
"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from google.adk.tools import FunctionTool
from google.adk.tools.tool_confirmation import ToolConfirmation

from aurora import repositorio
from aurora.tools import regulamento as tools_regulamento
from aurora.tools import reservas as tools_reservas
from aurora.tools import visitantes as tools_visitantes


class FakeToolContext:
    """Imita os atributos de ToolContext que as tools usam."""

    def __init__(self, user_id="101", function_call_id="fc-1", tool_confirmation=None):
        self.user_id = user_id
        self.function_call_id = function_call_id
        self.tool_confirmation = tool_confirmation
        self.actions = SimpleNamespace(skip_summarization=False, requested_tool_confirmations={})

    def request_confirmation(self, *, hint=None, payload=None):
        self.actions.requested_tool_confirmations[self.function_call_id] = ToolConfirmation(
            hint=hint or "", payload=payload
        )

    @property
    def pediu_confirmacao(self) -> bool:
        return bool(self.actions.requested_tool_confirmations)


def _como_tool(item) -> FunctionTool:
    return item if isinstance(item, FunctionTool) else FunctionTool(item)


TODAS_AS_TOOLS = [
    _como_tool(t)
    for t in tools_reservas.TOOLS + tools_visitantes.TOOLS + tools_regulamento.TOOLS
]


def chamar(tool, ctx=None, **args):
    """Executa a tool como o ADK executaria um function call do modelo."""
    return asyncio.run(_como_tool(tool).run_async(args=args, tool_context=ctx or FakeToolContext()))


def aprovado(confirmed=True):
    return ToolConfirmation(confirmed=confirmed)


def reservas_salao(apartamento, data):
    return [
        r
        for r in repositorio.listar_reservas(apartamento)
        if r["area"] == "salao-de-festas" and r["data"] == data
    ]


# --- Garantia 2: o apartamento nunca é parâmetro ---


@pytest.mark.parametrize("tool", TODAS_AS_TOOLS, ids=lambda t: t.name)
def test_nenhuma_tool_expoe_apartamento_ao_modelo(tool):
    # O que o modelo vê: os parâmetros declarados da tool.
    declaracao = tool._get_declaration()
    if declaracao.parameters_json_schema is not None:
        parametros = set(declaracao.parameters_json_schema.get("properties", {}))
    elif declaracao.parameters is not None:
        parametros = set(declaracao.parameters.properties or {})
    else:
        parametros = set()
    assert not any("apartamento" in p for p in parametros)
    assert "tool_context" not in parametros


def test_apartamento_inventado_pelo_modelo_e_ignorado(banco):
    # Mesmo que o modelo mande um argumento extra, a reserva vai para o da sessão.
    resultado = chamar(
        tools_reservas.reservar_area,
        FakeToolContext(user_id="101"),
        area="quadra",
        data="2030-04-06",
        apartamento="302",
    )
    assert resultado["status"] == "reservada"
    assert any(r["data"] == "2030-04-06" for r in repositorio.listar_reservas("101"))
    assert all(r["data"] != "2030-04-06" for r in repositorio.listar_reservas("302"))


def test_listar_minhas_reservas_so_do_apartamento_da_sessao(banco):
    resultado = chamar(tools_reservas.listar_minhas_reservas, FakeToolContext(user_id="101"))
    assert resultado["reservas"] == [{"codigo": "RSV-1377", "area": "quadra", "data": "2030-03-09"}]
    assert "RSV-4821" not in json.dumps(resultado)


def test_listar_meus_visitantes_so_do_apartamento_da_sessao(banco):
    resultado = chamar(tools_visitantes.listar_meus_visitantes, FakeToolContext(user_id="101"))
    assert resultado == {"status": "ok", "visitantes": []}


def test_cancelar_reserva_de_outro_apartamento_nao_vaza_nada(banco):
    # Passo 4: o 101 pede para cancelar a reserva do 302.
    resultado = chamar(
        tools_reservas.cancelar_reserva, area="salao-de-festas", data="2030-03-16"
    )
    assert resultado["status"] == "nao_encontrada"
    assert "RSV-4821" not in json.dumps(resultado) and "302" not in json.dumps(resultado)
    assert [r["codigo"] for r in repositorio.listar_reservas("302")] == ["RSV-4821"]

    pelo_codigo = chamar(tools_reservas.cancelar_reserva, codigo="RSV-4821")
    assert pelo_codigo["status"] == "nao_encontrada"


def test_data_ocupada_por_outro_nao_revela_o_dono(banco):
    # Passo 10: salão em 2030-03-16 já é do 302.
    disponibilidade = chamar(
        tools_reservas.consultar_disponibilidade, area="salao-de-festas", data="2030-03-16"
    )
    ctx = FakeToolContext()
    reserva = chamar(tools_reservas.reservar_area, ctx, area="salao-de-festas", data="2030-03-16")

    assert disponibilidade == {
        "status": "ok",
        "area": "salao-de-festas",
        "data": "2030-03-16",
        "livre": False,
    }
    assert reserva["status"] == "indisponivel"
    assert not ctx.pediu_confirmacao  # não pede para aprovar cobrança impossível
    for retorno in (disponibilidade, reserva):
        assert "RSV-4821" not in json.dumps(retorno)
        assert "302" not in json.dumps(retorno)
    assert reservas_salao("101", "2030-03-16") == []


# --- Regra 4: cancelar a própria reserva, sem confirmação ---


def test_cancelar_propria_reserva_sem_confirmacao(banco):
    ctx = FakeToolContext()
    resultado = chamar(tools_reservas.cancelar_reserva, ctx, area="quadra", data="2030-03-09")
    assert resultado == {
        "status": "cancelada",
        "codigo": "RSV-1377",
        "area": "quadra",
        "data": "2030-03-09",
    }
    assert not ctx.pediu_confirmacao
    assert repositorio.listar_reservas("101") == []


# --- Garantia 1: cobrança só com confirmação ---


def test_area_sem_taxa_reserva_direto(banco):
    # Passo 6.
    ctx = FakeToolContext()
    resultado = chamar(tools_reservas.reservar_area, ctx, area="quadra", data="2030-04-06")
    assert resultado["status"] == "reservada"
    assert not ctx.pediu_confirmacao


def test_area_com_taxa_pede_confirmacao_e_nao_grava(banco):
    # Passo 7, primeira metade.
    ctx = FakeToolContext(function_call_id="fc-salao")
    resultado = chamar(tools_reservas.reservar_area, ctx, area="salao-de-festas", data="2030-04-20")

    assert resultado["status"] == "aguardando_confirmacao"
    pedido = ctx.actions.requested_tool_confirmations["fc-salao"]
    assert pedido.payload == {"area": "salao-de-festas", "data": "2030-04-20", "taxa": 150.0}
    assert "150,00" in pedido.hint or "150.00" in pedido.hint
    assert ctx.actions.skip_summarization is True
    assert reservas_salao("101", "2030-04-20") == []


def test_negar_nao_grava(banco):
    # Passo 7, segunda metade: o ADK reexecuta com confirmed=False.
    ctx = FakeToolContext(function_call_id="fc-salao", tool_confirmation=aprovado(False))
    resultado = chamar(tools_reservas.reservar_area, ctx, area="salao-de-festas", data="2030-04-20")
    assert resultado["status"] == "negado"
    assert reservas_salao("101", "2030-04-20") == []


def test_aprovar_grava_uma_vez_mesmo_se_reexecutado(banco):
    # Passo 8: reexecução com confirmed=True. A retomada do ADK é at-least-once,
    # então a mesma chamada (mesmo function_call_id) pode rodar de novo.
    ctx = FakeToolContext(function_call_id="fc-salao", tool_confirmation=aprovado())
    primeira = chamar(tools_reservas.reservar_area, ctx, area="salao-de-festas", data="2030-04-20")
    segunda = chamar(tools_reservas.reservar_area, ctx, area="salao-de-festas", data="2030-04-20")

    assert primeira["status"] == segunda["status"] == "reservada"
    assert primeira["codigo"] == segunda["codigo"]
    assert len(reservas_salao("101", "2030-04-20")) == 1


def test_frase_de_confirmacao_no_chat_nao_muda_nada(banco):
    # Passo 11 em nível de tool: sem tool_confirmation, a tool nunca grava,
    # por mais que o modelo "acredite" que o morador já confirmou.
    ctx = FakeToolContext()
    chamar(tools_reservas.reservar_area, ctx, area="churrasqueira", data="2030-06-01")
    assert ctx.pediu_confirmacao
    assert all(r["area"] != "churrasqueira" for r in repositorio.listar_reservas("101"))


def test_visitante_exige_confirmacao(banco):
    ctx = FakeToolContext(function_call_id="fc-visita")
    resultado = chamar(
        tools_visitantes.autorizar_visitante_tool, ctx, nome="Joana Ribeiro", data="2030-04-21"
    )
    assert "error" in resultado
    assert "fc-visita" in ctx.actions.requested_tool_confirmations
    assert repositorio.listar_visitantes("101") == []


def test_visitante_negado_nao_grava(banco):
    ctx = FakeToolContext(function_call_id="fc-visita", tool_confirmation=aprovado(False))
    chamar(tools_visitantes.autorizar_visitante_tool, ctx, nome="Joana Ribeiro", data="2030-04-21")
    assert repositorio.listar_visitantes("101") == []


def test_visitante_aprovado_grava_uma_vez(banco):
    ctx = FakeToolContext(function_call_id="fc-visita", tool_confirmation=aprovado())
    for _ in range(2):
        resultado = chamar(
            tools_visitantes.autorizar_visitante_tool, ctx, nome="Joana Ribeiro", data="2030-04-21"
        )
        assert resultado == {"status": "autorizado", "nome": "Joana Ribeiro", "data": "2030-04-21"}
    assert repositorio.listar_visitantes("101") == [{"nome": "Joana Ribeiro", "data": "2030-04-21"}]


# --- Garantia 5: duas aprovações simultâneas ---


def test_duas_aprovacoes_simultaneas_uma_vence(banco):
    # Passo 14 em nível de tool: os dois passaram pela consulta prévia, e o
    # INSERT decide. O perdedor recebe uma resposta normal.
    async def disputa():
        tool = FunctionTool(tools_reservas.reservar_area)
        args = {"area": "salao-de-festas", "data": "2030-05-11"}
        return await asyncio.gather(
            tool.run_async(
                args=args,
                tool_context=FakeToolContext("101", "fc-a", aprovado()),
            ),
            tool.run_async(
                args=args,
                tool_context=FakeToolContext("201", "fc-b", aprovado()),
            ),
        )

    resultados = asyncio.run(disputa())
    assert sorted(r["status"] for r in resultados) == ["indisponivel", "reservada"]
    total = len(reservas_salao("101", "2030-05-11")) + len(reservas_salao("201", "2030-05-11"))
    assert total == 1


# --- Validação de entrada ---


def test_area_e_data_invalidas_viram_erro_normal(banco):
    assert chamar(tools_reservas.reservar_area, area="piscina", data="2030-04-20")["status"] == "erro"
    assert chamar(tools_reservas.reservar_area, area="quadra", data="20/04/2030")["status"] == "erro"
    assert (
        chamar(tools_reservas.consultar_disponibilidade, area="quadra", data="2030-02-30")["status"]
        == "erro"
    )


# --- Regulamento ---


def test_regulamento_lista_titulos_sem_texto():
    resultado = chamar(tools_regulamento.listar_capitulos)
    assert len(resultado["capitulos"]) == 14
    assert resultado["capitulos"][3] == {"numero": 4, "romano": "IV", "titulo": "Piscina"}
    assert "texto" not in json.dumps(resultado)


def test_regulamento_le_so_o_capitulo_pedido():
    resultado = chamar(tools_regulamento.ler_capitulo, numero=4)
    assert "Aos domingos e feriados, a piscina funciona das 9h às 20h." in resultado["texto"]
    assert "Art. 1º" not in resultado["texto"]  # Capítulo I
    assert "Animais" not in resultado["texto"]  # Capítulo VIII
    assert chamar(tools_regulamento.ler_capitulo, numero=99)["status"] == "erro"
