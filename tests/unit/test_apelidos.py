"""Apelidos de campo (FAPERJ -> nome oficial) e o aviso de que zero linhas não prova que não há registros."""

from __future__ import annotations

import json
import sqlite3

import pytest

from src.capacidades import CapacidadesInvalidas, capacidades_do_cenario, interpretar
from src.fetch_corpus import Pastas
from src.indicadores import construir, localizar_agregado
from src.normalize_openalex import COLUNAS_FINANCIAMENTO, COLUNAS_INSTITUICAO, COLUNAS_TRABALHO, _ddl
from src.router import consultar
from src.schema import Natureza, PlanoConsulta, Rota

CAP = capacidades_do_cenario("pesquisa")
FAPERJ = "Fundação Carlos Chagas Filho de Amparo à Pesquisa do Estado do Rio de Janeiro"


def _plano(entidade, rota=Rota.ESTRUTURADA, naturezas=(Natureza.ESTRUTURADA,), **kw):
    return PlanoConsulta(rota=rota, naturezas=list(naturezas), entidade=entidade, **kw)


@pytest.fixture()
def banco(tmp_path):
    caminho = tmp_path / "v1.sqlite"
    c = sqlite3.connect(caminho)
    c.execute(_ddl("trabalho", COLUNAS_TRABALHO, chave="id"))
    c.execute(_ddl("producao_instituicao", COLUNAS_INSTITUICAO))
    c.execute(_ddl("financiamento", COLUNAS_FINANCIAMENTO))
    c.execute("CREATE TABLE metadados (chave TEXT PRIMARY KEY, valor TEXT)")
    for n, (ano, financiador) in enumerate([(2023, FAPERJ), (2023, FAPERJ), (2023, "Conselho Nacional de Desenvolvimento Científico e Tecnológico"), (2022, FAPERJ)], 1):
        linha = dict.fromkeys(COLUNAS_FINANCIAMENTO)
        linha.update(trabalho_id=f"W{n}", financiador_nome=financiador, ano=ano, citacoes=10)
        c.execute(f"INSERT INTO financiamento VALUES ({','.join('?' * len(COLUNAS_FINANCIAMENTO))})", [linha[k] for k in COLUNAS_FINANCIAMENTO])
    c.execute("INSERT INTO metadados VALUES ('versao', 'v1'), ('cenario', 'pesquisa'), ('relatorio', ?)", (json.dumps({"periodo_de_publicacao": {"de": "2020-01-01", "ate": "2024-12-31"}}),))
    c.commit()
    c.close()
    return caminho


# ----------------------------------------------------------------- o campo


@pytest.mark.parametrize("dito", ["FAPERJ", "faperj", "Faperj", " FAPERJ "])
def test_apelido_vira_o_nome_oficial_sem_diferenca_de_caixa(dito):
    assert CAP.entidades["financiamento"].campos["financiador_nome"].canonico(dito) == FAPERJ


def test_o_que_nao_e_apelido_passa_sem_mudar():
    campo = CAP.entidades["financiamento"].campos["financiador_nome"]
    assert campo.canonico("Wellcome Trust") == "Wellcome Trust" and campo.canonico(2023) == 2023


def test_instituicoes_do_rj_tem_apelido_para_todas_as_dez():
    campo = CAP.entidades["producao_instituicao"].campos["instituicao_nome"]
    siglas = ["UFRJ", "UERJ", "UFF", "Fiocruz", "PUC-Rio", "UENF", "UFRRJ", "IMPA", "CEFET-RJ", "UNIRIO"]
    assert all(campo.canonico(s) != s for s in siglas)


def _base(**campo):
    campo = {"nome": "a", "tipo": "categoria", **campo}
    return {"cenario": "x", "entidades": {"e": {"exibir": ["a"], "ordem_padrao": "a", "campos": [campo]}}}


@pytest.mark.parametrize("campo,trecho", [
    ({"apelidos": {}}, "apelidos"),
    ({"apelidos": ["x"]}, "apelidos"),
    ({"tipo": "inteiro", "apelidos": {"a": "b"}}, "apelidos"),
    ({"apelidos": {"E": "Estadual"}, "valores": {"E": "Estadual"}}, "apelidos"),
])
def test_apelidos_invalidos_sao_recusados_ao_carregar(campo, trecho):
    with pytest.raises(CapacidadesInvalidas, match=trecho):
        interpretar(_base(**campo))


# ----------------------------------------------------------------- a consulta


