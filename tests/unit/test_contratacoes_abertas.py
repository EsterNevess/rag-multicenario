"""Contratações "abertas" (RJ-07): o prazo de propostas é campo consultável e o analisador sabe a data de hoje.

Antes, "aberta" virava só `situacao_nome = Divulgada no PNCP`, que é o estado de publicação (36.726 de 37.637 linhas), e o pregão
de um órgão que tinha encerrado as propostas em setembro aparecia como aberto.
"""

import sqlite3

from src.capacidades import capacidades_do_cenario
from src.generate import Contexto, montar_prompt_analise
from src.obs import PipelineLogger
from src.router import consultar
from src.schema import PlanoConsulta, Rota

RJ = capacidades_do_cenario("rj")


def _banco(tmp_path):
    caminho = tmp_path / "rj.sqlite"
    con = sqlite3.connect(caminho)
    colunas = [c.nome for c in RJ.entidades["contratacao"].campos.values() if c.expr is None]
    con.execute(f"CREATE TABLE contratacao ({', '.join(c + ' TEXT' for c in colunas)})")
    linhas = [
        ("A", "2026-08-30", "2026-09-17T10:00:00", "Divulgada no PNCP"),  # encerrou
        ("B", "2026-08-30", "2026-12-10T10:00:00", "Divulgada no PNCP"),  # aberta em 24/11
        ("C", "2026-08-30", "2026-12-10T10:00:00", "Revogada"),  # prazo futuro, mas revogada
        ("D", "2026-08-30", None, "Divulgada no PNCP"),  # sem prazo: não dá para dizer
    ]
    for numero, publicacao, encerra, situacao in linhas:
        con.execute(
            "INSERT INTO contratacao (numero_controle_pncp, data_publicacao, data_encerramento_proposta, situacao_nome) VALUES (?, ?, ?, ?)",
            (numero, publicacao, encerra, situacao),
        )
    con.commit()
    con.close()
    return caminho


def test_prazo_de_propostas_e_campo_consultavel_e_aparece_na_listagem():
    contratacao = RJ.entidades["contratacao"]
    assert contratacao.campos["data_encerramento_proposta"].tipo == "data"
    assert "data_encerramento_proposta" in contratacao.exibir


def test_aberta_exige_prazo_futuro_e_nao_so_a_situacao(tmp_path):
    plano = PlanoConsulta(
        rota=Rota.ESTRUTURADA, naturezas=["estruturada"], entidade="contratacao", operacoes=["listar"],
        filtros={"data_encerramento_proposta__gte": "2026-11-24", "situacao_nome": "Divulgada no PNCP"},
    )
    r = consultar(plano, _banco(tmp_path), RJ)
    assert [l["numero_controle_pncp"] for l in r.linhas] == ["B"]  # A encerrou, C foi revogada, D não tem prazo


def test_o_analisador_sabe_a_data_de_hoje_e_a_regra_das_abertas(tmp_path):
    ctx = Contexto(llm=None, logger=PipelineLogger(tmp_path), cap=RJ, banco=tmp_path / "x", hoje="2026-11-24")
    prompt = montar_prompt_analise(ctx)
    assert "Data de hoje: 2026-11-24" in prompt
    assert "data_encerramento_proposta__gte" in prompt and "NÃO quer dizer que a contratação ainda recebe propostas" in prompt


def _banco_contratos(tmp_path):
    caminho = tmp_path / "contratos.sqlite"
    con = sqlite3.connect(caminho)
    colunas = [c.nome for c in RJ.entidades["contrato"].campos.values() if c.expr is None]
    con.execute(f"CREATE TABLE contrato ({', '.join(c + ' TEXT' for c in colunas)})")
    linhas = [
        ("V1", "2026-01-10", "2026-12-31"),  # vigente em 24/11
        ("V2", "2026-12-01", "2027-06-30"),  # ainda não começou
        ("V3", "2024-01-10", "2025-12-31"),  # já terminou
        ("V4", "2025-01-10", "8202-05-14"),  # fim com erro de digitação: o filtro não o distingue
    ]
    for numero, inicio, fim in linhas:
        con.execute("INSERT INTO contrato (numero_controle_pncp, data_vigencia_inicio, data_vigencia_fim) VALUES (?, ?, ?)", (numero, inicio, fim))
    con.commit()
    con.close()
    return caminho


