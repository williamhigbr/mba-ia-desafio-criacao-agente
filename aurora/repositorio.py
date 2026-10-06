"""Acesso aos dados do condomínio, sem nenhuma dependência do ADK.

Toda função que lê ou grava dados de um apartamento recebe ``apartamento`` de
quem chama. As tools vão passar o apartamento da sessão, nunca um valor
escolhido pelo modelo. Nenhum retorno deste módulo expõe o dono de uma
reserva de outro apartamento.
"""

import json
import re
import sqlite3
import uuid
from datetime import date
from functools import cache

from aurora import config, db

_FORMATO_DATA = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TAMANHO_MAX_NOME = 100
_TENTATIVAS_CODIGO = 5


# --- Exceções de domínio (viram respostas normais, nunca erro de servidor) ---


class ErroDominio(Exception):
    """Base das falhas de regra de negócio."""


class DadoInvalido(ErroDominio, ValueError):
    pass


class AreaInexistente(ErroDominio):
    pass


class DataOcupada(ErroDominio):
    """A área já tem reserva ativa na data. Não carrega o dono de propósito."""


class ChaveIdempotenciaReutilizada(ErroDominio):
    """A mesma chave foi usada para uma operação diferente."""


# --- Dados estáticos (dados/*.json) ---


@cache
def _carregar_json(nome: str) -> list[dict]:
    with open(config.DADOS_DIR / nome, encoding="utf-8") as arquivo:
        return json.load(arquivo)


def listar_areas() -> list[dict]:
    return [dict(a) for a in _carregar_json("areas.json")]


def obter_area(area: str) -> dict | None:
    for item in _carregar_json("areas.json"):
        if item["id"] == area:
            return dict(item)
    return None


def area_existe(area: str) -> bool:
    return obter_area(area) is not None


def apartamento_existe(numero: str) -> bool:
    return any(a["numero"] == numero for a in _carregar_json("apartamentos.json"))


# --- Validações ---


def validar_data(data: str) -> str:
    """Aceita só AAAA-MM-DD com uma data de calendário válida."""
    if not isinstance(data, str) or not _FORMATO_DATA.match(data):
        raise DadoInvalido("Data deve estar no formato AAAA-MM-DD.")
    try:
        date.fromisoformat(data)
    except ValueError:
        raise DadoInvalido("Data inexistente no calendário.") from None
    return data


def _validar_area(area: str) -> dict:
    info = obter_area(area)
    if info is None:
        raise AreaInexistente(area)
    return info


def _validar_nome(nome: str) -> str:
    nome = " ".join(nome.split()) if isinstance(nome, str) else ""
    if not nome or len(nome) > _TAMANHO_MAX_NOME:
        raise DadoInvalido("Nome do visitante inválido.")
    return nome


# --- Reservas ---


def listar_reservas(apartamento: str) -> list[dict]:
    """Reservas ativas do apartamento, no formato das rotas de verificação."""
    with db.conectar() as conn:
        linhas = conn.execute(
            "SELECT codigo, area, data FROM reservas"
            " WHERE apartamento = ? AND status = 'ativa' ORDER BY data, area",
            (apartamento,),
        ).fetchall()
    return [dict(linha) for linha in linhas]


def data_livre(area: str, data: str) -> bool:
    """Só diz se a data está livre. Nunca revela de quem é a reserva."""
    _validar_area(area)
    validar_data(data)
    with db.conectar() as conn:
        ocupada = conn.execute(
            "SELECT 1 FROM reservas WHERE area = ? AND data = ? AND status = 'ativa'",
            (area, data),
        ).fetchone()
    return ocupada is None


def _novo_codigo() -> str:
    return f"RSV-{uuid.uuid4().hex[:8].upper()}"


