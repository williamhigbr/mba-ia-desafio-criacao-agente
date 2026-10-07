"""Spike da Fase 5: confirmação com sessão persistida e reinício de processo.

Prova (ou derruba) a hipótese central do desafio: com o App resumível e a
sessão em SQLite, a resposta a uma confirmação enviada por OUTRO processo
volta ao especialista que pediu e a tool executa uma única vez.

Uso (sempre a partir da raiz do repositório):

    uv run python scripts/spike_confirmacao.py completo              # pedir + aprovar em 2 processos
    uv run python scripts/spike_confirmacao.py completo --negar
    uv run python scripts/spike_confirmacao.py completo --modelo real  # usa o Gemini do .env

    uv run python scripts/spike_confirmacao.py pedir                 # só o processo 1
    uv run python scripts/spike_confirmacao.py aprovar               # só o processo 2 (pode repetir: deve falhar)

Opções:
    --modelo falso|real      falso (padrão) roteiriza o LLM; real usa AURORA_MODELO.
    --servico sqlite|database
                             sqlite (padrão) = SqliteSessionService;
                             database = DatabaseSessionService("sqlite+aiosqlite://...")
                             (precisa de sqlalchemy: `uv run --with sqlalchemy ...`).

Tudo fica isolado em var/spike/: não toca em var/condominio.db nem nas sessões
da API.
"""

import argparse
import asyncio
import json
import subprocess
import sys
import warnings
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))  # permite importar tests.roteiro

warnings.filterwarnings("ignore", category=UserWarning)  # avisos [EXPERIMENTAL] do ADK

from google.adk.agents import _agent_router  # noqa: E402
from google.adk.runners import Runner  # noqa: E402
from google.genai import types  # noqa: E402

from aurora import config, repositorio, restaurar  # noqa: E402

PASTA = config.VAR_DIR / "spike"
ESTADO = PASTA / "estado.json"
CONFIRMACAO = "adk_request_confirmation"
USUARIO = "101"
AREA = "salao-de-festas"

# Isola os dados do spike antes de qualquer acesso ao banco.
config.CONDOMINIO_DB = PASTA / "condominio.db"
config.SESSOES_DB = PASTA / "sessoes.db"


# --- Montagem: modelo, serviço de sessão e Runner ---


def _modelo_para(modelo: str, fase: str, data: str):
    if modelo == "real":
        from aurora.agents import modelo_padrao

        return modelo_padrao

    from tests.roteiro import ModeloRoteirizado, chamada, texto

    # Roteiro de cada processo. O root fica SEM roteiro na fase de aprovação:
    # se a resposta da confirmação cair nele (a armadilha), o spike quebra alto.
    roteiros = {
        "pedir": {
            "assistente_aurora": [chamada("transfer_to_agent", agent_name="especialista_reservas")],
            "especialista_reservas": [chamada("reservar_area", area=AREA, data=data)],
        },
        "aprovar": {
            "especialista_reservas": [texto("Pronto, processei a sua resposta.")],
            # Só é chamado se a resposta cair no root (a armadilha). Ele "mente",
            # como um LLM de verdade faria, e só as verificações revelam a falha.
            "assistente_aurora": [texto("Sua reserva foi confirmada!")],
        },
    }[fase]

    def modelo_para(nome: str):
        return ModeloRoteirizado(model=f"roteiro-{nome}", roteiro=list(roteiros.get(nome, [])))

    return modelo_para


def _servico(servico: str):
    if servico == "database":
        from google.adk.sessions import DatabaseSessionService

        return DatabaseSessionService(f"sqlite+aiosqlite:///{PASTA / 'sessoes_database.db'}")
    from google.adk.sessions.sqlite_session_service import SqliteSessionService

    return SqliteSessionService(str(config.SESSOES_DB))


def _runner(args, fase: str) -> Runner:
    from aurora.agent import criar_app

    app = criar_app(_modelo_para(args.modelo, fase, args.data))
    # Variações para reproduzir a armadilha silenciosa (só no processo 2).
    if fase == "aprovar" and args.sem_resumir:
        app.resumability_config = None
    if fase == "aprovar" and args.bloquear_retorno:
        app.root_agent.find_agent("especialista_reservas").disallow_transfer_to_parent = True
    return Runner(app=app, session_service=_servico(args.servico))


