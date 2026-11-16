"""Avaliação da redação de dados pessoais e clínicos em texto novo.

Aplica à mão, frase a frase, o que a ingestão faz com um registro conversacional (a redação de frases identificadoras e depois o
mascaramento de documentos e contatos) a um conjunto de frases que não foi usado para escrever as regras
(`scenarios/<id>/eval/redacao_holdout.json`), e mede:

  recall       fração das frases sensíveis em que NENHUM dos valores listados sobrou no texto (por frase) e dos valores (por valor)
  falso alarme fração das frases comuns que a ingestão alterou

Não há modelo de linguagem: é determinístico. Uso: python -m src.avaliar_redacao --scenario saude
"""

from __future__ import annotations

import argparse
import json
import unicodedata
from pathlib import Path
from typing import Any

from src.capacidades import capacidades_do_cenario
from src.pii import mascarar_dados_pessoais
from src.redacao import compilar, redigir

RAIZ = Path(__file__).resolve().parent.parent


def _simples(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn").lower()


def carregar_itens(cenario: str, raiz: Path | None = None, conjunto: str = "redacao_holdout") -> list[dict[str, Any]]:
    arquivo = (raiz or RAIZ) / "scenarios" / cenario / "eval" / f"{conjunto}.json"
    return json.loads(arquivo.read_text(encoding="utf-8"))["itens"]


def processar(texto: str, regras: Any, doc_type: str = "ouvidoria") -> str:
    """Na ordem da ingestão: primeiro a redação das frases, depois o mascaramento de contatos e documentos."""
    redigido, _ = redigir(texto, doc_type, regras)
    return mascarar_dados_pessoais(redigido)


def avaliar(cenario: str, doc_type: str = "ouvidoria", conjunto: str = "redacao_holdout") -> dict[str, Any]:
    regras = compilar(list(capacidades_do_cenario(cenario).redacoes_na_ingestao))
    sensiveis, comuns = [], []
    for item in carregar_itens(cenario, conjunto=conjunto):
        saida = processar(item["texto"], regras, doc_type)
        if item["tipo"] == "sensivel":
            sobrou = [v for v in item["valores"] if _simples(v) in _simples(saida)]
            sensiveis.append({"id": item["id"], "texto": item["texto"], "saida": saida, "sobrou": sobrou, "valores": item["valores"]})
        else:
            comuns.append({"id": item["id"], "texto": item["texto"], "saida": saida, "alterada": saida != item["texto"]})
    valores_total = sum(len(s["valores"]) for s in sensiveis)
    valores_sobrando = sum(len(s["sobrou"]) for s in sensiveis)
    return {
        "cenario": cenario,
        "frases_sensiveis": len(sensiveis),
        "frases_protegidas": sum(not s["sobrou"] for s in sensiveis),
        "recall_por_frase": round(sum(not s["sobrou"] for s in sensiveis) / len(sensiveis), 3) if sensiveis else None,
        "valores_total": valores_total,
        "valores_protegidos": valores_total - valores_sobrando,
        "recall_por_valor": round((valores_total - valores_sobrando) / valores_total, 3) if valores_total else None,
        "frases_comuns": len(comuns),
        "frases_comuns_alteradas": sum(c["alterada"] for c in comuns),
        "sensiveis": sensiveis,
        "comuns": comuns,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m src.avaliar_redacao", description=__doc__.split("\n\n")[0])
    p.add_argument("--scenario", required=True, choices=("rj", "saude", "pesquisa"))
    p.add_argument("--conjunto", default="redacao_holdout", help="arquivo de frases em scenarios/<id>/eval/ (sem .json); padrão: redacao_holdout (conjunto A)")
    args = p.parse_args(argv)
    r = avaliar(args.scenario, conjunto=args.conjunto)
    sufixo = "" if args.conjunto == "redacao_holdout" else "-" + args.conjunto.removeprefix("redacao_holdout_")
    destino = RAIZ / "logs" / f"avaliacao-redacao-{args.scenario}{sufixo}.json"
    destino.parent.mkdir(exist_ok=True)
    destino.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{args.scenario}: frases sensíveis protegidas {r['frases_protegidas']}/{r['frases_sensiveis']} ({r['recall_por_frase']}); "
          f"valores protegidos {r['valores_protegidos']}/{r['valores_total']} ({r['recall_por_valor']}); "
          f"frases comuns alteradas {r['frases_comuns_alteradas']}/{r['frases_comuns']}")
    for s in r["sensiveis"]:
        if s["sobrou"]:
            print(f"  SOBROU {s['id']}: {s['sobrou']} | {s['saida']}")
    for c in r["comuns"]:
        if c["alterada"]:
            print(f"  ALTERADA {c['id']}: {c['saida']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
