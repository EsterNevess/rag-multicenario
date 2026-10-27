"""Testes da recuperação: BM25, busca por vetores, RRF, filtros e busca por base."""

from __future__ import annotations

import json
import sqlite3

import numpy as np
import pytest

from src.index import CacheDeEmbeddings, carregar_indice, construir_indice
from src.obs import PipelineLogger
from src.retrieve import (
    Candidato,
    buscar_bm25,
    buscar_denso,
    buscar_hibrido,
    buscar_por_bases,
    casa_filtro,
    fundir_rrf,
    preparar_consulta_fts,
)
from tests.fakes import EmbedderPalavras


def _chunk(chunk_id, texto, base="b", **meta):
    return {
        "texto": texto,
        "metadata": {
            "source_file": "doc.pdf", "doc_type": meta.pop("doc_type", "lei"), "chunk_id": chunk_id,
            "sensitivity": "publico", "base_id": base, **meta,
        },
    }


def _indice(tmp_path, chunks, nome="idx", embedder=None):
    embedder = embedder or EmbedderPalavras()
    caminho = tmp_path / f"{nome}.jsonl"
    caminho.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in chunks), encoding="utf-8")
    cache = CacheDeEmbeddings(tmp_path / f"{nome}-cache.sqlite", embedder.identity)
    construir_indice(caminho, tmp_path / nome, embedder, cache, tamanho_lote=8, escrever=lambda _: None)
    cache.fechar()
    return carregar_indice(tmp_path / nome, embedder.identity), embedder


CORPUS = [
    _chunk("c1", "A dispensa de licitação ocorre em hipóteses previstas em lei.", artigo="75"),
    _chunk("c2", "O pregão é a modalidade para bens e serviços comuns.", artigo="29"),
    _chunk("c3", "A concorrência é a modalidade para obras e serviços especiais de engenharia.", artigo="28"),
    _chunk("c4", "O contrato administrativo deve ter prazo de vigência determinado.", artigo="105"),
    _chunk("c5", "Licitação licitação licitação: o procedimento geral de licitação pública.", artigo="1"),
]


# ----------------------------------------------------------------- consulta FTS


def test_consulta_remove_palavras_de_ligacao_e_usa_aspas():
    assert preparar_consulta_fts("Qual é a dispensa de licitação?") == '"dispensa" OR "licitação"'


def test_consulta_so_com_palavras_de_ligacao_ou_vazia_vira_none():
    assert preparar_consulta_fts("de a o que") is None
    assert preparar_consulta_fts("   ?!  ") is None


def test_consulta_nao_deixa_o_usuario_injetar_operadores_do_fts():
    consulta = preparar_consulta_fts('licitação AND (dispensa NOT x* NEAR "y"')
    assert "(" not in consulta and "*" not in consulta
    assert all(parte.startswith('"') and parte.endswith('"') for parte in consulta.split(" OR "))
    sqlite3.connect(":memory:").execute(
        "CREATE VIRTUAL TABLE t USING fts5(x)"
    ).connection.execute("SELECT * FROM t WHERE t MATCH ?", (consulta,)).fetchall()  # não pode levantar erro


def test_consulta_mantem_numeros_e_nao_repete_palavras():
    assert preparar_consulta_fts("Art. 75 art 75 lei") == '"art" OR "75" OR "lei"'


# ----------------------------------------------------------------- filtros


def test_filtro_por_igualdade_e_por_lista():
    meta = {"doc_type": "lei", "base_id": "x"}
    assert casa_filtro(meta, {"doc_type": "lei"})
    assert casa_filtro(meta, {"doc_type": ["lei", "decreto"]})
    assert not casa_filtro(meta, {"doc_type": "edital"})
    assert not casa_filtro(meta, {"doc_type": "lei", "base_id": "y"})  # todos precisam casar
    assert casa_filtro(meta, None) and casa_filtro(meta, {})


def test_filtro_de_vigencia():
    antigo = {"data_publicacao": "2018-05-01", "data_vigencia_fim": "2022-12-31"}
    atual = {"data_publicacao": "2023-01-10"}
    futuro = {"data_publicacao": "2030-01-01"}
    sem_data = {}
    em = {"vigente_em": "2024-06-01"}
    assert not casa_filtro(antigo, em)  # terminou em 2022
    assert casa_filtro(atual, em)  # sem data de fim: segue em vigor
    assert not casa_filtro(futuro, em)  # ainda não existia
    assert casa_filtro(sem_data, em)  # sem data não dá para afirmar que saiu de vigor
    assert casa_filtro(antigo, {"vigente_em": "2020-01-01"})


# ----------------------------------------------------------------- BM25 e vetores


