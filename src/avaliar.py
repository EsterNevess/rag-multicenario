"""Avaliação da recuperação: BM25, busca densa e fusão RRF comparadas em perguntas com resposta conhecida.

Cada pergunta de `scenarios/<id>/eval/retrieval_gold.json` declara o que é um trecho relevante por CRITÉRIO (expressões
regulares sobre o texto e os metadados do trecho), e não por uma lista de ids: assim o conjunto relevante é recalculado
sobre o índice real e continua válido quando a ingestão muda. Não há modelo de linguagem aqui: tudo é determinístico e
repetível, e cada sistema roda 3 vezes para confirmar isso.

Uso: python -m src.avaliar --scenario saude [--hoje AAAA-MM-DD]
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.index import IndiceCarregado
from src.retrieve import _metadados, buscar_bm25, buscar_denso, casa_filtro, fundir_rrf

RAIZ = Path(__file__).resolve().parent.parent
K_BUSCA = 50
REPETICOES = 3
SISTEMAS = ("bm25", "denso", "rrf", "rrf_k10", "rrf_sem_filtro")


def _simples(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)).lower()


@dataclass(frozen=True)
class Item:
    id: str
    pergunta: str
    base: str
    filtros: dict[str, Any]
    texto: tuple[str, ...]
    metadata: dict[str, str]


def carregar_gold(cenario: str, raiz: Path | None = None) -> list[Item]:
    arquivo = (raiz or RAIZ) / "scenarios" / cenario / "eval" / "retrieval_gold.json"
    itens = []
    for bruto in json.loads(arquivo.read_text(encoding="utf-8"))["itens"]:
        rel = bruto["relevante"]
        itens.append(Item(bruto["id"], bruto["pergunta"], bruto["base"], bruto.get("filtros") or {}, tuple(rel.get("texto") or ()), dict(rel.get("metadata") or {})))
    return itens


def trechos_relevantes(indice: IndiceCarregado, item: Item) -> set[str]:
    """Todos os trechos do índice que cumprem o critério do item (todas as expressões de texto e todos os metadados)."""
    padroes = [re.compile(_simples(p)) for p in item.texto]
    meta_padroes = {k: re.compile(_simples(v)) for k, v in item.metadata.items()}
    meta = _metadados(indice)
    conexao = sqlite3.connect(indice.caminho_fts)
    try:
        textos = dict(conexao.execute("SELECT chunk_id, texto FROM metadados"))
    finally:
        conexao.close()
    achados = set()
    for cid, texto in textos.items():
        if all(p.search(_simples(texto)) for p in padroes) and all(p.search(_simples(str(meta[cid].get(k, "")))) for k, p in meta_padroes.items()):
            achados.add(cid)
    return achados


def _filtros(item: Item, hoje: str, niveis: list[str]) -> dict[str, Any]:
    f = {k: (hoje if v == "$hoje" else v) for k, v in item.filtros.items()}
    f["sensitivity"] = niveis
    return f


def ranking(sistema: str, indice: IndiceCarregado, embedder: Any, item: Item, hoje: str, niveis: list[str]) -> list[str]:
    """Os chunk_id em ordem, do melhor ao pior, até K_BUSCA."""
    filtros = _filtros(item, hoje, niveis)
    if sistema == "rrf_sem_filtro":
        filtros = {"sensitivity": niveis}
    bm25 = buscar_bm25(indice, item.pergunta, K_BUSCA, filtros) if sistema != "denso" else []
    denso = buscar_denso(indice, embedder.embed_consulta([item.pergunta])[0], K_BUSCA, filtros) if sistema != "bm25" else []
    if sistema == "bm25":
        return [c.chunk_id for c in bm25]
    if sistema == "denso":
        return [c.chunk_id for c in denso]
    k_rrf = 10 if sistema == "rrf_k10" else 60
    return [cid for cid, _, _ in fundir_rrf({"bm25": bm25, "denso": denso}, k_rrf=k_rrf)]


def metricas(ordem: list[str], relevantes: set[str]) -> dict[str, float]:
    primeira = next((i for i, c in enumerate(ordem[:10], start=1) if c in relevantes), None)
    return {
        "hit@1": float(bool(ordem[:1]) and ordem[0] in relevantes),
        "hit@5": float(any(c in relevantes for c in ordem[:5])),
        "mrr@10": 1.0 / primeira if primeira else 0.0,
        "p@5": sum(c in relevantes for c in ordem[:5]) / 5,
    }


def avaliar(cenario: str, hoje: str | None = None) -> dict[str, Any]:
    sys.path.insert(0, str(RAIZ))
    from config import Settings
    from src.fetch_corpus import pastas_do_cenario
    from src.generate import carregar_contexto

    ctx = carregar_contexto(Settings(scenario=cenario), pastas_do_cenario(cenario))
    hoje = hoje or ctx.hoje
    niveis = list(ctx.cap.niveis_consultaveis)
    itens = carregar_gold(cenario)
    por_item: list[dict[str, Any]] = []
    invalidos: list[str] = []
    for item in itens:
        indice = ctx.indices.get(item.base)
        if indice is None:
            invalidos.append(f"{item.id}: base {item.base} sem índice")
            continue
        relevantes = trechos_relevantes(indice, item)
        meta = _metadados(indice)
        pool = {c for c in relevantes if casa_filtro(meta[c], _filtros(item, hoje, niveis))}  # relevantes que o filtro deixa passar
        if not relevantes:
            invalidos.append(f"{item.id}: nenhum trecho cumpre o critério")
            continue
        linha: dict[str, Any] = {"id": item.id, "pergunta": item.pergunta, "base": item.base, "relevantes": len(relevantes), "relevantes_apos_filtro": len(pool), "sistemas": {}}
        for sistema in SISTEMAS:
            rodadas, tempos = [], []
            for _ in range(REPETICOES):
                t = time.perf_counter()
                rodadas.append(ranking(sistema, indice, ctx.embedder, item, hoje, niveis))
                tempos.append(time.perf_counter() - t)
            linha["sistemas"][sistema] = {**metricas(rodadas[0], relevantes), "deterministico": all(r == rodadas[0] for r in rodadas), "ms": round(1000 * min(tempos))}
        por_item.append(linha)
    resumo: dict[str, dict[str, float]] = {}
    for sistema in SISTEMAS:
        valores = [l["sistemas"][sistema] for l in por_item]
        resumo[sistema] = {m: round(sum(v[m] for v in valores) / len(valores), 3) for m in ("hit@1", "hit@5", "mrr@10", "p@5")} if valores else {}
        if valores:
            resumo[sistema]["deterministico"] = all(v["deterministico"] for v in valores)
            resumo[sistema]["ms_mediano"] = sorted(v["ms"] for v in valores)[len(valores) // 2]
    return {"cenario": cenario, "hoje": hoje, "itens_validos": len(por_item), "itens_invalidos": invalidos, "resumo": resumo, "itens": por_item}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m src.avaliar", description=__doc__.split("\n\n")[0])
    p.add_argument("--scenario", required=True, choices=("rj", "saude", "pesquisa"))
    p.add_argument("--hoje", help="AAAA-MM-DD; padrão: hoje")
    args = p.parse_args(argv)
    r = avaliar(args.scenario, args.hoje)
    destino = RAIZ / "logs" / f"avaliacao-recuperacao-{args.scenario}.json"
    destino.parent.mkdir(exist_ok=True)
    destino.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{args.scenario}: {r['itens_validos']} perguntas válidas; inválidas: {r['itens_invalidos'] or 'nenhuma'}")
    print(f"{'sistema':16} {'hit@1':>6} {'hit@5':>6} {'mrr@10':>7} {'p@5':>6} {'ms':>6}  determinístico")
    for s, m in r["resumo"].items():
        if m:
            print(f"{s:16} {m['hit@1']:6.3f} {m['hit@5']:6.3f} {m['mrr@10']:7.3f} {m['p@5']:6.3f} {m['ms_mediano']:6}  {m['deterministico']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
