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