def _espionar_roteamento() -> None:
    """Mostra qual agente o Runner escolhe (o ponto da armadilha silenciosa)."""
    original = _agent_router.find_agent_to_run

    def espiao(session, root_agent, resumability_config=None):
        agente = original(session, root_agent, resumability_config)
        resumivel = bool(resumability_config and resumability_config.is_resumable)
        print(f"  [find_agent_to_run] resumível={resumivel} -> {agente.name}")
        return agente

    _agent_router.find_agent_to_run = espiao


# --- Utilidades de inspeção ---


def _resumo(eventos) -> None:
    for ev in eventos:
        partes = []
        for p in ev.content.parts if ev.content else []:
            if p.text and not p.thought:
                partes.append(f"texto {p.text[:70]!r}")
            if p.function_call:
                partes.append(f"call {p.function_call.name}({json.dumps(p.function_call.args, ensure_ascii=False)[:90]})")
            if p.function_response:
                partes.append(
                    f"resp {p.function_response.name} -> "
                    f"{json.dumps(p.function_response.response, ensure_ascii=False)[:90]}"
                )
        if ev.actions.transfer_to_agent:
            partes.append(f"transfer -> {ev.actions.transfer_to_agent}")
        if partes:
            print(f"  {ev.author:<24} inv={ev.invocation_id[-8:]} | " + " | ".join(partes))


def _pendencias(eventos) -> list[dict]:
    pedidos, respondidos = {}, set()
    for ev in eventos:
        for fc in ev.get_function_calls():
            if fc.name == CONFIRMACAO:
                pedidos[fc.id] = {
                    "id": fc.id,
                    "invocation_id": ev.invocation_id,
                    "autor": ev.author,
                    "original": fc.args["originalFunctionCall"],
                }
        for fr in ev.get_function_responses():
            if fr.name == CONFIRMACAO:
                respondidos.add(fr.id)
    return [p for i, p in pedidos.items() if i not in respondidos]


def _reservas_salao(data: str) -> list[dict]:
    return [r for r in repositorio.listar_reservas(USUARIO) if r["area"] == AREA and r["data"] == data]


async def _eventos_da_sessao(runner: Runner, session_id: str):
    sessao = await runner.session_service.get_session(
        app_name=config.APP_NAME, user_id=USUARIO, session_id=session_id
    )
    if sessao is None:
        raise SystemExit(f"Sessão {session_id} não encontrada no banco: a persistência falhou.")
    return sessao.events


async def _rodar(runner: Runner, session_id: str, mensagem: types.Content) -> list:
    return [
        ev
        async for ev in runner.run_async(
            user_id=USUARIO, session_id=session_id, new_message=mensagem
        )
    ]


# --- Processo 1: pedir ---


async def pedir(args) -> int:
    PASTA.mkdir(parents=True, exist_ok=True)
    for arquivo in PASTA.glob("sessoes_database.db*"):
        arquivo.unlink()
    restaurar.restaurar()  # dados do spike limpos (apaga também var/spike/sessoes.db)
    ESTADO.unlink(missing_ok=True)

    runner = _runner(args, "pedir")
    sessao = await runner.session_service.create_session(
        app_name=config.APP_NAME, user_id=USUARIO, state={"apartamento": USUARIO}
    )
    texto_msg = f"Reserve o salão de festas para {args.data}."
    print(f"[processo 1] serviço={args.servico} modelo={args.modelo} sessão={sessao.id}")
    print(f"  morador: {texto_msg}")
    eventos = await _rodar(
        runner, sessao.id, types.Content(role="user", parts=[types.Part(text=texto_msg)])
    )
    _resumo(eventos)

    pendencias = _pendencias(await _eventos_da_sessao(runner, sessao.id))
    if len(pendencias) != 1:
        print(f"FALHA: esperava 1 confirmação pendente, achei {len(pendencias)}.")
        print("  Com --modelo real, o modelo pode ter perguntado algo em vez de chamar a tool; rode de novo.")
        return 1
    [pendencia] = pendencias
    if _reservas_salao(args.data):
        print("FALHA: a reserva foi gravada antes da confirmação.")
        return 1

    ESTADO.write_text(
        json.dumps({"session_id": sessao.id, "data": args.data, **pendencia}, ensure_ascii=False, indent=2)
    )
    print(f"  pendência: id={pendencia['id']} autor={pendencia['autor']} "
          f"inv={pendencia['invocation_id'][-8:]} acao={pendencia['original']['name']} "
          f"detalhes={pendencia['original'].get('args')}")
    print(f"OK: pendência criada, nada gravado. Estado salvo em {ESTADO.relative_to(RAIZ)}.")
    return 0


# --- Processo 2: responder (aprovar ou negar) ---