def test_bm25_acha_a_palavra_ignora_acento_e_respeita_k(tmp_path):
    indice, _ = _indice(tmp_path, CORPUS)
    achados = buscar_bm25(indice, "licitacao", k=10)  # sem acento
    assert [c.chunk_id for c in achados][:2] == ["c5", "c1"]  # o que repete mais vem primeiro
    assert all(c.nota > 0 for c in achados)
    assert len(buscar_bm25(indice, "licitação", k=1)) == 1


def test_bm25_consulta_sem_termo_util_devolve_vazio(tmp_path):
    indice, _ = _indice(tmp_path, CORPUS)
    assert buscar_bm25(indice, "de a o") == []
    assert buscar_bm25(indice, "palavra que nao existe em lugar nenhum") == []


def test_bm25_aplica_filtro(tmp_path):
    chunks = [_chunk("l1", "licitação na lei", doc_type="lei"), _chunk("e1", "licitação no edital", doc_type="edital")]
    indice, _ = _indice(tmp_path, chunks)
    assert [c.chunk_id for c in buscar_bm25(indice, "licitação", filtros={"doc_type": "edital"})] == ["e1"]


def test_busca_densa_acha_o_trecho_com_as_mesmas_palavras(tmp_path):
    indice, embedder = _indice(tmp_path, CORPUS)
    achados = buscar_denso(indice, embedder.embed_consulta(["modalidade pregão bens serviços comuns"])[0], k=3)
    assert achados[0].chunk_id == "c2"
    assert [c.nota for c in achados] == sorted((c.nota for c in achados), reverse=True)
    assert len(achados) == 3


def test_busca_densa_com_filtro_ainda_devolve_k_resultados(tmp_path):
    # O filtro tira quase todo mundo do topo; ainda assim a lista tem de vir completa.
    chunks = [_chunk(f"x{i}", f"palavra comum numero {i}", doc_type="edital") for i in range(20)]
    chunks.append(_chunk("alvo1", "outra coisa totalmente diferente aqui", doc_type="lei"))
    chunks.append(_chunk("alvo2", "mais uma coisa diferente aqui tambem", doc_type="lei"))
    indice, embedder = _indice(tmp_path, chunks)
    achados = buscar_denso(indice, embedder.embed_consulta(["palavra comum"])[0], k=2, filtros={"doc_type": "lei"})
    assert {c.chunk_id for c in achados} == {"alvo1", "alvo2"}


def test_busca_densa_com_filtro_de_vigencia(tmp_path):
    chunks = [
        _chunk("antigo", "dose do medicamento no protocolo", data_publicacao="2018-01-01", data_vigencia_fim="2022-12-31"),
        _chunk("atual", "dose do medicamento no protocolo", data_publicacao="2023-02-01"),
    ]
    indice, embedder = _indice(tmp_path, chunks)
    achados = buscar_denso(indice, embedder.embed_consulta(["dose do medicamento"])[0], k=5, filtros={"vigente_em": "2024-06-01"})
    assert [c.chunk_id for c in achados] == ["atual"]


# ----------------------------------------------------------------- RRF


def test_rrf_calcula_a_soma_de_um_sobre_k_mais_posicao():
    a = [Candidato("a", 9), Candidato("b", 8), Candidato("c", 7)]
    b = [Candidato("b", 0.9), Candidato("c", 0.8), Candidato("d", 0.7)]
    fundido = fundir_rrf({"bm25": a, "denso": b})
    notas = {cid: nota for cid, nota, _ in fundido}
    assert notas["b"] == pytest.approx(1 / 62 + 1 / 61)  # 2ª em uma, 1ª na outra
    assert notas["a"] == pytest.approx(1 / 61)
    assert notas["d"] == pytest.approx(1 / 63)
    assert [cid for cid, _, _ in fundido] == ["b", "c", "a", "d"]


def test_rrf_guarda_a_posicao_em_cada_lista():
    fundido = fundir_rrf({"bm25": [Candidato("a", 1), Candidato("b", 1)], "denso": [Candidato("b", 1)]})
    posicoes = {cid: pos for cid, _, pos in fundido}
    assert posicoes["a"] == {"bm25": 1}  # só uma lista
    assert posicoes["b"] == {"bm25": 2, "denso": 1}


def test_rrf_empate_e_desempatado_pelo_id():
    fundido = fundir_rrf({"x": [Candidato("z", 1)], "y": [Candidato("a", 1)]})
    assert [cid for cid, _, _ in fundido] == ["a", "z"]


# ----------------------------------------------------------------- híbrida


def test_busca_hibrida_devolve_texto_metadados_e_posicoes(tmp_path):
    indice, embedder = _indice(tmp_path, CORPUS)
    resultados = buscar_hibrido(indice, embedder, "dispensa de licitação", k=3)
    assert len(resultados) == 3
    assert resultados[0].chunk_id == "c1"
    primeiro = resultados[0]
    assert "dispensa de licitação" in primeiro.texto
    assert primeiro.metadata["artigo"] == "75"
    assert primeiro.bm25_rank == 1 and primeiro.denso_rank is not None
    assert [r.score_rrf for r in resultados] == sorted((r.score_rrf for r in resultados), reverse=True)


