"""Chaves do resultado anterior: a tabela de capacidades diz como ligar uma entidade à outra, e a consulta aceita a restrição."""

import sqlite3

import pytest

from src.capacidades import CapacidadesInvalidas, interpretar
from src.router import PlanoInvalido, consultar
from src.schema import PlanoConsulta, Rota

DADOS = {
    "cenario": "t",
    "descricao": "teste",
    "entidades": {
        "trabalho": {
            "tabela": "trabalho",
            "exibir": ["id", "titulo"],
            "ordem_padrao": "id",
            "chave": "id",
            "campos": [{"nome": "id", "tipo": "id"}, {"nome": "titulo", "tipo": "texto"}],
        },
        "financiamento": {
            "tabela": "financiamento",
            "exibir": ["trabalho_id", "financiador_nome"],
            "ordem_padrao": "trabalho_id",
            "chave": "trabalho_id",
            "campos": [
                {"nome": "trabalho_id", "tipo": "id", "refere": "trabalho"},
                {"nome": "financiador_nome", "tipo": "categoria"},
            ],
        },
        "contagem": {
            "tabela": "contagem",
            "exibir": ["ano"],
            "ordem_padrao": "ano",
            "campos": [{"nome": "ano", "tipo": "inteiro"}],
        },
    },
}


def test_tipo_da_chave_e_campo_de_ligacao():
    cap = interpretar(DADOS)
    assert cap.tipo_da_chave("trabalho") == "trabalho"
    assert cap.tipo_da_chave("financiamento") == "trabalho"  # trabalho_id refere trabalho
    assert cap.tipo_da_chave("contagem") is None
    assert cap.campo_de_ligacao("financiamento", "trabalho") == "trabalho_id"
    assert cap.campo_de_ligacao("trabalho", "trabalho") == "id"
    assert cap.campo_de_ligacao("contagem", "trabalho") is None


def test_chave_precisa_ser_campo_exibido():
    ruim = {**DADOS, "entidades": {"trabalho": {**DADOS["entidades"]["trabalho"], "chave": "titulo", "exibir": ["id"]}}}
    with pytest.raises(CapacidadesInvalidas):
        interpretar(ruim)
    ruim = {**DADOS, "entidades": {"trabalho": {**DADOS["entidades"]["trabalho"], "chave": "nao_existe"}}}
    with pytest.raises(CapacidadesInvalidas):
        interpretar(ruim)


def _banco(tmp_path):
    caminho = tmp_path / "t.sqlite"
    con = sqlite3.connect(caminho)
    con.execute("CREATE TABLE financiamento (trabalho_id TEXT, financiador_nome TEXT)")
    con.executemany("INSERT INTO financiamento VALUES (?, ?)", [("W1", "FAPERJ"), ("W2", "FAPERJ"), ("W3", "CNPq"), ("W9", "FAPERJ")])
    con.commit()
    con.close()
    return caminho


def test_consulta_limitada_as_chaves_anteriores(tmp_path):
    cap = interpretar(DADOS)
    plano = PlanoConsulta(rota=Rota.ESTRUTURADA, naturezas=["estruturada"], entidade="financiamento", operacoes=["listar"])
    r = consultar(plano, _banco(tmp_path), cap, restricao=("trabalho_id", ["W1", "W3"]))
    assert sorted(l["trabalho_id"] for l in r.linhas) == ["W1", "W3"]  # W2 e W9 existem, mas não estavam no resultado anterior
    assert "resultado anterior" in r.descricao


def test_restricao_com_lista_vazia_nao_devolve_nada(tmp_path):
    cap = interpretar(DADOS)
    plano = PlanoConsulta(rota=Rota.ESTRUTURADA, naturezas=["estruturada"], entidade="financiamento", operacoes=["listar"])
    assert consultar(plano, _banco(tmp_path), cap, restricao=("trabalho_id", [])).linhas == []


def test_restricao_em_campo_desconhecido_e_recusada(tmp_path):
    cap = interpretar(DADOS)
    plano = PlanoConsulta(rota=Rota.ESTRUTURADA, naturezas=["estruturada"], entidade="financiamento", operacoes=["listar"])
    with pytest.raises(PlanoInvalido):
        consultar(plano, _banco(tmp_path), cap, restricao=("campo_que_nao_existe", ["W1"]))
