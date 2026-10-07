"""Verificação das Fases 7 e 8 contra a API real (Gemini de verdade).

Pré-requisito: a API no ar, com dados restaurados.

    uv run aurora-restaurar && uv run aurora-api      # terminal 1

Fase 7, regulamento consultado e não carregado (passo 12 do avaliador):

    uv run python scripts/verificar_garantias.py regulamento
    uv run python scripts/verificar_garantias.py regulamento \\
        --pergunta "Posso ter cachorro no apartamento?" --capitulo 8 --esperado ""

Fase 8, duas aprovações simultâneas para a mesma área e data (passo 14):

    uv run python scripts/verificar_garantias.py disputa --rodadas 3

Opções comuns: --url (padrão http://localhost:8000).
Sai com código 1 se alguma verificação falhar.
"""

import argparse
import asyncio
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx

RAIZ = Path(__file__).resolve().parent.parent
REGULAMENTO = RAIZ / "dados" / "regulamento.md"
TAMANHO_TRECHO = 60
CABECALHO = re.compile(r"^## Capítulo ([IVXLC]+): (.+)$", re.MULTILINE)


# --- Relatório ---


class Relatorio:
    def __init__(self):
        self.falhas = 0

    def conferir(self, condicao: bool, descricao: str) -> bool:
        print(f"  [{'OK' if condicao else 'FALHA'}] {descricao}")
        self.falhas += not condicao
        return condicao

    def info(self, descricao: str) -> None:
        print(f"  [info] {descricao}")


# --- Cliente da API ---


class Api:
    def __init__(self, cliente: httpx.AsyncClient):
        self.c = cliente

    async def criar_sessao(self, apartamento: str) -> str:
        r = await self.c.post("/sessoes", json={"apartamento": apartamento})
        r.raise_for_status()
        return r.json()["session_id"]

    async def mensagem(self, sid: str, texto: str) -> dict:
        r = await self.c.post(f"/sessoes/{sid}/mensagens", json={"texto": texto})
        if r.status_code != 200:
            raise RuntimeError(f"POST mensagens -> {r.status_code}: {r.text[:300]}")
        return r.json()

    async def confirmar(self, sid: str, id_: str, confirmado: bool = True) -> httpx.Response:
        return await self.c.post(
            f"/sessoes/{sid}/confirmacoes", json={"id": id_, "confirmado": confirmado}
        )

    async def eventos(self, sid: str) -> list[dict]:
        r = await self.c.get(f"/sessoes/{sid}/eventos")
        r.raise_for_status()
        return r.json()

    async def reservas(self, apartamento: str) -> list[dict]:
        r = await self.c.get(f"/apartamentos/{apartamento}/reservas")
        r.raise_for_status()
        return r.json()


def _chamadas(eventos: list[dict]) -> list[str]:
    return [
        parte["function_call"]["name"]
        for e in eventos
        for parte in (e.get("content") or {}).get("parts", [])
        if parte.get("function_call")
    ]


# --- Fase 7: regulamento ---


def _capitulos() -> list[tuple[int, str, list[str]]]:
    """(número, título, trechos de 60 caracteres de cada parágrafo) por capítulo."""
    texto = REGULAMENTO.read_text(encoding="utf-8")
    cabecalhos = list(CABECALHO.finditer(texto))
    capitulos = []
    for numero, cab in enumerate(cabecalhos, start=1):
        fim = cabecalhos[numero].start() if numero < len(cabecalhos) else len(texto)
        trechos = []
        for paragrafo in texto[cab.end() : fim].strip().split("\n\n"):
            trecho = paragrafo.split("** ", 1)[-1][:TAMANHO_TRECHO]  # pula o "**Art. N.**"
            if len(trecho) == TAMANHO_TRECHO:
                trechos.append(trecho)
        capitulos.append((numero, cab.group(2).strip(), trechos))
    return capitulos


async def regulamento(api: Api, args, rel: Relatorio) -> None:
    print(f"Fase 7 | pergunta: {args.pergunta!r}")
    sid = await api.criar_sessao("101")
    corpo = await api.mensagem(sid, args.pergunta)
    resposta = corpo["resposta"]
    print(f"  resposta: {resposta!r}")

    if args.esperado:
        rel.conferir(
            re.search(args.esperado, resposta, re.IGNORECASE) is not None,
            f"resposta contém /{args.esperado}/",
        )
    rel.conferir(corpo["confirmacoes_pendentes"] == [], "nenhuma confirmação pendente")

    eventos = await api.eventos(sid)
    serializado = json.dumps(eventos, ensure_ascii=False)
    chamadas = _chamadas(eventos)
    rel.info(f"{len(eventos)} eventos, {len(serializado)} caracteres; chamadas: {chamadas}")
    rel.conferir("especialista_regulamento" in chamadas, "o root chamou o especialista_regulamento")
    rel.conferir(
        not {"ler_capitulo", "listar_capitulos"} & set(chamadas),
        "as tools de leitura do regulamento não aparecem na sessão (rodaram no AgentTool)",
    )

    for numero, titulo, trechos in _capitulos():
        vazados = [t for t in trechos if t in serializado]
        if not vazados:
            continue
        if numero == args.capitulo:
            rel.info(f"{len(vazados)} trecho(s) do Capítulo {numero} ({titulo}), o do assunto: permitido")
        else:
            rel.conferir(False, f"trecho do Capítulo {numero} ({titulo}) nos eventos: {vazados[0]!r}")
    outros = [n for n, _, t in _capitulos() if n != args.capitulo and any(x in serializado for x in t)]
    if not outros:
        rel.conferir(True, "nenhum trecho de capítulos de outros assuntos nos eventos")


