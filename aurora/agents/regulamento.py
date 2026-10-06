"""Especialista de regulamento.

Acionado pelo root como ``AgentTool`` (ver ``aurora/agents/root.py``): roda
numa sessão em memória separada, então o texto dos capítulos que ele lê não
entra nos eventos da sessão do morador (Garantia 4). Só os TÍTULOS dos
capítulos estão na instrução; o texto é lido sob demanda, um capítulo por vez.
"""

from google.adk.agents import LlmAgent

from aurora import regulamento
from aurora.agents import ModeloPara, modelo_padrao
from aurora.tools import regulamento as tools_regulamento

NOME = "especialista_regulamento"


def _instrucao() -> str:
    titulos = "\n".join(
        f"- Capítulo {c['romano']} (numero={c['numero']}): {c['titulo']}"
        for c in regulamento.listar_capitulos()
    )
    return f"""Você responde dúvidas sobre o Regulamento Interno do Residencial Aurora.

Capítulos disponíveis:
{titulos}

Como trabalhar:
1. Identifique o capítulo do assunto da pergunta e leia-o com ler_capitulo. Leia só o necessário, normalmente um único capítulo.
2. Responda de forma objetiva, em até três frases, citando o artigo (ex.: "Art. 22, II").
3. Não transcreva o capítulo nem trechos que não respondem à pergunta.
4. Se o regulamento não tratar do assunto, diga isso. Nunca responda de memória.
"""


def criar_especialista_regulamento(modelo_para: ModeloPara = modelo_padrao) -> LlmAgent:
    return LlmAgent(
        name=NOME,
        model=modelo_para(NOME),
        description=(
            "Responde dúvidas sobre o regulamento interno do condomínio: horários das "
            "áreas comuns, regras de convivência, silêncio, animais, mudanças, obras, "
            "garagem, lixo, portaria, infrações e penalidades. Envie a pergunta do morador."
        ),
        instruction=_instrucao(),
        tools=tools_regulamento.TOOLS,
    )