async def aprovar(args) -> int:
    if not ESTADO.exists():
        print("Rode `pedir` antes.")
        return 1
    estado = json.loads(ESTADO.read_text())
    confirmado = not args.negar
    data = estado["data"]

    _espionar_roteamento()
    runner = _runner(args, "aprovar")  # Runner e serviço novos: simula o reinício
    session_id = estado["session_id"]

    antes = await _eventos_da_sessao(runner, session_id)
    print(f"[processo 2] serviço={args.servico} sessão={session_id} eventos carregados={len(antes)}")
    if estado["id"] not in {p["id"] for p in _pendencias(antes)}:
        print("  (o id já não está pendente: a API responderia 409 sem chamar o Runner)")
        print("OK: reenvio detectado pelas pendências derivadas dos eventos." if _reservas_salao(data) or args.negar
              else "FALHA: id não pendente e nenhuma reserva gravada.")
        return 0

    resposta = types.FunctionResponse(
        id=estado["id"], name=CONFIRMACAO, response={"confirmed": confirmado}
    )
    print(f"  morador responde confirmação {estado['id']} com confirmed={confirmado}")
    # Sem invocation_id: o Runner o resolve pelo id do function call
    # (_resolve_invocation_id_from_fr). Passar o da pendência daria o mesmo.
    eventos = await _rodar(
        runner, session_id, types.Content(role="user", parts=[types.Part(function_response=resposta)])
    )
    _resumo(eventos)

    retornos = [
        (ev.author, fr.response)
        for ev in eventos
        for fr in ev.get_function_responses()
        if fr.name == "reservar_area"
    ]
    gravadas = _reservas_salao(data)
    pendentes = _pendencias(await _eventos_da_sessao(runner, session_id))

    print("Verificações:")
    ok = True

    def conferir(condicao: bool, descricao: str) -> None:
        nonlocal ok
        ok &= condicao
        print(f"  [{'OK' if condicao else 'FALHA'}] {descricao}")

    esperado = "reservada" if confirmado else "negado"
    conferir(len(retornos) == 1, f"reservar_area reexecutada uma vez (achei {len(retornos)})")
    if retornos:
        autor, retorno = retornos[0]
        conferir(autor == "especialista_reservas", f"reexecutada pelo especialista (autor={autor})")
        conferir(retorno.get("status") == esperado, f"status={retorno.get('status')!r}, esperado {esperado!r}")
    conferir(len(gravadas) == (1 if confirmado else 0), f"reservas do salão em {data} no banco: {len(gravadas)}")
    conferir(pendentes == [], "nenhuma confirmação pendente depois da resposta")
    if not ok and not retornos:
        print("  Armadilha silenciosa: a resposta foi aceita, mas a tool não rodou. "
              "Veja acima qual agente o find_agent_to_run escolheu.")
    return 0 if ok else 1


# --- Orquestração em dois processos ---


def completo(args) -> int:
    base = [sys.executable, str(Path(__file__).resolve()), "--modelo", args.modelo,
            "--servico", args.servico, "--data", args.data]
    if subprocess.run([*base, "pedir"], cwd=RAIZ).returncode != 0:
        return 1
    print("\n--- processo 1 encerrado (reinício simulado) ---\n", flush=True)
    resposta = [*base, "aprovar"] + (["--negar"] if args.negar else [])
    resposta += ["--sem-resumir"] if args.sem_resumir else []
    resposta += ["--bloquear-retorno"] if args.bloquear_retorno else []
    if subprocess.run(resposta, cwd=RAIZ).returncode != 0:
        return 1
    print("\n--- reenvio da mesma resposta, em um terceiro processo ---\n", flush=True)
    return subprocess.run(resposta, cwd=RAIZ).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("acao", choices=["pedir", "aprovar", "completo"])
    parser.add_argument("--modelo", choices=["falso", "real"], default="falso")
    parser.add_argument("--servico", choices=["sqlite", "database"], default="sqlite")
    parser.add_argument("--data", default="2030-04-20")
    parser.add_argument("--negar", action="store_true", help="responde confirmed=false")
    parser.add_argument(
        "--sem-resumir", action="store_true",
        help="processo 2 com App NÃO resumível (reproduz a armadilha?)",
    )
    parser.add_argument(
        "--bloquear-retorno", action="store_true",
        help="processo 2 com disallow_transfer_to_parent=True no especialista",
    )
    args = parser.parse_args()

    if args.acao == "completo":
        return completo(args)
    return asyncio.run(pedir(args) if args.acao == "pedir" else aprovar(args))


if __name__ == "__main__":
    raise SystemExit(main())
