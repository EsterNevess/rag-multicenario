"""PE-02: "desses" limita a consulta aos registros que a resposta anterior listou, com as chaves vindas do banco."""

from __future__ import annotations

import sqlite3

import pytest

from src.capacidades import capacidades_do_cenario
from src.generate import Contexto, TurnoAnterior, chaves_da_listagem, coletar, mensagem_do_analisador
from src.obs import PipelineLogger
from src.router import consultar
from src.schema import ChavesDoResultado, Natureza, PlanoConsulta, Rota

CAP = capacidades_do_cenario("pesquisa")


@pytest.fixture()
def banco(tmp_path):
    caminho = tmp_path / "v.sqlite"
    c = sqlite3.connect(caminho)
    c.execute("CREATE TABLE trabalho (id TEXT, doi TEXT, titulo TEXT, ano INTEGER, data_publicacao TEXT, citacoes INTEGER, periodico TEXT, financiadores TEXT)")
    c.executemany("INSERT INTO trabalho (id, titulo, ano, data_publicacao, citacoes, financiadores) VALUES (?,?,?,?,?,?)",
                  [(f"W{i}", f"Trabalho {i}", 2022, f"2022-01-{i:02d}", 100 - i, "") for i in range(1, 6)])
    c.execute("CREATE TABLE financiamento (trabalho_id TEXT, financiador_id TEXT, financiador_nome TEXT, ano INTEGER, titulo TEXT, area TEXT, citacoes INTEGER)")
    c.executemany("INSERT INTO financiamento VALUES (?,?,?,?,?,?,?)",
                  [("W2", "F1", "FAPERJ", 2022, "Trabalho 2", "x", 1), ("W4", "F2", "CNPq", 2022, "Trabalho 4", "x", 1),
                   ("W9", "F1", "FAPERJ", 2022, "Trabalho 9", "x", 1)])  # W9 financiado, mas fora do resultado anterior
    c.commit()
    c.close()
    return caminho


def _ctx(tmp_path, banco) -> Contexto:
    return Contexto(llm=None, logger=PipelineLogger(tmp_path / "logs"), cap=CAP, banco=banco, hoje="2026-11-23")


def _listar_trabalhos(limite=3) -> PlanoConsulta:
    return PlanoConsulta(rota=Rota.ESTRUTURADA, naturezas=[Natureza.ESTRUTURADA], entidade="trabalho", operacoes=["listar"], campo_agregacao="citacoes", limite=limite)


def _desses(**kw) -> PlanoConsulta:
    return PlanoConsulta(rota=Rota.ESTRUTURADA, naturezas=[Natureza.ESTRUTURADA], entidade="financiamento", operacoes=["listar"], do_resultado_anterior=True, **kw)


def test_a_listagem_devolve_as_chaves_do_banco_na_ordem(tmp_path, banco):
    ctx = _ctx(tmp_path, banco)
    plano = _listar_trabalhos()
    evidencias, _ = coletar(plano, "Os 3 mais citados?", ctx)
    assert evidencias[0].chaves == ChavesDoResultado(tipo="trabalho", valores=("W1", "W2", "W3"))


def test_contagem_nao_gera_chaves(tmp_path, banco):
    plano = PlanoConsulta(rota=Rota.ESTRUTURADA, naturezas=[Natureza.ESTRUTURADA], entidade="trabalho", operacoes=["contar"])
    r = consultar(plano, banco, CAP)
    assert chaves_da_listagem(r, plano, _ctx(tmp_path, banco)) is None


def test_desses_limita_a_consulta_ao_resultado_anterior(tmp_path, banco):
    ctx = _ctx(tmp_path, banco)
    anterior = TurnoAnterior("Os 3 mais citados?", _listar_trabalhos(), ChavesDoResultado(tipo="trabalho", valores=("W1", "W2", "W3")))
    evidencias, avisos = coletar(_desses(), "Desses, quais têm financiamento?", ctx, anterior)
    texto = evidencias[0].texto
    assert "W2" in texto and "W4" not in texto and "W9" not in texto  # só W2 estava no resultado anterior
    assert "1 linha(s)" in texto
    assert "resultado anterior" in texto
    assert not avisos
    assert evidencias[0].chaves == ChavesDoResultado(tipo="trabalho", valores=("W2",))  # e o resultado novo vira a memória


def test_sem_memoria_o_desses_avisa_e_nao_restringe(tmp_path, banco):
    evidencias, avisos = coletar(_desses(), "Desses, quais têm financiamento?", _ctx(tmp_path, banco), None)
    assert "W9" in evidencias[0].texto
    assert any("NÃO foi limitada" in a for a in avisos)


def test_chaves_de_outro_tipo_nao_ligam(tmp_path, banco):
    anterior = TurnoAnterior("?", _listar_trabalhos(), ChavesDoResultado(tipo="instituicao", valores=("I1",)))
    _, avisos = coletar(_desses(), "Desses?", _ctx(tmp_path, banco), anterior)
    assert any("não se liga" in a for a in avisos)


def test_a_mensagem_do_analisador_informa_o_resultado_sem_listar_identificadores():
    anterior = TurnoAnterior("Os 3 mais citados?", _listar_trabalhos(), ChavesDoResultado(tipo="trabalho", valores=("W1", "W2", "W3")))
    mensagem = mensagem_do_analisador("Desses, quais têm financiamento?", anterior)
    assert "listou 3 registro(s) de trabalho" in mensagem and "do_resultado_anterior" in mensagem
    assert "W1" not in mensagem  # a LLM não vê nem escreve os identificadores


def test_sem_chaves_a_mensagem_nao_menciona_o_resultado():
    assert "listou" not in mensagem_do_analisador("E em 2025?", TurnoAnterior("Quantas?", _listar_trabalhos()))
