"""Reranker: lógica de lotes, ordenação e erro sem arquivos. O modelo real não entra nos testes (é um download de ~550 MB)."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from src.rerank import Reranker, RerankerIndisponivel, carregar_reranker, probabilidade


class _Tokenizador:
    def __init__(self):
        self.truncado = self.preenchido = None
        self.pares = []

    def enable_truncation(self, max_length):
        self.truncado = max_length

    def enable_padding(self):
        self.preenchido = True

    def encode_batch(self, pares):
        self.pares.append(list(pares))
        return [SimpleNamespace(ids=[1, len(t), 2], attention_mask=[1, 1, 1]) for _, t in pares]


class _Sessao:
    """A nota de cada par é o tamanho do texto (o tokenizador falso o coloca na posição 1)."""

    def __init__(self, entradas=("input_ids", "attention_mask")):
        self._entradas = entradas
        self.chamadas = []

    def run(self, _saidas, entrada):
        self.chamadas.append(set(entrada))
        return [np.array([[float(ids[1])] for ids in entrada["input_ids"]])]


def _reranker(entradas=("input_ids", "attention_mask")):
    tok = _Tokenizador()
    return Reranker(_Sessao(entradas), tok, set(entradas)), tok


def test_configura_truncamento_e_preenchimento_no_tokenizador():
    r, tok = _reranker()
    assert tok.truncado == 512 and tok.preenchido is True and r.max_tokens == 512


def test_pontuar_devolve_uma_nota_por_texto_na_mesma_ordem_e_em_lotes():
    r, tok = _reranker()
    textos = ["a" * n for n in range(1, 21)]  # 20 textos: 3 lotes de 8, 8 e 4
    assert r.pontuar("pergunta", textos) == [float(n) for n in range(1, 21)]
    assert [len(l) for l in tok.pares] == [8, 8, 4]
    assert all(par[0] == "pergunta" for lote in tok.pares for par in lote)


def test_reordenar_poe_a_maior_nota_primeiro_e_empate_mantem_a_ordem_recebida():
    r, _ = _reranker()
    candidatos = [("c1", "aa"), ("c2", "bbbbb"), ("c3", "cc"), ("c4", "d")]
    assert r.reordenar("p", candidatos) == ["c2", "c1", "c3", "c4"]  # c1 e c3 empatam (2): c1 veio antes


def test_reordenar_lista_vazia_nao_chama_o_modelo():
    r, tok = _reranker()
    assert r.reordenar("p", []) == [] and tok.pares == []


def test_token_type_ids_so_vai_se_o_modelo_declarar_essa_entrada():
    r, _ = _reranker(("input_ids", "attention_mask"))
    r.pontuar("p", ["x"])
    assert r._sessao.chamadas == [{"input_ids", "attention_mask"}]
    r2, _ = _reranker(("input_ids", "attention_mask", "token_type_ids"))
    r2.pontuar("p", ["x"])
    assert r2._sessao.chamadas == [{"input_ids", "attention_mask", "token_type_ids"}]


def test_sem_os_arquivos_do_modelo_levanta_reranker_indisponivel_com_o_que_falta(tmp_path):
    (tmp_path / "tokenizer.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RerankerIndisponivel, match="model_int8.onnx"):
        carregar_reranker(tmp_path)


def test_probabilidade_do_logit():
    assert probabilidade(0.0) == 0.5 and probabilidade(10) > 0.99 and probabilidade(-10) < 0.01
