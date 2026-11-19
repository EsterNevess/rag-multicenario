"""Pergunta de comparação de versões: documentos de todas as versões e filtro de texto que não esvazia a consulta."""

from __future__ import annotations

import sqlite3

import pytest

from src.capacidades import capacidades_do_cenario
from src.generate import Contexto, coletar, completar_plano, relaxar_igualdades
from src.obs import PipelineLogger
from src.schema import Natureza, PlanoConsulta, Rota

COLUNAS = ["id", "doenca", "versao_nova", "data_nova", "versao_anterior", "data_anterior", "secao", "presenca", "caracteres_nova",
           "caracteres_anterior", "frases_novas", "frases_removidas", "frases_mantidas_pct", "exemplos_de_frases_novas"]


@pytest.fixture()
def banco(tmp_path):
    caminho = tmp_path / "saude.sqlite"
    c = sqlite3.connect(caminho)
    c.execute(f"CREATE TABLE pcdt_secao ({', '.join(COLUNAS)})")
    linha = dict.fromkeys(COLUNAS)
    linha.update(id=1, doenca="Artrite reumatoide", versao_nova="Portaria Conjunta SAES/SCTIE 33/2026", data_nova="2026-01-19",
                 versao_anterior="Portaria Conjunta SAES/SCTIE 16/2021", data_anterior="2021-09-10", secao="6. Tratamento",
                 presenca="nas duas versões", frases_novas=135, frases_removidas=5, frases_mantidas_pct=43.5)
    c.execute(f"INSERT INTO pcdt_secao VALUES ({','.join('?' * len(COLUNAS))})", [linha[k] for k in COLUNAS])
    c.commit()
    c.close()
    return caminho


def _ctx(tmp_path, banco=None) -> Contexto:
    return Contexto(llm=None, logger=PipelineLogger(tmp_path / "logs"), cap=capacidades_do_cenario("saude"), banco=banco, hoje="2026-11-19")


def _plano(**kw) -> PlanoConsulta:
    base = {"rota": Rota.HIBRIDA, "naturezas": [Natureza.ESTRUTURADA, Natureza.NORMATIVA], "entidade": "pcdt_secao",
            "filtros": {"doenca__contem": "artrite"}, "operacoes": ["listar"], "consulta_semantica": "comparar as versões"}
    base.update(kw)
    return PlanoConsulta(**base)


# ----------------------------------------------------------------- documentos de todas as versões


def test_comparacao_sem_vigencia_ganha_todas_e_a_doenca(tmp_path):
    plano = completar_plano(_plano(), _ctx(tmp_path))
    assert plano.filtros_documentos == {"vigente_em": "todas", "doenca": "artrite"}


def test_vigencia_escolhida_pelo_analisador_nao_e_trocada(tmp_path):
    plano = completar_plano(_plano(filtros_documentos={"vigente_em": "2020-01-01", "doenca": "parkinson"}), _ctx(tmp_path))
    assert plano.filtros_documentos == {"vigente_em": "2020-01-01", "doenca": "parkinson"}


def test_outra_entidade_ou_rota_estruturada_ficam_como_estao(tmp_path):
    ctx = _ctx(tmp_path)
    medicamento = _plano(entidade="pcdt_medicamento", filtros={"doenca__contem": "parkinson"})
    assert completar_plano(medicamento, ctx).filtros_documentos == {}
    so_sql = _plano(rota=Rota.ESTRUTURADA, naturezas=[Natureza.ESTRUTURADA])
    assert completar_plano(so_sql, ctx).filtros_documentos == {}


# ----------------------------------------------------------------- igualdade em campo de texto que não casa


def test_igualdade_em_versao_vira_busca_por_trecho(tmp_path):
    relaxado = relaxar_igualdades(_plano(filtros={"doenca__contem": "artrite", "versao_nova": "2026"}), _ctx(tmp_path))
    assert relaxado is not None
    assert relaxado.filtros == {"doenca__contem": "artrite", "versao_nova__contem": "2026"}


def test_sem_igualdade_em_texto_nao_ha_o_que_relaxar(tmp_path):
    ctx = _ctx(tmp_path)
    assert relaxar_igualdades(_plano(filtros={"doenca__contem": "artrite"}), ctx) is None
    assert relaxar_igualdades(_plano(filtros={"data_nova__gte": "2026-01-01"}), ctx) is None


def test_coletar_refaz_a_consulta_vazia_com_busca_por_trecho(tmp_path, banco):
    evidencias, _ = coletar(_plano(filtros={"doenca__contem": "artrite", "versao_nova": "2026"}), "o que mudou?", _ctx(tmp_path, banco))
    assert len(evidencias) == 1
    assert "1 linha(s)" in evidencias[0].texto and "frases_novas=135" in evidencias[0].texto
    assert "versao_nova contém" in evidencias[0].texto  # a consulta mostra que o filtro virou busca por trecho


def test_consulta_exata_que_acha_linhas_nao_e_alterada(tmp_path, banco):
    plano = _plano(filtros={"doenca__contem": "artrite", "versao_nova": "Portaria Conjunta SAES/SCTIE 33/2026"})
    evidencias, _ = coletar(plano, "o que mudou?", _ctx(tmp_path, banco))
    assert "1 linha(s)" in evidencias[0].texto and "versao_nova = Portaria Conjunta SAES/SCTIE 33/2026" in evidencias[0].texto


def test_consulta_vazia_que_continua_vazia_segue_vazia(tmp_path, banco):
    evidencias, _ = coletar(_plano(filtros={"doenca__contem": "artrite", "versao_nova": "1999"}), "o que mudou?", _ctx(tmp_path, banco))
    assert "0 linha(s)" in evidencias[0].texto
