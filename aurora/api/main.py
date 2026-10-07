"""Rotas da API do Residencial Aurora.

Subir: ``uv run aurora-api`` (http://localhost:8000).

Sem autenticação, por definição do desafio: o apartamento enviado em
``POST /sessoes`` representa o morador autenticado, e as rotas de verificação
(``/apartamentos/...``) ficariam atrás de acesso administrativo em produção.
"""

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from google.adk.apps import App
from google.adk.runners import Runner
from google.adk.sessions import Session
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.genai import errors as genai_errors
from pydantic import BaseModel, Field

from aurora import config, db, repositorio
from aurora.api import execucao
from aurora.api.confirmacoes import confirmacoes_pendentes

logger = logging.getLogger("aurora.api")


# --- Corpos das requisições ---


class NovaSessao(BaseModel):
    apartamento: str = Field(min_length=1, max_length=10)


class NovaMensagem(BaseModel):
    texto: str = Field(min_length=1, max_length=4000)


class RespostaConfirmacao(BaseModel):
    id: str
    confirmado: bool


# --- Aplicação ---


def _app_adk_padrao() -> App:
    from aurora.agent import app as app_adk

    return app_adk


def criar_api(criar_app_adk: Callable[[], App] = _app_adk_padrao) -> FastAPI:
    """Monta a API. ``criar_app_adk`` permite aos testes injetar um modelo falso."""

    @asynccontextmanager
    async def lifespan(api: FastAPI):
        await asyncio.to_thread(db.garantir_schema)
        config.SESSOES_DB.parent.mkdir(parents=True, exist_ok=True)
        # Garantia 3: sessões e eventos em SQLite, relidos a cada requisição.
        servico = SqliteSessionService(str(config.SESSOES_DB))
        api.state.runner = Runner(app=criar_app_adk(), session_service=servico)
        # Uma execução por vez em cada sessão: duas requisições na mesma
        # sessão não intercalam eventos nem respondem a mesma confirmação.
        api.state.travas = defaultdict(asyncio.Lock)
        yield
        await api.state.runner.close()

    api = FastAPI(title="Assistente do Residencial Aurora", lifespan=lifespan)

    @api.exception_handler(genai_errors.APIError)
    async def _erro_do_modelo(request: Request, erro: genai_errors.APIError):
        logger.warning("Falha ao chamar o modelo: %s", erro)
        return JSONResponse(
            status_code=503,
            content={"detail": "O modelo de linguagem está indisponível no momento. Tente de novo."},
        )

    async def _sessao(request: Request, session_id: str) -> Session:
        apartamento = await asyncio.to_thread(repositorio.apartamento_da_sessao, session_id)
        if apartamento is None:
            raise HTTPException(status_code=404, detail="Sessão não encontrada.")
        runner: Runner = request.app.state.runner
        sessao = await runner.session_service.get_session(
            app_name=config.APP_NAME, user_id=apartamento, session_id=session_id
        )
        if sessao is None:
            raise HTTPException(status_code=404, detail="Sessão não encontrada.")
        return sessao

    # --- Conversa ---

    @api.post("/sessoes", status_code=201)
    async def criar_sessao(corpo: NovaSessao, request: Request) -> dict:
        runner: Runner = request.app.state.runner
        # Garantia 2: o apartamento é fixado aqui, uma única vez, como user_id
        # da sessão. É a fonte única: as tools leem só o user_id
        # (aurora/tools/_contexto.py). De propósito, ele NÃO é copiado para o
        # state, que tools, output_key e AgentTool podem alterar.
        sessao = await runner.session_service.create_session(
            app_name=config.APP_NAME,
            user_id=corpo.apartamento,
        )
        await asyncio.to_thread(repositorio.registrar_sessao, sessao.id, corpo.apartamento)
        return {"session_id": sessao.id}

    @api.post("/sessoes/{session_id}/mensagens")
    async def enviar_mensagem(session_id: str, corpo: NovaMensagem, request: Request) -> dict:
        async with request.app.state.travas[session_id]:
            sessao = await _sessao(request, session_id)
            return await execucao.enviar_mensagem(request.app.state.runner, sessao, corpo.texto)

    @api.post("/sessoes/{session_id}/confirmacoes")
    async def responder_confirmacao(
        session_id: str, corpo: RespostaConfirmacao, request: Request
    ) -> dict:
        async with request.app.state.travas[session_id]:
            sessao = await _sessao(request, session_id)
            # Garantia 1: só um id pendente NESTA sessão é aceito, e isso é
            # decidido aqui, antes do Runner. Id inexistente, de outra sessão
            # ou já respondido -> 409, e nada é executado.
            pendentes = {p.id: p for p in confirmacoes_pendentes(sessao.events)}
            pendencia = pendentes.get(corpo.id)
            if pendencia is None:
                raise HTTPException(
                    status_code=409,
                    detail="Não existe confirmação pendente com esse id nesta sessão.",
                )
            return await execucao.responder_confirmacao(
                request.app.state.runner, sessao, pendencia, corpo.confirmado
            )

    @api.get("/sessoes/{session_id}/eventos")
    async def listar_eventos(session_id: str, request: Request) -> list[dict]:
        # Somente leitura: não grava nada na sessão.
        sessao = await _sessao(request, session_id)
        return [e.model_dump(mode="json", exclude_none=True) for e in sessao.events]

    # --- Verificação (leem o banco direto, sem passar pelo modelo) ---

    @api.get("/apartamentos/{apartamento}/reservas")
    async def reservas_do_apartamento(apartamento: str) -> list[dict]:
        return await asyncio.to_thread(repositorio.listar_reservas, apartamento)

    @api.get("/apartamentos/{apartamento}/visitantes")
    async def visitantes_do_apartamento(apartamento: str) -> list[dict]:
        return await asyncio.to_thread(repositorio.listar_visitantes, apartamento)

    return api


app = criar_api()


def _chave_configurada() -> bool:
    import os

    vertex = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").strip().lower() in ("1", "true", "yes")
    return vertex or bool(os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))


def main() -> None:
    import sys

    import uvicorn

    # Sem chave, toda mensagem falharia dentro do cliente do Gemini. Melhor
    # avisar na subida do que responder 500 na primeira conversa.
    if not _chave_configurada():
        sys.exit("GOOGLE_API_KEY não definida. Copie .env.example para .env e preencha a chave.")

    uvicorn.run("aurora.api.main:app", host="127.0.0.1", port=8000)
