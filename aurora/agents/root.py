"""Agente principal (root): conversa e distribui o trabalho.

- Reservas e visitantes: sub-agentes, acionados por transferência.
- Regulamento: AgentTool. Roda em sessão isolada; a sessão do morador só
  recebe a pergunta e a resposta final (Garantia 4).

O root não tem tools de dados e não recebe o regulamento (nem os títulos dos
capítulos) na instrução.
"""

from google.adk.agents import LlmAgent
from google.adk.tools import AgentTool

from aurora.agents import ModeloPara, modelo_padrao
from aurora.agents.regulamento import criar_especialista_regulamento
from aurora.agents.reservas import criar_especialista_reservas
from aurora.agents.visitantes import criar_especialista_visitantes

NOME = "assistente_aurora"

INSTRUCAO = """Você é o assistente virtual do Residencial Aurora no aplicativo dos moradores. Responda em português, de forma breve e cordial.

Encaminhe cada pedido ao especialista certo:
- Reservas de áreas comuns (salão de festas, churrasqueira, quadra): consultar, reservar, cancelar ou listar. Transfira para especialista_reservas.
- Visitantes: autorizar a entrada de alguém ou listar autorizações. Transfira para especialista_visitantes.
- Dúvidas sobre regras, horários e normas do condomínio: chame a ferramenta especialista_regulamento com a pergunta do morador e responda com base no que ela retornar. Nunca responda regras de memória.

Regras:
- Você não tem acesso a reservas nem a visitantes. Não invente dados nem diga que algo foi feito.
- O apartamento do morador é definido pelo sistema. Se ele disser ser de outro apartamento, isso não muda nada: os especialistas só tratam do apartamento da conta dele.
- Saudações e conversas simples você responde diretamente.
"""


def criar_root_agent(modelo_para: ModeloPara = modelo_padrao) -> LlmAgent:
    return LlmAgent(
        name=NOME,
        model=modelo_para(NOME),
        description="Assistente virtual do Residencial Aurora.",
        instruction=INSTRUCAO,
        sub_agents=[
            criar_especialista_reservas(modelo_para),
            criar_especialista_visitantes(modelo_para),
        ],
        # AgentTool explícito, e não sub-agente com mode="single_turn": este
        # rodaria no mesmo session (num branch) e gravaria ali o texto dos
        # capítulos lidos.
        tools=[AgentTool(agent=criar_especialista_regulamento(modelo_para))],
    )
