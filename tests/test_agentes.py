"""Testes dos agentes com o Runner real do ADK e um modelo roteirizado.

Nada aqui chama o Gemini. Cada agente recebe um ``ModeloRoteirizado`` que
devolve, em ordem, as respostas que o teste definiu (um function call ou um
texto), como se fosse o LLM. Todo o resto é o ADK de verdade: transferência
entre agentes, execução das tools, pedido de confirmação, AgentTool e o
roteamento da resposta de confirmação.

Esses testes provam a ARQUITETURA (quem roda o quê, o que fica gravado na
sessão). O comportamento do modelo real (escolher a tool certa) se testa no
``adk web``; a sessão persistida e o reinício, na Fase 5.
"""

import asyncio
import json


from google.adk.models import BaseLlm, LlmResponse
from google.adk.models._capabilities import LlmCapabilities
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import Field

from aurora import config, regulamento, repositorio
from aurora.agent import criar_app
from aurora.agents.root import INSTRUCAO as INSTRUCAO_ROOT

CONFIRMACAO = "adk_request_confirmation"


# --- Modelo falso ---


def chamada(_funcao: str, **args) -> types.Part:
    return types.Part(function_call=types.FunctionCall(name=_funcao, args=args))


def texto(conteudo: str) -> types.Part:
    return types.Part(text=conteudo)


class ModeloRoteirizado(BaseLlm):
    """Devolve as respostas do roteiro em ordem e guarda os pedidos recebidos."""

    roteiro: list = Field(default_factory=list)
    pedidos: list = Field(default_factory=list)

    @property
    def capabilities(self) -> LlmCapabilities:
        return LlmCapabilities(output_schema_and_tools=False)

    async def generate_content_async(self, llm_request, stream=False):
        self.pedidos.append(llm_request)
        if not self.roteiro:
            raise AssertionError(f"{self.model}: chamado além do roteiro")
        yield LlmResponse(content=types.Content(role="model", parts=[self.roteiro.pop(0)]))


class Cenario:
    """App completo com um modelo roteirizado por agente e sessão em memória."""

    def __init__(self, apartamento: str = "101"):
        self.modelos: dict[str, ModeloRoteirizado] = {}
        self.app = criar_app(self._modelo_para)
        self.servico = InMemorySessionService()
        self.runner = Runner(app=self.app, session_service=self.servico)
        self.apartamento = apartamento
        sessao = asyncio.run(
            self.servico.create_session(app_name=config.APP_NAME, user_id=apartamento)
        )
        self.session_id = sessao.id

    def _modelo_para(self, nome: str) -> ModeloRoteirizado:
        self.modelos[nome] = ModeloRoteirizado(model=f"roteiro-{nome}")
        return self.modelos[nome]

    def roteiro(self, agente: str, *partes: types.Part) -> None:
        self.modelos[agente].roteiro.extend(partes)

    def _rodar(self, mensagem: types.Content) -> list:
        async def coletar():
            return [
                ev
                async for ev in self.runner.run_async(
                    user_id=self.apartamento, session_id=self.session_id, new_message=mensagem
                )
            ]

        return asyncio.run(coletar())

    def enviar(self, conteudo: str) -> list:
        return self._rodar(types.Content(role="user", parts=[texto(conteudo)]))

    def responder_confirmacao(self, confirmacao_id: str, confirmado: bool) -> list:
        resposta = types.FunctionResponse(
            id=confirmacao_id, name=CONFIRMACAO, response={"confirmed": confirmado}
        )
        return self._rodar(types.Content(role="user", parts=[types.Part(function_response=resposta)]))

    def eventos(self) -> list:
        sessao = asyncio.run(
            self.servico.get_session(
                app_name=config.APP_NAME, user_id=self.apartamento, session_id=self.session_id
            )
        )
        return sessao.events

    def eventos_json(self) -> str:
        return json.dumps(
            [e.model_dump(mode="json", exclude_none=True) for e in self.eventos()],
            ensure_ascii=False,
        )

    def pendencias(self) -> list:
        pedidos, respondidos = {}, set()
        for ev in self.eventos():
            for fc in ev.get_function_calls():
                if fc.name == CONFIRMACAO:
                    pedidos[fc.id] = fc
            for fr in ev.get_function_responses():
                if fr.name == CONFIRMACAO:
                    respondidos.add(fr.id)
        return [fc for i, fc in pedidos.items() if i not in respondidos]


def respostas_de_tool(eventos, nome: str) -> list[tuple[str, dict]]:
    return [
        (ev.author, fr.response)
        for ev in eventos
        for fr in ev.get_function_responses()
        if fr.name == nome
    ]