def test_filtro_por_sigla_acha_as_linhas_do_nome_oficial_e_mostra_o_apelido(banco):
    r = consultar(_plano("financiamento", operacoes=["contar"], filtros={"financiador_nome": "FAPERJ", "ano": 2023}), banco, CAP)
    assert r.linhas == [{"total": 2}]
    assert "apelido FAPERJ" in r.descricao and FAPERJ in r.descricao


def test_sem_o_apelido_a_sigla_nao_casaria_e_a_consulta_avisa_que_zero_nao_prova_nada(banco):
    from dataclasses import replace

    sem_apelidos = {n: replace(e, campos={k: replace(c, apelidos=None) for k, c in e.campos.items()}) for n, e in CAP.entidades.items()}
    sem = replace(CAP, entidades=sem_apelidos)
    r = consultar(_plano("financiamento", operacoes=["listar"], filtros={"financiador_nome": "FAPERJ"}), banco, sem)
    assert r.linhas == [] and any("NÃO prova" in a and "financiador_nome" in a for a in r.avisos)


def test_zero_linhas_com_filtro_de_igualdade_em_nome_traz_o_aviso(banco):
    r = consultar(_plano("financiamento", operacoes=["listar"], filtros={"financiador_nome": "Instituição Que Não Existe"}), banco, CAP)
    assert r.linhas == [] and any("exigem o nome exato" in a for a in r.avisos)


def test_zero_linhas_sem_filtro_de_nome_ou_com_linhas_nao_traz_o_aviso(banco):
    vazio_por_ano = consultar(_plano("financiamento", operacoes=["listar"], filtros={"ano": 1999}), banco, CAP)
    com_linhas = consultar(_plano("financiamento", operacoes=["listar"], filtros={"financiador_nome": "FAPERJ"}), banco, CAP)
    assert vazio_por_ano.linhas == [] and not vazio_por_ano.avisos
    assert com_linhas.linhas and not com_linhas.avisos


def test_indicador_herda_os_apelidos_das_suas_dimensoes(tmp_path, banco):
    dados = tmp_path / "data"
    (dados / "normalized" / "pesquisa").mkdir(parents=True)
    banco.rename(dados / "normalized" / "pesquisa" / "v1.sqlite")
    pastas = Pastas(dados=dados, cenario=tmp_path / "sc", cenario_id="pesquisa")
    construir("pesquisa", "v1", pastas)
    agregado = localizar_agregado(pastas, "v1")
    r = consultar(_plano("trabalhos_por_financiador_e_ano", Rota.AGREGADA, (Natureza.AGREGADA,), filtros={"financiador_nome": "faperj", "ano": 2023}), agregado, CAP)
    assert r.linhas == [{"ano": 2023, "financiador_nome": FAPERJ, "valor": 2.0, "n": 2}]


# ----------------------------------------------------------------- na geração


def test_a_regra_de_resposta_proibe_dizer_que_nao_ha_quando_o_filtro_pode_nao_ter_casado():
    from src.generate import sistema_geracao

    assert "nunca afirme que \"não há\"" in sistema_geracao(CAP)


def test_o_aviso_de_zero_linhas_chega_ao_modelo_de_geracao(tmp_path, banco):
    from src.generate import Contexto, responder
    from src.obs import PipelineLogger
    from tests.fakes import LLMRoteirizado

    plano = {"rota": "estruturada", "naturezas": ["estruturada"], "entidade": "financiamento", "filtros": {"financiador_nome": "Ninguém"}, "operacoes": ["contar"],
             "campo_agregacao": None, "agrupar_por": [], "limite": None, "consulta_semantica": None}
    llm = LLMRoteirizado(json.dumps(plano), "A consulta não achou registros, mas o filtro pode não ter casado [1].")
    ctx = Contexto(llm=llm, logger=PipelineLogger(tmp_path / "logs"), cap=CAP, banco=banco, hoje="2026-11-06")
    responder("Quantos artigos o financiador Ninguém financiou?", ctx)
    assert "ATENÇÃO: Nenhum registro casou" in llm.chamadas[1][1]


def test_contar_com_um_nome_que_nao_casa_devolve_total_zero_e_tambem_avisa(banco):
    r = consultar(_plano("financiamento", operacoes=["contar"], filtros={"financiador_nome": "Instituição Que Não Existe"}), banco, CAP)
    assert r.linhas == [{"total": 0}] and any("NÃO prova" in a for a in r.avisos)
    ok = consultar(_plano("financiamento", operacoes=["contar"], filtros={"financiador_nome": "FAPERJ"}), banco, CAP)
    assert ok.linhas == [{"total": 3}] and not ok.avisos
