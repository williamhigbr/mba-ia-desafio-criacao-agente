"""Ponto de entrada do ADK: ``root_agent`` e ``app``.

``uv run adk web aurora`` encontra este módulo e usa o ``app``.
"""

from google.adk.agents import LlmAgent
from google.adk.apps import App, ResumabilityConfig

from aurora import config
from aurora.agents import ModeloPara, modelo_padrao
from aurora.agents.root import criar_root_agent


def criar_app(modelo_para: ModeloPara = modelo_padrao) -> App:
    return App(
        name=config.APP_NAME,
        root_agent=criar_root_agent(modelo_para),
        # Resumível: a resposta de uma confirmação volta para o agente que fez
        # o pedido (find_agent_to_run), e não para o root, que não tem a tool.
        resumability_config=ResumabilityConfig(is_resumable=True),
    )


app = criar_app()
root_agent: LlmAgent = app.root_agent