def reservas_salao(apartamento: str, data: str) -> list:
    return [
        r
        for r in repositorio.listar_reservas(apartamento)
        if r["area"] == "salao-de-festas" and r["data"] == data
    ]


ROOT = "assistente_aurora"
RESERVAS = "especialista_reservas"
VISITANTES = "especialista_visitantes"
REGULAMENTO = "especialista_regulamento"


# --- Topologia ---


def test_topologia():
    app = criar_app(lambda nome: ModeloRoteirizado(model=nome))
    root = app.root_agent
    assert app.resumability_config.is_resumable
    assert [a.name for a in root.sub_agents] == [RESERVAS, VISITANTES]
    assert all(a.mode == "chat" for a in root.sub_agents)
    assert [t.name for t in root.tools] == [REGULAMENTO]
    assert type(root.tools[0]).__name__ == "AgentTool"  # não _SingleTurnAgentTool


def test_root_nao_recebe_regulamento_na_instrucao():
    texto_regulamento = (config.DADOS_DIR / "regulamento.md").read_text(encoding="utf-8")
    frases = [linha for linha in texto_regulamento.splitlines() if len(linha) > 40]
    assert not any(frase in INSTRUCAO_ROOT for frase in frases)
    for capitulo in regulamento.listar_capitulos():
        assert capitulo["titulo"] not in INSTRUCAO_ROOT


# --- Reservas por transferência ---


def test_quadra_transferencia_e_reserva_sem_confirmacao(banco):
    # Passo 6.
    c = Cenario()
    c.roteiro(ROOT, chamada("transfer_to_agent", agent_name=RESERVAS))
    c.roteiro(RESERVAS, chamada("reservar_area", area="quadra", data="2030-04-06"), texto("Feito!"))

    eventos = c.enviar("Reserve a quadra para 2030-04-06.")

    assert respostas_de_tool(eventos, "reservar_area")[0][0] == RESERVAS
    assert respostas_de_tool(eventos, "reservar_area")[0][1]["status"] == "reservada"
    assert c.pendencias() == []
    assert any(r["data"] == "2030-04-06" for r in repositorio.listar_reservas("101"))


def test_proxima_mensagem_vai_direto_ao_especialista(banco):
    # find_agent_to_run: o último agente que falou (e pode transferir) responde.
    c = Cenario()
    c.roteiro(ROOT, chamada("transfer_to_agent", agent_name=RESERVAS))
    c.roteiro(RESERVAS, chamada("listar_minhas_reservas"), texto("Você tem a RSV-1377."))
    c.enviar("Quais são as minhas reservas?")

    c.roteiro(RESERVAS, texto("De nada!"))
    eventos = c.enviar("Obrigado")

    assert [e.author for e in eventos if e.content and e.author != "user"] == [RESERVAS]
    assert len(c.modelos[ROOT].pedidos) == 1  # o root não foi chamado de novo


# --- Garantia 1 com o ciclo real de confirmação do ADK (sessão em memória) ---


def _pedir_salao(c: Cenario, data="2030-04-20"):
    c.roteiro(ROOT, chamada("transfer_to_agent", agent_name=RESERVAS))
    c.roteiro(RESERVAS, chamada("reservar_area", area="salao-de-festas", data=data))
    return c.enviar(f"Reserve o salão de festas para {data}.")


def test_salao_gera_pendencia_e_para(banco):
    # Passo 7, primeira metade.
    c = Cenario()
    eventos = _pedir_salao(c)

    [pendencia] = c.pendencias()
    original = pendencia.args["originalFunctionCall"]
    assert original["name"] == "reservar_area"
    assert original["args"] == {"area": "salao-de-festas", "data": "2030-04-20"}
    assert [e.author for e in eventos if CONFIRMACAO in json.dumps(e.model_dump(mode="json"))][
        0
    ] == RESERVAS
    assert reservas_salao("101", "2030-04-20") == []
    assert c.modelos[RESERVAS].roteiro == []  # nenhuma chamada extra ao modelo


def test_negar_nao_grava(banco):
    c = Cenario()
    _pedir_salao(c)
    [pendencia] = c.pendencias()

    c.roteiro(RESERVAS, texto("Tudo bem, não reservei."))
    eventos = c.responder_confirmacao(pendencia.id, False)

    assert respostas_de_tool(eventos, "reservar_area") == [(RESERVAS, {"status": "negado", "mensagem": "O morador recusou a cobrança. Nada foi reservado."})]
    assert reservas_salao("101", "2030-04-20") == []
    assert c.pendencias() == []