def test_trecho_so_do_vetor_entra_na_lista_mesmo_sem_palavra_em_comum(tmp_path):
    indice, embedder = _indice(tmp_path, CORPUS)
    resultados = buscar_hibrido(indice, embedder, "palavra inexistente zzz", k=5)
    assert resultados  # o BM25 não achou nada, mas a busca por vetores ainda responde
    assert all(r.bm25_rank is None for r in resultados)


def test_busca_hibrida_registra_os_tres_estagios_no_log(tmp_path):
    indice, embedder = _indice(tmp_path, CORPUS)
    logger = PipelineLogger(tmp_path / "logs", run_id="r1")
    buscar_hibrido(indice, embedder, "pregão", k=2, logger=logger)
    arquivo = next((tmp_path / "logs").glob("pipeline-*.jsonl"))
    linhas = [json.loads(l) for l in arquivo.read_text(encoding="utf-8").splitlines()]
    assert [l["stage"] for l in linhas] == ["retrieval_bm25", "retrieval_denso", "fusao_rrf"]
    assert {l["run_id"] for l in linhas} == {"r1"}
    assert linhas[1]["modelo"] == embedder.identity


def test_busca_hibrida_sem_logger_funciona(tmp_path):
    indice, embedder = _indice(tmp_path, CORPUS)
    assert buscar_hibrido(indice, embedder, "pregão", k=2)


# ----------------------------------------------------------------- por base


def test_base_grande_afoga_a_pequena_em_indice_unico_mas_nao_pesquisando_por_base(tmp_path):
    """O problema que fez os artigos da lei sumirem no projeto em equipe, reproduzido em miniatura."""
    embedder = EmbedderPalavras()
    grande = [_chunk(f"g{i}", f"contrato de fornecimento numero {i} valor global contratação objeto serviço", base="contratos") for i in range(60)]
    pequena = [_chunk("lei75", "Art. 75 é dispensável a contratação direta por valor", base="lei"), _chunk("lei76", "Art. 76 alienação de bens", base="lei")]
    pergunta = "contrato contratação valor objeto serviço fornecimento dispensável"

    # Tudo em um índice só: a lei compete com 60 contratos e fica de fora dos 3 primeiros.
    unico, _ = _indice(tmp_path, grande + pequena, "unico", embedder)
    assert "lei75" not in {r.chunk_id for r in buscar_hibrido(unico, embedder, pergunta, k=3)}

    # Uma base por índice: o melhor da lei compete com o melhor dos contratos.
    indices = {
        "contratos": _indice(tmp_path, grande, "grande", embedder)[0],
        "lei": _indice(tmp_path, pequena, "pequena", embedder)[0],
    }
    por_base = buscar_por_bases(indices, embedder, pergunta, k=3, k_por_base=5, garantir_por_base=1)
    assert "lei75" in {r.chunk_id for r in por_base}
    assert {r.metadata["base_id"] for r in por_base} >= {"contratos", "lei"}
    assert len(por_base) == 3
    assert [r.score_rrf for r in por_base] == sorted((r.score_rrf for r in por_base), reverse=True)


def test_garantia_de_representacao_inclui_a_base_mesmo_com_nota_baixa(tmp_path):
    embedder = EmbedderPalavras()
    forte = [_chunk(f"f{i}", "contrato contratação valor objeto serviço fornecimento", base="forte") for i in range(8)]
    fraca = [_chunk("w1", "documento sobre assunto bem diferente", base="fraca")]
    indices = {
        "forte": _indice(tmp_path, forte, "forte", embedder)[0],
        "fraca": _indice(tmp_path, fraca, "fraca", embedder)[0],
    }
    pergunta = "contrato contratação valor objeto serviço fornecimento"
    sem = buscar_por_bases(indices, embedder, pergunta, k=3, k_por_base=5, garantir_por_base=0)
    com = buscar_por_bases(indices, embedder, pergunta, k=3, k_por_base=5, garantir_por_base=1)
    assert "fraca" not in {r.metadata["base_id"] for r in sem}
    assert "fraca" in {r.metadata["base_id"] for r in com}
    assert len(com) == 3


def test_por_bases_respeita_k_final(tmp_path):
    embedder = EmbedderPalavras()
    indices = {
        "a": _indice(tmp_path, [_chunk(f"a{i}", f"texto alfa {i}", base="a") for i in range(6)], "a", embedder)[0],
        "b": _indice(tmp_path, [_chunk(f"b{i}", f"texto beta {i}", base="b") for i in range(6)], "b", embedder)[0],
    }
    assert len(buscar_por_bases(indices, embedder, "texto", k=4)) == 4
