import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from aurora import config, db, repositorio, restaurar
from aurora.repositorio import DadoInvalido, DataOcupada


def test_restauracao_carrega_dados_iniciais(banco):
    assert repositorio.listar_reservas("101") == [
        {"codigo": "RSV-1377", "area": "quadra", "data": "2030-03-09"}
    ]
    assert repositorio.listar_visitantes("302") == [{"nome": "Marina Duarte", "data": "2030-03-16"}]


def test_restauracao_desfaz_mudancas_e_apaga_sessoes(banco):
    config.SESSOES_DB.write_text("sessao antiga")
    repositorio.cancelar_reserva("101", codigo="RSV-1377")
    repositorio.autorizar_visitante("101", "Joana Ribeiro", "2030-04-21")
    repositorio.registrar_sessao("s1", "101")

    restaurar.restaurar()

    assert [r["codigo"] for r in repositorio.listar_reservas("101")] == ["RSV-1377"]
    assert repositorio.listar_visitantes("101") == []
    assert repositorio.apartamento_da_sessao("s1") is None
    assert not config.SESSOES_DB.exists()


def test_data_livre_so_devolve_booleano(banco):
    assert repositorio.data_livre("salao-de-festas", "2030-03-16") is False
    assert repositorio.data_livre("salao-de-festas", "2030-04-20") is True


def test_criar_reserva_e_data_ocupada(banco):
    codigo = repositorio.criar_reserva("101", "salao-de-festas", "2030-04-20")
    assert codigo.startswith("RSV-")
    assert {"codigo": codigo, "area": "salao-de-festas", "data": "2030-04-20"} in (
        repositorio.listar_reservas("101")
    )
    with pytest.raises(DataOcupada):
        repositorio.criar_reserva("201", "salao-de-festas", "2030-04-20")
    # Data ocupada pelo 302 nos dados iniciais.
    with pytest.raises(DataOcupada):
        repositorio.criar_reserva("101", "salao-de-festas", "2030-03-16")


def test_exclusividade_sob_concorrencia(banco):
    apartamentos = [f"A{i}" for i in range(20)]

    def tentar(apartamento):
        try:
            return repositorio.criar_reserva(apartamento, "salao-de-festas", "2030-05-11")
        except DataOcupada:
            return None

    with ThreadPoolExecutor(max_workers=20) as executor:
        resultados = list(executor.map(tentar, apartamentos))

    assert sum(r is not None for r in resultados) == 1
    with db.conectar() as conn:
        (total,) = conn.execute(
            "SELECT COUNT(*) FROM reservas"
            " WHERE area = 'salao-de-festas' AND data = '2030-05-11' AND status = 'ativa'"
        ).fetchone()
    assert total == 1


def test_indice_unico_vale_mesmo_sem_o_repositorio(banco):
    # A garantia está no banco: um INSERT direto também é recusado.
    with db.conectar() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO reservas (codigo, apartamento, area, data)"
            " VALUES ('X-1', '101', 'salao-de-festas', '2030-03-16')"
        )


def test_cancelar_libera_data_mas_nao_o_codigo(banco, monkeypatch):
    assert repositorio.cancelar_reserva("101", codigo="RSV-1377") == {
        "codigo": "RSV-1377",
        "area": "quadra",
        "data": "2030-03-09",
    }
    assert repositorio.listar_reservas("101") == []

    # Força o gerador a sugerir o código cancelado: o banco recusa e outro é gerado.
    sugestoes = iter(["RSV-1377", "RSV-NOVO0001"])
    monkeypatch.setattr(repositorio, "_novo_codigo", lambda: next(sugestoes))
    codigo = repositorio.criar_reserva("101", "quadra", "2030-03-09")
    assert codigo == "RSV-NOVO0001"


def test_nao_cancela_reserva_de_outro_apartamento(banco):
    assert repositorio.cancelar_reserva("101", codigo="RSV-4821") is None
    assert repositorio.cancelar_reserva("101", area="salao-de-festas", data="2030-03-16") is None
    assert [r["codigo"] for r in repositorio.listar_reservas("302")] == ["RSV-4821"]


def test_criar_reserva_idempotente_pela_chave(banco):
    primeiro = repositorio.criar_reserva("101", "salao-de-festas", "2030-04-20", chave="fc-1")
    segundo = repositorio.criar_reserva("101", "salao-de-festas", "2030-04-20", chave="fc-1")
    assert primeiro == segundo
    salao = [r for r in repositorio.listar_reservas("101") if r["area"] == "salao-de-festas"]
    assert len(salao) == 1
    with pytest.raises(repositorio.ChaveIdempotenciaReutilizada):
        repositorio.criar_reserva("101", "churrasqueira", "2030-04-20", chave="fc-1")


def test_autorizar_visitante_idempotente(banco):
    repositorio.autorizar_visitante("101", "  Joana   Ribeiro ", "2030-04-21", chave="fc-2")
    repositorio.autorizar_visitante("101", "Joana Ribeiro", "2030-04-21", chave="fc-2")
    assert repositorio.listar_visitantes("101") == [{"nome": "Joana Ribeiro", "data": "2030-04-21"}]


@pytest.mark.parametrize("data", ["20300420", "2030-02-30", "2030/04/20", "", "amanhã"])
def test_data_invalida(banco, data):
    with pytest.raises(DadoInvalido):
        repositorio.criar_reserva("101", "quadra", data)


def test_area_inexistente(banco):
    with pytest.raises(repositorio.AreaInexistente):
        repositorio.criar_reserva("101", "piscina", "2030-04-20")


def test_sessoes(banco):
    repositorio.registrar_sessao("s1", "101")
    assert repositorio.apartamento_da_sessao("s1") == "101"
    assert repositorio.apartamento_da_sessao("outra") is None