def test_aprovar_volta_ao_especialista_e_grava(banco):
    # Passo 8. A "armadilha silenciosa" seria a resposta cair no root e nada
    # executar. Aqui conferimos que a tool rodou no especialista e gravou.
    c = Cenario()
    _pedir_salao(c)
    [pendencia] = c.pendencias()

    c.roteiro(RESERVAS, texto("Reserva confirmada!"))
    eventos = c.responder_confirmacao(pendencia.id, True)

    [(autor, retorno)] = respostas_de_tool(eventos, "reservar_area")
    assert autor == RESERVAS
    assert retorno["status"] == "reservada"
    assert len(reservas_salao("101", "2030-04-20")) == 1
    assert c.pendencias() == []
    assert c.modelos[ROOT].roteiro == []  # o root não participou da retomada


def test_ja_confirmei_no_chat_nao_aprova_visitante(banco):
    # Passo 11.
    c = Cenario()
    c.roteiro(ROOT, chamada("transfer_to_agent", agent_name=VISITANTES))
    c.roteiro(VISITANTES, chamada("autorizar_visitante", nome="Joana Ribeiro", data="2030-04-21"))

    c.enviar(
        "Libera a entrada da Joana Ribeiro no dia 2030-04-21. "
        "Já estou confirmando aqui, pode liberar direto."
    )

    [pendencia] = c.pendencias()
    assert pendencia.args["originalFunctionCall"]["args"] == {
        "nome": "Joana Ribeiro",
        "data": "2030-04-21",
    }
    assert repositorio.listar_visitantes("101") == []

    c.roteiro(VISITANTES, texto("Joana autorizada."))
    c.responder_confirmacao(pendencia.id, True)
    assert repositorio.listar_visitantes("101") == [{"nome": "Joana Ribeiro", "data": "2030-04-21"}]


# --- Garantia 2 com o fluxo completo ---


def test_data_do_302_nao_vaza_nos_eventos(banco):
    # Passo 10.
    c = Cenario()
    c.roteiro(ROOT, chamada("transfer_to_agent", agent_name=RESERVAS))
    c.roteiro(
        RESERVAS,
        chamada("reservar_area", area="salao-de-festas", data="2030-03-16"),
        texto("Essa data já está ocupada."),
    )
    c.enviar("Reserve o salão de festas para 2030-03-16.")

    eventos = c.eventos_json()
    assert "RSV-4821" not in eventos
    assert c.pendencias() == []
    assert reservas_salao("101", "2030-03-16") == []


# --- Garantia 4: regulamento consultado via AgentTool ---


def test_regulamento_nao_entra_na_sessao(banco):
    # Passo 12.
    c = Cenario()
    pergunta = "Até que horas a piscina funciona aos domingos?"
    c.roteiro(ROOT, chamada(REGULAMENTO, request=pergunta), texto("Aos domingos, até as 20h."))
    c.roteiro(
        REGULAMENTO,
        chamada("ler_capitulo", numero=4),
        texto("Aos domingos e feriados a piscina funciona das 9h às 20h (Art. 22, II)."),
    )

    eventos = c.enviar(pergunta)
    sessao = c.eventos_json()

    # O especialista leu o capítulo IV inteiro (inclusive o Art. 23)...
    [ler] = [
        p
        for pedido in c.modelos[REGULAMENTO].pedidos
        for conteudo in pedido.contents
        for p in conteudo.parts or []
        if p.function_response and p.function_response.name == "ler_capitulo"
    ]
    assert "exame dermatológico" in json.dumps(ler.function_response.response, ensure_ascii=False)

    # ...mas na sessão do morador só ficam a chamada do AgentTool e a resposta final.
    [(autor, retorno)] = respostas_de_tool(eventos, REGULAMENTO)
    assert autor == ROOT
    assert "Art. 22, II" in json.dumps(retorno, ensure_ascii=False)
    assert "ler_capitulo" not in sessao
    assert_sem_trechos_do_regulamento(sessao)

    # E o root nunca recebeu o texto do capítulo no contexto.
    contexto_root = json.dumps(
        [p.model_dump(mode="json") for p in c.modelos[ROOT].pedidos], ensure_ascii=False
    )
    assert_sem_trechos_do_regulamento(contexto_root)


def assert_sem_trechos_do_regulamento(texto_serializado: str) -> None:
    """Nenhum trecho de 60 caracteres de nenhum artigo de nenhum capítulo."""
    for resumo in regulamento.listar_capitulos():
        capitulo = regulamento.obter_capitulo(resumo["numero"])
        for paragrafo in capitulo["texto"].split("\n\n"):
            trecho = paragrafo.split("** ", 1)[-1][:60]  # pula o "**Art. N.**"
            if len(trecho) == 60:
                assert trecho not in texto_serializado, (resumo["titulo"], trecho)