# --- Fase 8: disputa ---


async def _pedir_salao(api: Api, sid: str, data: str, rel: Relatorio) -> dict | None:
    """Pede o salão; responde perguntas do assistente até 2 vezes, como o avaliador."""
    textos = [
        f"Reserve o salão de festas para {data}.",
        f"Sim, quero reservar o salão de festas (salao-de-festas) para {data}.",
        "Sim, pode seguir com a reserva.",
    ]
    for texto in textos:
        corpo = await api.mensagem(sid, texto)
        pendentes = [p for p in corpo["confirmacoes_pendentes"] if p["detalhes"].get("data") == data]
        if pendentes:
            return pendentes[0]
        rel.info(f"sem pendência ainda; o assistente disse: {corpo['resposta'][:120]!r}")
    return None


async def disputa(api: Api, args, rel: Relatorio) -> None:
    inicio = date.fromisoformat(args.data_inicial)
    for rodada in range(args.rodadas):
        data = (inicio + timedelta(days=rodada)).isoformat()
        print(f"Fase 8 | rodada {rodada + 1}/{args.rodadas} | salão em {data}")

        s3, s4 = await api.criar_sessao("101"), await api.criar_sessao("201")
        c3 = await _pedir_salao(api, s3, data, rel)
        c4 = await _pedir_salao(api, s4, data, rel)
        if not rel.conferir(c3 is not None and c4 is not None, "as duas sessões ficaram com confirmação pendente"):
            rel.info("se a data já estava ocupada, restaure os dados com a API parada ou use --data-inicial")
            continue
        antes = [r for ap in ("101", "201") for r in await api.reservas(ap) if r["area"] == "salao-de-festas" and r["data"] == data]
        rel.conferir(antes == [], "nada gravado antes das aprovações")

        r3, r4 = await asyncio.gather(api.confirmar(s3, c3["id"]), api.confirmar(s4, c4["id"]))
        rel.conferir(
            (r3.status_code, r4.status_code) == (200, 200),
            f"as duas aprovações responderam 200 (101={r3.status_code}, 201={r4.status_code})",
        )
        for apartamento, r in (("101", r3), ("201", r4)):
            if r.status_code != 200:
                rel.info(f"corpo da resposta do {apartamento}: {r.text[:300]}")

        depois = {
            ap: [r for r in await api.reservas(ap) if r["area"] == "salao-de-festas" and r["data"] == data]
            for ap in ("101", "201")
        }
        total = sum(len(v) for v in depois.values())
        vencedor = next((ap for ap, v in depois.items() if v), None)
        rel.conferir(total == 1, f"exatamente uma reserva somando 101 e 201 (achei {total}; vencedor: {vencedor})")
        if vencedor and r3.status_code == r4.status_code == 200:
            perdedor = "201" if vencedor == "101" else "101"
            texto = (r4 if perdedor == "201" else r3).json()["resposta"]
            rel.info(f"resposta ao perdedor ({perdedor}): {texto[:160]!r}")


# --- Entrada ---


async def principal(args) -> int:
    rel = Relatorio()
    async with httpx.AsyncClient(base_url=args.url, timeout=180) as cliente:
        try:
            await cliente.get("/apartamentos/101/reservas")
        except httpx.ConnectError:
            print(f"API fora do ar em {args.url}. Suba com `uv run aurora-api`.")
            return 1
        api = Api(cliente)
        await (regulamento if args.acao == "regulamento" else disputa)(api, args, rel)
    print("Resultado:", "tudo OK" if rel.falhas == 0 else f"{rel.falhas} falha(s)")
    return 0 if rel.falhas == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("acao", choices=["regulamento", "disputa"])
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--pergunta", default="Até que horas a piscina funciona aos domingos?")
    parser.add_argument("--capitulo", type=int, default=4, help="capítulo do assunto (trechos dele são permitidos)")
    parser.add_argument("--esperado", default=r"20\s*h|20:00", help="regex esperada na resposta ('' desliga)")
    parser.add_argument("--rodadas", type=int, default=1)
    parser.add_argument("--data-inicial", default="2030-05-11", help="data da 1ª rodada; as seguintes somam 1 dia")
    args = parser.parse_args()
    return asyncio.run(principal(args))


if __name__ == "__main__":
    sys.exit(main())