def criar_reserva(apartamento: str, area: str, data: str, chave: str | None = None) -> str:
    """Grava uma reserva ativa e devolve o código gerado.

    A exclusividade (regra 1 / Garantia 5) é decidida pelo índice único
    ``ux_reserva_ativa`` no momento do INSERT, não por uma consulta anterior.

    ``chave`` torna a operação idempotente: repetir a chamada com a mesma chave
    devolve o mesmo código sem gravar de novo (a retomada do ADK é at-least-once).
    """
    _validar_area(area)
    validar_data(data)

    with db.transacao() as conn:
        if chave is not None:
            existente = conn.execute(
                "SELECT codigo, apartamento, area, data FROM reservas WHERE chave_idempotencia = ?",
                (chave,),
            ).fetchone()
            if existente is not None:
                if (existente["apartamento"], existente["area"], existente["data"]) != (
                    apartamento,
                    area,
                    data,
                ):
                    raise ChaveIdempotenciaReutilizada(chave)
                return existente["codigo"]

        for _ in range(_TENTATIVAS_CODIGO):
            codigo = _novo_codigo()
            try:
                conn.execute(
                    "INSERT INTO reservas (codigo, apartamento, area, data, chave_idempotencia)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (codigo, apartamento, area, data, chave),
                )
                return codigo
            except sqlite3.IntegrityError:
                # O banco recusou. Só classificamos o motivo (estamos dentro da
                # mesma transação IMMEDIATE, então a leitura é consistente).
                ocupada = conn.execute(
                    "SELECT 1 FROM reservas WHERE area = ? AND data = ? AND status = 'ativa'",
                    (area, data),
                ).fetchone()
                if ocupada is not None:
                    raise DataOcupada() from None
                codigo_repetido = conn.execute(
                    "SELECT 1 FROM reservas WHERE codigo = ?", (codigo,)
                ).fetchone()
                if codigo_repetido is None:
                    raise
                # Código já usado (inclusive por reserva cancelada): gera outro.
        raise RuntimeError("Não foi possível gerar um código de reserva único.")


def cancelar_reserva(
    apartamento: str,
    *,
    codigo: str | None = None,
    area: str | None = None,
    data: str | None = None,
) -> dict | None:
    """Cancela uma reserva ativa DO PRÓPRIO apartamento.

    Identifica a reserva pelo código ou por área + data. Devolve a reserva
    cancelada ou ``None`` se ela não existe entre as do apartamento; não há
    como distinguir "não existe" de "é de outro apartamento".
    Cancelamento é lógico: o código continua gravado e nunca é reutilizado.
    """
    if codigo:
        filtro, params = "codigo = ?", (codigo,)
    elif area and data:
        validar_data(data)
        filtro, params = "area = ? AND data = ?", (area, data)
    else:
        raise DadoInvalido("Informe o código ou a área e a data da reserva.")

    with db.transacao() as conn:
        linhas = conn.execute(
            "UPDATE reservas SET status = 'cancelada'"
            f" WHERE apartamento = ? AND status = 'ativa' AND {filtro}"
            " RETURNING codigo, area, data",
            (apartamento, *params),
        ).fetchall()
    return dict(linhas[0]) if linhas else None


# --- Visitantes ---


def listar_visitantes(apartamento: str) -> list[dict]:
    with db.conectar() as conn:
        linhas = conn.execute(
            "SELECT nome, data FROM visitantes WHERE apartamento = ? ORDER BY data, id",
            (apartamento,),
        ).fetchall()
    return [dict(linha) for linha in linhas]


def autorizar_visitante(
    apartamento: str, nome: str, data: str, chave: str | None = None
) -> dict:
    """Registra a autorização de entrada. Idempotente pela ``chave``."""
    nome = _validar_nome(nome)
    validar_data(data)

    with db.transacao() as conn:
        if chave is not None:
            existente = conn.execute(
                "SELECT apartamento, nome, data FROM visitantes WHERE chave_idempotencia = ?",
                (chave,),
            ).fetchone()
            if existente is not None:
                if tuple(existente) != (apartamento, nome, data):
                    raise ChaveIdempotenciaReutilizada(chave)
                return {"nome": nome, "data": data}

        conn.execute(
            "INSERT INTO visitantes (apartamento, nome, data, chave_idempotencia)"
            " VALUES (?, ?, ?, ?)",
            (apartamento, nome, data, chave),
        )
    return {"nome": nome, "data": data}


# --- Sessões (session_id -> apartamento) ---


def registrar_sessao(session_id: str, apartamento: str) -> None:
    with db.transacao() as conn:
        conn.execute(
            "INSERT INTO sessoes (session_id, apartamento) VALUES (?, ?)",
            (session_id, apartamento),
        )


def apartamento_da_sessao(session_id: str) -> str | None:
    with db.conectar() as conn:
        linha = conn.execute(
            "SELECT apartamento FROM sessoes WHERE session_id = ?", (session_id,)
        ).fetchone()
    return linha["apartamento"] if linha else None