def test_contrato_vigente_exige_inicio_e_fim_em_torno_de_hoje(tmp_path):
    contrato = RJ.entidades["contrato"]
    assert contrato.campos["data_vigencia_inicio"].tipo == "data" and "data_vigencia_fim" in contrato.exibir
    plano = PlanoConsulta(
        rota=Rota.ESTRUTURADA, naturezas=["estruturada"], entidade="contrato", operacoes=["listar"],
        filtros={"data_vigencia_inicio__lte": "2026-11-24", "data_vigencia_fim__gte": "2026-11-24"},
    )
    r = consultar(plano, _banco_contratos(tmp_path), RJ)
    assert sorted(l["numero_controle_pncp"] for l in r.linhas) == ["V1", "V4"]  # V4 é o fim de 8202: por isso a dica manda avisar


def test_a_dica_dos_contratos_vigentes_chega_ao_analisador(tmp_path):
    ctx = Contexto(llm=None, logger=PipelineLogger(tmp_path), cap=RJ, banco=tmp_path / "x", hoje="2026-11-24")
    prompt = montar_prompt_analise(ctx)
    assert "data_vigencia_inicio__lte e data_vigencia_fim__gte" in prompt and "8202-05-14" in prompt

def test_filtro_de_data_depois_do_fim_da_base_avisa_na_evidencia(tmp_path):
    plano = PlanoConsulta(
        rota=Rota.ESTRUTURADA, naturezas=["estruturada"], entidade="contrato", operacoes=["listar"],
        filtros={"data_vigencia_inicio__lte": "2026-11-24", "data_vigencia_fim__gte": "2026-11-24"},
    )
    r = consultar(plano, _banco_contratos(tmp_path), RJ)
    avisos = [a for a in r.avisos if "só traz informação até 2026-08-31" in a]
    assert len(avisos) == 1 and "2026-11-24" in avisos[0]  # um aviso só, mesmo com dois filtros de data


def test_filtro_de_data_dentro_da_base_nao_avisa(tmp_path):
    plano = PlanoConsulta(
        rota=Rota.ESTRUTURADA, naturezas=["estruturada"], entidade="contrato", operacoes=["listar"],
        filtros={"data_vigencia_inicio__lte": "2026-06-30", "data_vigencia_fim__gte": "2026-06-30"},
    )
    assert not any("só traz informação até" in a for a in consultar(plano, _banco_contratos(tmp_path), RJ).avisos)


def test_contratacoes_abertas_depois_do_fim_da_base_tambem_avisam(tmp_path):
    plano = PlanoConsulta(
        rota=Rota.ESTRUTURADA, naturezas=["estruturada"], entidade="contratacao", operacoes=["listar"],
        filtros={"data_encerramento_proposta__gte": "2026-11-24", "situacao_nome": "Divulgada no PNCP"},
    )
    assert any("só traz informação até 2026-08-31" in a for a in consultar(plano, _banco(tmp_path), RJ).avisos)


def test_base_ate_so_vale_para_campo_de_data():
    import pytest

    from src.capacidades import CapacidadesInvalidas, _campo

    assert _campo({"nome": "d", "tipo": "data", "base_ate": "2026-08-31"}, "t").base_ate == "2026-08-31"
    for ruim in ({"nome": "d", "tipo": "texto", "base_ate": "2026-08-31"}, {"nome": "d", "tipo": "data", "base_ate": "31/08/2026"}):
        with pytest.raises(CapacidadesInvalidas, match="base_ate"):
            _campo(ruim, "t")
