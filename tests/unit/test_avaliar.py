"""Avaliação da recuperação: métricas, critério de relevância e os conjuntos de perguntas dos três cenários."""

from __future__ import annotations

import json

import pytest

from src.avaliar import Item, carregar_gold, metricas, ranking, trechos_relevantes
from src.index import CacheDeEmbeddings, carregar_indice, construir_indice
from tests.fakes import EmbedderPalavras


def test_metricas_para_o_primeiro_relevante_na_terceira_posicao():
    m = metricas(["a", "b", "c", "d", "e"], {"c", "e"})
    assert m == {"hit@1": 0.0, "hit@5": 1.0, "mrr@10": pytest.approx(1 / 3), "p@5": 0.4}


def test_metricas_sem_nenhum_relevante_no_topo_ou_com_lista_vazia():
    assert metricas(["a", "b"], {"z"}) == {"hit@1": 0.0, "hit@5": 0.0, "mrr@10": 0.0, "p@5": 0.0}
    assert metricas([], {"z"})["hit@1"] == 0.0


def test_relevante_alem_da_decima_posicao_nao_entra_no_mrr_mas_entra_na_lista():
    ordem = [f"x{i}" for i in range(10)] + ["alvo"]
    assert metricas(ordem, {"alvo"})["mrr@10"] == 0.0


@pytest.fixture()
def indice(tmp_path):
    emb = EmbedderPalavras()
    def c(i, texto, **meta):
        return {"texto": texto, "metadata": {"source_file": f"{i}.txt", "doc_type": "pcdt", "chunk_id": i, "sensitivity": "publico", "base_id": "b", "natureza": "normativa", **meta}}
    chunks = [
        c("c1", "A insulina NPH é aplicada em duas doses diárias.", doenca="Diabetes", versao="V1"),
        c("c2", "Exames de acompanhamento: hemograma e creatinina.", doenca="Artrite", versao="V1"),
        c("c3", "Exames de acompanhamento: hemograma e VHS.", doenca="Artrite", versao="V2"),
        c("c4", "Texto sem relação alguma com o assunto.", doenca="Outra", versao="V2"),
    ]
    jsonl = tmp_path / "x.jsonl"
    jsonl.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in chunks), encoding="utf-8")
    cache = CacheDeEmbeddings(tmp_path / "c.sqlite", emb.identity)
    construir_indice(jsonl, tmp_path / "i", emb, cache, tamanho_lote=8, escrever=lambda _: None)
    cache.fechar()
    return carregar_indice(tmp_path / "i", emb.identity), emb


def _item(**kw):
    base = {"id": "T", "pergunta": "exames de acompanhamento hemograma", "base": "b", "filtros": {}, "texto": (), "metadata": {}}
    base.update(kw)
    return Item(**base)


def test_o_criterio_exige_todas_as_expressoes_de_texto_e_todos_os_metadados(indice):
    idx, _ = indice
    assert trechos_relevantes(idx, _item(texto=("hemograma",))) == {"c2", "c3"}
    assert trechos_relevantes(idx, _item(texto=("hemograma", "creatinina"))) == {"c2"}
    assert trechos_relevantes(idx, _item(texto=("hemograma",), metadata={"versao": "v2"})) == {"c3"}
    assert trechos_relevantes(idx, _item(texto=("nada disso existe",))) == set()


def test_o_criterio_ignora_acento_e_caixa(indice):
    idx, _ = indice
    assert trechos_relevantes(idx, _item(texto=("insulina nph",))) == {"c1"}
    assert trechos_relevantes(idx, _item(texto=("é aplicada",))) == {"c1"}


@pytest.mark.parametrize("sistema", ["bm25", "denso", "rrf", "rrf_k10", "rrf_sem_filtro"])
def test_cada_sistema_devolve_a_mesma_ordem_nas_repeticoes(indice, sistema):
    idx, emb = indice
    item = _item(filtros={"doenca__contem": "artrite"})
    primeira = ranking(sistema, idx, emb, item, "2026-11-16", ["publico"])
    assert primeira and ranking(sistema, idx, emb, item, "2026-11-16", ["publico"]) == primeira


def test_o_filtro_restringe_e_a_variante_sem_filtro_o_ignora(indice):
    idx, emb = indice
    com = ranking("rrf", idx, emb, _item(filtros={"doenca__contem": "artrite", "vigente_em": "$hoje"}), "2026-11-16", ["publico"])
    sem = ranking("rrf_sem_filtro", idx, emb, _item(filtros={"doenca__contem": "artrite"}), "2026-11-16", ["publico"])
    assert set(com) <= {"c2", "c3"} and "c1" in sem and "c4" in sem


@pytest.mark.parametrize("cenario", ["rj", "saude", "pesquisa"])
def test_o_conjunto_de_perguntas_do_cenario_esta_bem_formado(cenario):
    itens = carregar_gold(cenario)
    assert len(itens) >= 10 and len({i.id for i in itens}) == len(itens)
    for i in itens:
        assert i.pergunta.strip() and i.base and (i.texto or i.metadata), i.id


@pytest.mark.parametrize("cenario", ["rj", "saude", "pesquisa"])
def test_o_segundo_conjunto_de_perguntas_esta_bem_formado_e_nao_repete_ids_nem_perguntas_do_primeiro(cenario):
    primeiro = carregar_gold(cenario)
    novas = carregar_gold(cenario, conjunto="retrieval_gold_novas")
    assert len(novas) >= 8 and len({i.id for i in novas}) == len(novas)
    assert not {i.id for i in novas} & {i.id for i in primeiro}
    assert not {i.pergunta for i in novas} & {i.pergunta for i in primeiro}
    for i in novas:
        assert i.pergunta.strip() and i.base and (i.texto or i.metadata), i.id
