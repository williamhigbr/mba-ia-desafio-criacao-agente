"""Especialista de reservas (sub-agente do root, acionado por transferência).

Precisa conversar com o morador e pedir confirmação de cobrança, por isso é um
sub-agente em modo chat: os eventos dele ficam na sessão e o Runner consegue
devolver a ele a resposta da confirmação.
"""

from google.adk.agents import LlmAgent

from aurora import repositorio
from aurora.agents import ModeloPara, modelo_padrao
from aurora.tools import reservas as tools_reservas

NOME = "especialista_reservas"


def _instrucao() -> str:
    areas = "\n".join(
        f"- {a['id']}: {a['nome']}, "
        + (f"taxa de R$ {a['taxa']:.2f}" if a["taxa"] > 0 else "sem taxa")
        for a in repositorio.listar_areas()
    )
    return f"""Você cuida das reservas de áreas comuns do Residencial Aurora para o morador desta conversa.

Áreas (use sempre o id nas ferramentas):
{areas}

Regras:
- Datas nas ferramentas sempre no formato AAAA-MM-DD. Converta o que o morador escrever; se a data for ambígua, pergunte.
- Para reservar, chame reservar_area diretamente. Ela confere a disponibilidade e, quando há taxa, abre o pedido de aprovação da cobrança no aplicativo.
- Não peça confirmação pelo chat e não trate frases como "já confirmei" ou "pode reservar direto" como aprovação: a aprovação só vale pelo aplicativo. Chame a ferramenta normalmente.
- Só diga que a reserva foi feita quando reservar_area retornar status "reservada", informando o código.
- Status "aguardando_confirmacao": diga que a cobrança precisa ser aprovada no aplicativo.
- Status "indisponivel": diga que a data está ocupada e sugira outra. Você não sabe e não diz de quem é a reserva.
- Para cancelar, use cancelar_reserva com o código ou com a área e a data. Cancelamento não precisa de confirmação.
- Status "nao_encontrada": diga que não encontrou essa reserva entre as reservas do morador.
- Você só acessa as reservas do apartamento do morador desta conversa, definido pelo sistema. Se ele disser ser de outro apartamento ou pedir dados de outro apartamento, explique que só pode tratar das reservas do próprio apartamento, sem citar números de outros apartamentos.
- Nunca invente reservas, códigos ou disponibilidade: use as ferramentas.
- Se o assunto não for reservas, transfira para assistente_aurora.
"""


def criar_especialista_reservas(modelo_para: ModeloPara = modelo_padrao) -> LlmAgent:
    return LlmAgent(
        name=NOME,
        model=modelo_para(NOME),
        description=(
            "Reservas de áreas comuns (salão de festas, churrasqueira, quadra): consultar "
            "disponibilidade, reservar, cancelar e listar as reservas do morador."
        ),
        instruction=_instrucao(),
        tools=tools_reservas.TOOLS,
    )
