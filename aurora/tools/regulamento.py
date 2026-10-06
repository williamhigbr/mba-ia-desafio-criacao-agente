"""Tools do especialista de regulamento.

Rodam dentro do AgentTool (sessão isolada em memória), então o texto dos
capítulos lidos não entra nos eventos da sessão do morador (Garantia 4).
"""

from aurora import regulamento


async def listar_capitulos() -> dict:
    """Lista os capítulos do regulamento interno (número e título)."""
    return {"status": "ok", "capitulos": regulamento.listar_capitulos()}


async def ler_capitulo(numero: int) -> dict:
    """Lê o texto completo de UM capítulo do regulamento interno.

    Leia só o capítulo do assunto perguntado.

    Args:
        numero: número do capítulo (1 a 14), conforme listar_capitulos.
    """
    capitulo = regulamento.obter_capitulo(numero)
    if capitulo is None:
        return {"status": "erro", "mensagem": "Capítulo inexistente. Use listar_capitulos."}
    return {"status": "ok", **capitulo}


TOOLS = [listar_capitulos, ler_capitulo]
