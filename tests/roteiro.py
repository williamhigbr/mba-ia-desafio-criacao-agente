"""Modelo falso para exercitar o ADK sem chamar o Gemini.

Usado pelos testes (tests/test_agentes.py) e pelo spike de confirmação
(scripts/spike_confirmacao.py --modelo falso).
"""

from google.adk.models import BaseLlm, LlmResponse
from google.adk.models._capabilities import LlmCapabilities
from google.genai import types
from pydantic import Field


def chamada(_funcao: str, **args) -> types.Part:
    """Resposta do modelo pedindo um function call."""
    return types.Part(function_call=types.FunctionCall(name=_funcao, args=args))


def texto(conteudo: str) -> types.Part:
    """Resposta do modelo em texto."""
    return types.Part(text=conteudo)


class ModeloRoteirizado(BaseLlm):
    """Devolve as respostas do roteiro em ordem e guarda os pedidos recebidos.

    Ser chamado além do roteiro é erro: assim um teste percebe quando um agente
    que não deveria participar (por exemplo, o root numa retomada) é acionado.
    """

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
