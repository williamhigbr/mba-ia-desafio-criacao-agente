"""De onde as tools tiram o apartamento (Garantia 2)."""

from google.adk.tools import ToolContext


def apartamento_da_sessao(tool_context: ToolContext) -> str:
    """Apartamento do morador autenticado.

    Vem do ``user_id`` da sessão, definido uma única vez em ``POST /sessoes``
    (user_id = apartamento). O modelo não tem como alterá-lo: não é parâmetro
    de nenhuma tool e não fica no state, que tools e agentes podem escrever.
    """
    apartamento = tool_context.user_id
    if not apartamento:
        # Erro de programação (sessão criada sem apartamento), nunca do morador.
        raise RuntimeError("Sessão sem apartamento associado.")
    return apartamento
