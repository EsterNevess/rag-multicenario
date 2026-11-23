"""Base agregada: a igualdade em nome que volta vazia é refeita com busca por trecho (caso PE-04, o financiador pelo nome parcial)."""

from __future__ import annotations

import json
import sqlite3

import pytest

from src.capacidades import capacidades_do_cenario
from src.generate import Contexto, coletar, relaxar_igualdades
from src.obs import PipelineLogger
from src.schema import Natureza, PlanoConsulta, Rota

CAP = capacidades_do_cenario("pesquisa")
FAPERJ = "Fundação Carlos Chagas Filho de Amparo à Pesquisa do Estado do Rio de Janeiro"


@pytest.fixture()
def banco(tmp_path):
    caminho = tmp_path / "agregado.sqlite"
    c = sqlite3.connect(caminho)
    c.execute("CREATE TABLE indicadores (id TEXT PRIMARY KEY, nome TEXT, descricao TEXT, unidade TEXT, origem TEXT, dimensoes TEXT, metrica TEXT, "
              "campo TEXT, filtro_fixo TEXT, definicao TEXT, ressalvas TEXT, linhas INTEGER, sql TEXT)")
    c.execute("CREATE TABLE metadados (chave TEXT PRIMARY KEY, valor TEXT)")
    c.execute("INSERT INTO indicadores VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
              ("trabalhos_por_financiador_e_ano", "Artigos por financiador e ano", "d", "contagem", "financiamento",
               json.dumps(["ano", "financiador_nome"]), "contagem", None, "[]", "Pares artigo-financiador.", "[]", 2, ""))
    relatorio = {"calculado_sobre": {"arquivo": "v2.sqlite", "sha256": "x", "periodo_de_publicacao": {"de": "2020-01-01", "ate": "2024-12-31"}}}
    c.executemany("INSERT INTO metadados VALUES (?, ?)", [("versao", "v2"), ("relatorio", json.dumps(relatorio))])
    c.execute("CREATE TABLE ind_trabalhos_por_financiador_e_ano (ano INTEGER, financiador_nome TEXT, valor REAL, n INTEGER)")
    c.executemany("INSERT INTO ind_trabalhos_por_financiador_e_ano VALUES (?,?,?,?)",
                  [(2023, FAPERJ, 305.0, 305), (2023, "Conselho Nacional de Desenvolvimento Científico e Tecnológico", 900.0, 900)])
    c.commit()
    c.close()
    return caminho


def _ctx(tmp_path, banco=None) -> Contexto:
    return Contexto(llm=None, logger=PipelineLogger(tmp_path / "logs"), cap=CAP, banco_agregado=banco, hoje="2026-11-23")


def _plano(**filtros) -> PlanoConsulta:
    return PlanoConsulta(rota=Rota.AGREGADA, naturezas=[Natureza.AGREGADA], entidade="trabalhos_por_financiador_e_ano", filtros={"ano": 2023, **filtros})


def test_nome_parcial_por_igualdade_vira_busca_por_trecho_no_indicador(tmp_path):
    relaxado = relaxar_igualdades(_plano(financiador_nome="Carlos Chagas Filho"), _ctx(tmp_path), agregada=True)
    assert relaxado is not None
    assert relaxado.filtros == {"ano": 2023, "financiador_nome__contem": "Carlos Chagas Filho"}


def test_sem_agregada_o_indicador_nao_e_uma_entidade_conhecida(tmp_path):
    assert relaxar_igualdades(_plano(financiador_nome="Carlos Chagas Filho"), _ctx(tmp_path)) is None


def test_ano_e_outros_campos_que_nao_sao_texto_ficam_como_estao(tmp_path):
    assert relaxar_igualdades(_plano(), _ctx(tmp_path), agregada=True) is None


def test_coletar_refaz_a_consulta_agregada_vazia_e_acha_o_numero(tmp_path, banco):
    evidencias, _ = coletar(_plano(financiador_nome="Carlos Chagas Filho"), "Quantas publicações de 2023 citam a FAPERJ?", _ctx(tmp_path, banco))
    assert len(evidencias) == 1
    assert "valor=305.0" in evidencias[0].texto and "financiador_nome contém" in evidencias[0].texto
    assert "0 linha(s)" not in evidencias[0].texto


def test_nome_exato_que_acha_linhas_nao_e_alterado(tmp_path, banco):
    evidencias, _ = coletar(_plano(financiador_nome=FAPERJ), "Quantas publicações de 2023 citam a FAPERJ?", _ctx(tmp_path, banco))
    assert "1 linha(s)" in evidencias[0].texto and "valor=305.0" in evidencias[0].texto
    assert "financiador_nome contém" not in evidencias[0].texto


def test_consulta_vazia_que_continua_vazia_segue_vazia(tmp_path, banco):
    evidencias, _ = coletar(_plano(financiador_nome="Inexistente"), "Quantas publicações de 2023 citam a FAPERJ?", _ctx(tmp_path, banco))
    assert "0 linha(s)" in evidencias[0].texto
