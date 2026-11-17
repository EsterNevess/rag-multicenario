"""Ablação do custo do rerank: quantos candidatos reordenar (N) e quantos tokens ler de cada trecho.

O cross-encoder custa tempo proporcional ao número de trechos lidos e ao tamanho de cada um. Aqui, para cada pergunta, a fusão RRF dá a
ordem; o rerank pontua os 20 primeiros trechos uma vez por tamanho de leitura (256 e 512 tokens), e cada variante (N de 5 a 20) apenas
reordena os N primeiros com as notas já calculadas, o que dá comparações pareadas sem repetir o trabalho. O tempo de cada variante é
medido à parte, chamando o modelo de verdade em algumas perguntas.

Uso: python -m src.avaliar_rerank --scenario saude [--conjunto retrieval_gold_novas]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from src.avaliar import carregar_gold, metricas, ranking, trechos_relevantes
from src.rerank import RerankerIndisponivel, carregar_reranker
from src.retrieve import _textos

RAIZ = Path(__file__).resolve().parent.parent
N_MAXIMO = 20
VARIANTES_N = (5, 10, 15, 20)
TAMANHOS = (256, 512)
PERGUNTAS_PARA_MEDIR_TEMPO = 8


def nome_da_variante(n: int, tokens: int) -> str:
    return f"rerank_n{n}_t{tokens}"


def reordenar(ordem: list[str], notas: dict[str, float], n: int) -> list[str]:
    """Os `n` primeiros da fusão reordenados pelas notas (empate fica na ordem da fusão); o resto segue como estava."""
    topo = ordem[:n]
    posicao = {c: i for i, c in enumerate(topo)}
    return sorted(topo, key=lambda c: (-notas[c], posicao[c])) + ordem[n:]


def medir_tempo(reranker: Any, consulta: str, textos: list[str], n: int) -> float:
    inicio = time.perf_counter()
    reranker.pontuar(consulta, textos[:n])
    return 1000 * (time.perf_counter() - inicio)


def avaliar(
    cenario: str, hoje: str | None = None, conjunto: str = "retrieval_gold", rerankers: dict[int, Any] | None = None, medir_tempo_em: int = PERGUNTAS_PARA_MEDIR_TEMPO,
) -> dict[str, Any]:
    sys.path.insert(0, str(RAIZ))
    from config import Settings
    from src.fetch_corpus import pastas_do_cenario
    from src.generate import carregar_contexto

    if rerankers is None:
        rerankers = {t: carregar_reranker(max_tokens=t) for t in TAMANHOS}
    ctx = carregar_contexto(Settings(scenario=cenario), pastas_do_cenario(cenario))
    hoje = hoje or ctx.hoje
    niveis = list(ctx.cap.niveis_consultaveis)
    por_item: list[dict[str, Any]] = []
    invalidos: list[str] = []
    tempos: dict[str, list[float]] = {nome_da_variante(n, t): [] for n in VARIANTES_N for t in TAMANHOS}
    for item in carregar_gold(cenario, conjunto=conjunto):
        indice = ctx.indices.get(item.base)
        if indice is None:
            invalidos.append(f"{item.id}: base {item.base} sem índice")
            continue
        relevantes = trechos_relevantes(indice, item)
        if not relevantes:
            invalidos.append(f"{item.id}: nenhum trecho cumpre o critério")
            continue
        ordem = ranking("rrf", indice, ctx.embedder, item, hoje, niveis)
        topo = ordem[:N_MAXIMO]
        textos = _textos(indice, topo)
        lista = [textos[c] for c in topo]
        notas = {t: dict(zip(topo, rerankers[t].pontuar(item.pergunta, lista), strict=False)) for t in TAMANHOS}
        linha: dict[str, Any] = {"id": item.id, "pergunta": item.pergunta, "relevantes": len(relevantes), "candidatos": len(topo), "sistemas": {"rrf": metricas(ordem, relevantes)}}
        for t in TAMANHOS:
            for n in VARIANTES_N:
                linha["sistemas"][nome_da_variante(n, t)] = metricas(reordenar(ordem, notas[t], min(n, len(topo))), relevantes)
        if len(por_item) < medir_tempo_em and len(topo) == N_MAXIMO:
            for t in TAMANHOS:
                for n in VARIANTES_N:
                    tempos[nome_da_variante(n, t)].append(medir_tempo(rerankers[t], item.pergunta, lista, n))
        por_item.append(linha)
    resumo: dict[str, dict[str, float]] = {}
    for sistema in ["rrf"] + list(tempos):
        valores = [l["sistemas"][sistema] for l in por_item]
        resumo[sistema] = {m: round(sum(v[m] for v in valores) / len(valores), 3) for m in ("hit@1", "hit@5", "mrr@10", "p@5")} if valores else {}
        if sistema in tempos and tempos[sistema]:
            resumo[sistema]["ms_mediano"] = round(statistics.median(tempos[sistema]))
    return {"cenario": cenario, "hoje": hoje, "itens_validos": len(por_item), "itens_invalidos": invalidos, "resumo": resumo, "itens": por_item}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m src.avaliar_rerank", description=__doc__.split("\n\n")[0])
    p.add_argument("--scenario", required=True, choices=("rj", "saude", "pesquisa"))
    p.add_argument("--hoje", help="AAAA-MM-DD; padrão: hoje")
    p.add_argument("--conjunto", default="retrieval_gold", help="arquivo de perguntas em scenarios/<id>/eval/ (sem .json)")
    p.add_argument("--tempo", type=int, default=PERGUNTAS_PARA_MEDIR_TEMPO, help="em quantas perguntas medir o tempo de cada variante (0: não mede)")
    args = p.parse_args(argv)
    try:
        r = avaliar(args.scenario, args.hoje, args.conjunto, medir_tempo_em=args.tempo)
    except RerankerIndisponivel as erro:
        print(f"Erro: {erro}", file=sys.stderr)
        return 2
    sufixo = "" if args.conjunto == "retrieval_gold" else "-" + args.conjunto.removeprefix("retrieval_gold_")
    destino = RAIZ / "logs" / f"avaliacao-rerank-{args.scenario}{sufixo}.json"
    destino.parent.mkdir(exist_ok=True)
    destino.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{args.scenario}: {r['itens_validos']} perguntas válidas; inválidas: {r['itens_invalidos'] or 'nenhuma'}")
    print(f"{'variante':16} {'hit@1':>6} {'hit@5':>6} {'mrr@10':>7} {'p@5':>6} {'ms':>7}")
    for nome, m in r["resumo"].items():
        if m:
            print(f"{nome:16} {m['hit@1']:6.3f} {m['hit@5']:6.3f} {m['mrr@10']:7.3f} {m['p@5']:6.3f} {m.get('ms_mediano', ''):>7}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
