"""Ablação do custo do rerank (N candidatos e tokens por trecho), com o contexto e o modelo trocados por falsos."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src import avaliar_rerank as ar
from src import generate
from src.rerank import RerankerIndisponivel
from tests.unit.test_retrieve import CORPUS, _indice


def test_reordenar_so_mexe_nos_n_primeiros_e_empate_fica_na_ordem_da_fusao():
    ordem = ["a", "b", "c", "d", "e"]
    notas = {"a": 1.0, "b": 5.0, "c": 5.0, "d": 9.0, "e": 0.0}
    assert ar.reordenar(ordem, notas, 3) == ["b", "c", "a", "d", "e"]  # b e c empatam: b veio antes; d e e ficam onde estavam
    assert ar.reordenar(ordem, notas, 5) == ["d", "b", "c", "a", "e"]
    assert ar.reordenar(ordem, notas, 1) == ordem


def test_nome_da_variante():
    assert ar.nome_da_variante(10, 256) == "rerank_n10_t256"


class _ModeloFalso:
    """Dá a nota mais alta ao último trecho recebido, para que o rerank sempre mude a ordem; conta o que leu."""

    def __init__(self, tokens):
        self.tokens = tokens
        self.lidos = []

    def pontuar(self, consulta, textos):
        self.lidos.append(len(textos))
        return [float(i) for i in range(len(textos))]


@pytest.fixture()
def ambiente(tmp_path, monkeypatch):
    indice, embedder = _indice(tmp_path, CORPUS)
    ctx = SimpleNamespace(indices={"b": indice}, embedder=embedder, cap=SimpleNamespace(niveis_consultaveis=("publico",)), hoje="2026-11-16")
    monkeypatch.setattr(generate, "carregar_contexto", lambda config, pastas: ctx)
    itens = [ar.carregar_gold.__globals__["Item"]("Q1", "dispensa de licitação", "b", {}, ("dispensa",), {})]
    monkeypatch.setattr(ar, "carregar_gold", lambda cenario, raiz=None, conjunto="retrieval_gold": itens)
    return {t: _ModeloFalso(t) for t in ar.TAMANHOS}


def test_avaliar_mede_o_rrf_e_as_oito_variantes_e_calcula_o_rerank_uma_vez_por_tamanho(ambiente):
    r = ar.avaliar("rj", rerankers=ambiente, medir_tempo_em=0)
    assert r["itens_validos"] == 1 and r["itens_invalidos"] == []
    assert set(r["resumo"]) == {"rrf"} | {ar.nome_da_variante(n, t) for t in ar.TAMANHOS for n in ar.VARIANTES_N} and len(r["resumo"]) == 9
    assert all("ms_mediano" not in m for m in r["resumo"].values())  # tempo desligado
    assert ambiente[256].lidos == [5] and ambiente[512].lidos == [5]  # o corpus tem 5 trechos: uma chamada de 5 por tamanho, e as variantes só reordenam


def test_medir_tempo_chama_o_modelo_de_verdade_por_variante_quando_ha_candidatos_suficientes(tmp_path, monkeypatch):
    indice, embedder = _indice(tmp_path, CORPUS)
    ctx = SimpleNamespace(indices={"b": indice}, embedder=embedder, cap=SimpleNamespace(niveis_consultaveis=("publico",)), hoje="2026-11-16")
    monkeypatch.setattr(generate, "carregar_contexto", lambda config, pastas: ctx)
    monkeypatch.setattr(ar, "N_MAXIMO", 5)  # com 5 trechos no corpus, "candidatos suficientes" passa a ser 5
    monkeypatch.setattr(ar, "VARIANTES_N", (2, 5))
    itens = [ar.carregar_gold.__globals__["Item"]("Q1", "dispensa de licitação", "b", {}, ("dispensa",), {})]
    monkeypatch.setattr(ar, "carregar_gold", lambda cenario, raiz=None, conjunto="retrieval_gold": itens)
    modelos = {t: _ModeloFalso(t) for t in ar.TAMANHOS}
    r = ar.avaliar("rj", rerankers=modelos, medir_tempo_em=1)
    assert r["resumo"]["rerank_n2_t256"]["ms_mediano"] >= 0 and r["resumo"]["rerank_n5_t512"]["ms_mediano"] >= 0
    assert modelos[256].lidos == [5, 2, 5] and modelos[512].lidos == [5, 2, 5]  # pontuação das notas + uma medição por variante


def test_linha_de_comando_sem_o_modelo_sai_com_2_e_com_o_modelo_imprime_a_tabela_e_grava(monkeypatch, capsys, tmp_path):
    def sem_modelo(*a, **k):
        raise RerankerIndisponivel("faltam arquivos")

    monkeypatch.setattr(ar, "avaliar", sem_modelo)
    assert ar.main(["--scenario", "rj"]) == 2 and "faltam arquivos" in capsys.readouterr().err
    resultado = {"itens_validos": 3, "itens_invalidos": [], "resumo": {"rrf": {"hit@1": 0.5, "hit@5": 0.9, "mrr@10": 0.7, "p@5": 0.4},
                 "rerank_n10_t256": {"hit@1": 0.6, "hit@5": 0.9, "mrr@10": 0.8, "p@5": 0.4, "ms_mediano": 4200}}, "itens": []}
    monkeypatch.setattr(ar, "avaliar", lambda cenario, hoje, conjunto, medir_tempo_em: resultado)
    monkeypatch.setattr(ar, "RAIZ", tmp_path)
    assert ar.main(["--scenario", "saude", "--conjunto", "retrieval_gold_novas", "--tempo", "0"]) == 0
    saida = capsys.readouterr().out
    assert "3 perguntas válidas" in saida and "rerank_n10_t256" in saida and "4200" in saida
    assert json.loads((tmp_path / "logs" / "avaliacao-rerank-saude-novas.json").read_text(encoding="utf-8"))["itens_validos"] == 3


def test_pergunta_com_menos_candidatos_que_o_maximo_nao_entra_na_medicao_de_tempo(ambiente):
    """Com 5 trechos no corpus e 20 como máximo, medir o tempo de 'n20' seria mentira: a pergunta fica de fora."""
    r = ar.avaliar("rj", rerankers=ambiente, medir_tempo_em=1)
    assert all("ms_mediano" not in m for m in r["resumo"].values())
    assert ambiente[256].lidos == [5]  # só as notas, nenhuma chamada de medição
