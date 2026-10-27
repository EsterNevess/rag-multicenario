"""Recuperação: busca por palavras (BM25), busca por significado (FAISS) e a fusão das duas (RRF).

Para cada pergunta:

1. BM25 (SQLite FTS5): acha os trechos que usam as mesmas palavras. É o que acerta um número de
   artigo, um CNPJ ou um nome próprio.
2. FAISS: acha os trechos com o mesmo sentido, mesmo com palavras diferentes.
3. RRF (Reciprocal Rank Fusion): junta as duas listas somando 1/(60 + posição) de cada uma. Só usa
   a posição, não a nota, então não é preciso comparar o BM25 com o cosseno.

Cada base é pesquisada separadamente e os resultados são juntados depois, com uma garantia mínima
de representação por base. Em um índice único, uma base grande afoga uma pequena.

O reranqueamento por cross-encoder (a etapa `rerank`) não está aqui: depende de um modelo próprio e
entra na fase de avaliação, onde a sua contribuição pode ser medida.
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import nullcontext
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import numpy as np

from src.index import Embedder, IndiceCarregado
from src.obs import PipelineLogger, Stage

K_RRF = 60

# Palavras de ligação que não ajudam a distinguir um trecho de outro. O BM25 já dá pouco peso a elas,
# mas retirá-las da consulta deixa a busca mais rápida e as listas mais limpas.
STOPWORDS = frozenset(
    """a o as os um uma uns umas de do da dos das em no na nos nas por para com sem sobre entre
    que e é ou se ao aos à às qual quais quando como onde quem foi são ser há mais já seu sua seus
    suas este esta estes estas isso essa esse essas esses lhe me meu minha nos nós eu você
    pelo pela pelos pelas até após ante também só muito pode podem deve devem""".split()
)


@dataclass
class Candidato:
    chunk_id: str
    nota: float


@dataclass
class Resultado:
    chunk_id: str
    texto: str
    metadata: dict[str, Any]
    score_rrf: float
    bm25_rank: int | None = None
    denso_rank: int | None = None
    bm25_nota: float | None = None
    denso_nota: float | None = None
    extras: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- consulta e filtros


def preparar_consulta_fts(texto: str) -> str | None:
    """Transforma a pergunta em uma consulta FTS5 segura: cada palavra entre aspas, unidas por OR.

    Entre aspas, a palavra vira um termo literal. Assim "AND", "NEAR", "*" ou parênteses escritos
    pelo usuário não são lidos como operadores (e não quebram a consulta).
    """
    tokens = [t for t in re.findall(r"\w+", texto.lower()) if t not in STOPWORDS]
    if not tokens:
        return None
    unicos = list(dict.fromkeys(tokens))
    return " OR ".join(f'"{t}"' for t in unicos)


def casa_filtro(metadata: dict[str, Any], filtros: dict[str, Any] | None) -> bool:
    """Todos os filtros precisam casar (E). Um valor que é lista casa com qualquer item dela.

    Chave especial `vigente_em` (AAAA-MM-DD): o trecho tem de ter sido publicado até essa data e
    não pode ter terminado a vigência antes dela. Trecho sem data de publicação é mantido, porque
    não dá para afirmar que está fora de vigência.
    """
    if not filtros:
        return True
    for chave, esperado in filtros.items():
        if chave == "vigente_em":
            publicado = metadata.get("data_publicacao")
            fim = metadata.get("data_vigencia_fim")
            if publicado and publicado > esperado:
                return False
            if fim and fim < esperado:
                return False
            continue
        valor = metadata.get(chave)
        aceitos = esperado if isinstance(esperado, (list, tuple, set)) else [esperado]
        if valor not in aceitos:
            return False
    return True


_CACHE_METADADOS: dict[tuple[str, int], dict[str, dict[str, Any]]] = {}


def _metadados(indice: IndiceCarregado) -> dict[str, dict[str, Any]]:
    """Metadados de todos os trechos do índice (sem o texto). Lidos uma vez e guardados em memória."""
    chave = (str(indice.caminho_fts), indice.caminho_fts.stat().st_mtime_ns)
    if chave not in _CACHE_METADADOS:
        conexao = sqlite3.connect(indice.caminho_fts)
        try:
            _CACHE_METADADOS[chave] = {
                chunk_id: json.loads(meta) for chunk_id, meta in conexao.execute("SELECT chunk_id, metadata FROM metadados")
            }
        finally:
            conexao.close()
    return _CACHE_METADADOS[chave]


def _textos(indice: IndiceCarregado, ids: list[str]) -> dict[str, str]:
    if not ids:
        return {}
    conexao = sqlite3.connect(indice.caminho_fts)
    try:
        marcas = ",".join("?" * len(ids))
        return dict(conexao.execute(f"SELECT chunk_id, texto FROM metadados WHERE chunk_id IN ({marcas})", ids))
    finally:
        conexao.close()


# --------------------------------------------------------------------------- as duas buscas


def buscar_bm25(
    indice: IndiceCarregado, consulta: str, k: int = 50, filtros: dict[str, Any] | None = None
) -> list[Candidato]:
    termo = preparar_consulta_fts(consulta)
    if termo is None:
        return []
    conexao = sqlite3.connect(indice.caminho_fts)
    try:
        # bm25() devolve um número menor para um resultado melhor (e negativo); invertemos o sinal
        # para a nota crescer com a relevância.
        linhas = conexao.execute(
            "SELECT chunk_id, bm25(trechos) FROM trechos WHERE trechos MATCH ? ORDER BY bm25(trechos)",
            (termo,),
        ).fetchall()
    finally:
        conexao.close()
    if filtros:
        meta = _metadados(indice)
        linhas = [(cid, nota) for cid, nota in linhas if casa_filtro(meta[cid], filtros)]
    return [Candidato(cid, -nota) for cid, nota in linhas[:k]]


def buscar_denso(
    indice: IndiceCarregado, vetor_consulta: np.ndarray, k: int = 50, filtros: dict[str, Any] | None = None
) -> list[Candidato]:
    """Busca exata por produto interno. Com filtro, examina todos os vetores e filtra depois,
    para que o filtro nunca deixe a lista com menos de k resultados quando há o que devolver."""
    consulta = np.asarray(vetor_consulta, dtype=np.float32).reshape(1, -1)
    quantos = indice.indice.ntotal if filtros else min(k, indice.indice.ntotal)
    notas, posicoes = indice.indice.search(consulta, quantos)
    meta = _metadados(indice) if filtros else None
    candidatos: list[Candidato] = []
    for nota, pos in zip(notas[0], posicoes[0]):
        if pos < 0:
            continue
        chunk_id = indice.chunk_ids[pos]
        if meta is not None and not casa_filtro(meta[chunk_id], filtros):
            continue
        candidatos.append(Candidato(chunk_id, float(nota)))
        if len(candidatos) == k:
            break
    return candidatos


# --------------------------------------------------------------------------- fusão


def fundir_rrf(listas: dict[str, list[Candidato]], k_rrf: int = K_RRF) -> list[tuple[str, float, dict[str, int]]]:
    """RRF: a nota de um trecho é a soma de 1/(k_rrf + posição) em cada lista em que aparece.

    Devolve (chunk_id, nota, {nome_da_lista: posição}) em ordem decrescente de nota. Em caso de empate,
    vale o chunk_id, para o resultado ser sempre o mesmo.
    """
    notas: dict[str, float] = {}
    posicoes: dict[str, dict[str, int]] = {}
    for nome, candidatos in listas.items():
        for posicao, candidato in enumerate(candidatos, start=1):
            notas[candidato.chunk_id] = notas.get(candidato.chunk_id, 0.0) + 1.0 / (k_rrf + posicao)
            posicoes.setdefault(candidato.chunk_id, {})[nome] = posicao
    ordenados = sorted(notas.items(), key=lambda item: (-item[1], item[0]))
    return [(cid, nota, posicoes[cid]) for cid, nota in ordenados]


def buscar_hibrido(
    indice: IndiceCarregado,
    embedder: Embedder,
    consulta: str,
    k: int = 5,
    filtros: dict[str, Any] | None = None,
    k_busca: int = 50,
    logger: PipelineLogger | None = None,
) -> list[Resultado]:
    """BM25 + FAISS + RRF em uma base. Devolve os k melhores, com o texto e a posição em cada busca."""

    def etapa(stage: Stage, entrada: str):
        # Sem logger, um contexto vazio aceita as atribuições abaixo e não registra nada.
        return logger.etapa(stage, input=entrada) if logger else nullcontext(SimpleNamespace())

    with etapa(Stage.RETRIEVAL_BM25, consulta) as e:
        bm25 = buscar_bm25(indice, consulta, k_busca, filtros)
        e.output = f"{len(bm25)} candidatos"
        e.extra = {"k": k_busca, "filtros": filtros or {}}
    with etapa(Stage.RETRIEVAL_DENSO, consulta) as e:
        denso = buscar_denso(indice, embedder.embed_consulta([consulta])[0], k_busca, filtros)
        e.output = f"{len(denso)} candidatos"
        e.modelo = embedder.identity
        e.extra = {"k": k_busca, "filtros": filtros or {}}
    with etapa(Stage.FUSAO_RRF, consulta) as e:
        fundidos = fundir_rrf({"bm25": bm25, "denso": denso})[:k]
        e.output = f"{len(fundidos)} resultados"
        e.extra = {"k_rrf": K_RRF, "k": k}

    notas_bm25 = {c.chunk_id: c.nota for c in bm25}
    notas_denso = {c.chunk_id: c.nota for c in denso}
    textos = _textos(indice, [cid for cid, _, _ in fundidos])
    meta = _metadados(indice)
    return [
        Resultado(
            chunk_id=cid,
            texto=textos[cid],
            metadata=meta[cid],
            score_rrf=nota,
            bm25_rank=pos.get("bm25"),
            denso_rank=pos.get("denso"),
            bm25_nota=notas_bm25.get(cid),
            denso_nota=notas_denso.get(cid),
        )
        for cid, nota, pos in fundidos
    ]


def buscar_por_bases(
    indices: dict[str, IndiceCarregado],
    embedder: Embedder,
    consulta: str,
    k: int = 5,
    k_por_base: int = 5,
    garantir_por_base: int = 1,
    filtros: dict[str, Any] | None = None,
    logger: PipelineLogger | None = None,
) -> list[Resultado]:
    """Pesquisa cada base separadamente e junta. Garante até `garantir_por_base` trechos de cada base
    que tenha resultado, para uma base grande não afogar uma pequena; o resto é preenchido pela nota RRF."""
    por_base = {
        base_id: buscar_hibrido(indice, embedder, consulta, k=k_por_base, filtros=filtros, logger=logger)
        for base_id, indice in indices.items()
    }
    garantidos: list[Resultado] = []
    for resultados in por_base.values():
        garantidos.extend(resultados[:garantir_por_base])
    restantes = [r for resultados in por_base.values() for r in resultados[garantir_por_base:]]
    restantes.sort(key=lambda r: (-r.score_rrf, r.chunk_id))
    escolhidos = garantidos[:k] if len(garantidos) >= k else garantidos + restantes[: k - len(garantidos)]
    escolhidos.sort(key=lambda r: (-r.score_rrf, r.chunk_id))
    return escolhidos
