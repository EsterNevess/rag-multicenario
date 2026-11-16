"""Avaliação da resposta final: cada pergunta roda N vezes com o modelo e se mede o que muda de uma repetição para outra.

As perguntas ficam em `scenarios/<id>/eval/respostas.json` (com os marcadores já preenchidos). Medidas, por pergunta:

  rota        a rota escolhida bate com a esperada? (conferência grossa: tipo de evidência, não o plano exato)
  recusa      o sistema recusou quando devia, e só quando devia?
  numeros     os números calculados em SQL aparecem na resposta? (só onde há resposta numérica conhecida)
  estabilidade  as N repetições concordam na rota e na recusa?

Não há juiz automático de qualidade: o texto das respostas fica em `logs/` para leitura. A rodada anda uma repetição de
cada vez sobre todas as perguntas, para que um limite de uso do provedor corte as últimas repetições e não as últimas perguntas.

Uso: python -m src.avaliar_respostas --scenario saude [--repeticoes 3]
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parent.parent
PAUSA_ENTRE_CHAMADAS = 25  # s; o plano gratuito limita tokens por minuto
ESPERA_NO_LIMITE = 65


def numeros_da_resposta(texto: str) -> list[float]:
    achados = []
    for m in re.finditer(r"\d{1,3}(?:[.\s  ]\d{3})+(?:,\d+)?|\d+(?:,\d+)?", texto):
        bruto = re.sub(r"[.\s  ]", "", m.group(0)).replace(",", ".")
        try:
            achados.append(float(bruto))
        except ValueError:
            pass
    return achados


def aparece(esperado: float, texto: str, tolerancia: float = 0.005) -> bool:
    """O número está no texto, também como percentual (0,706 escrito como 70,6) ou como fração."""
    alvos = [esperado, esperado / 100, esperado * 100]
    return any(abs(n - a) <= tolerancia * max(abs(a), 1e-9) for n in numeros_da_resposta(texto) for a in alvos)


def tipo_de_evidencia(rotas: list[str]) -> set[str]:
    saida: set[str] = set()
    for r in rotas:
        if r == "documentos":
            saida |= {"normativa", "conversacional"}
        elif r in ("estruturada", "agregada"):
            saida.add(r)
        elif r == "hibrida":
            saida |= {"normativa", "conversacional", "estruturada", "agregada"}
    return saida


def rota_ok(esperado: list[str], rotas: list[str], recusou: bool) -> bool:
    if not esperado:
        return recusou
    return not recusou and bool(tipo_de_evidencia(rotas) & set(esperado))


def carregar_perguntas(cenario: str) -> list[dict[str, Any]]:
    return json.loads((RAIZ / "scenarios" / cenario / "eval" / "respostas.json").read_text(encoding="utf-8"))["perguntas"]


def numeros_esperados(banco: Path | None, consultas: list[str]) -> list[float]:
    if not consultas or banco is None:
        return []
    conexao = sqlite3.connect(f"file:{banco.as_posix()}?mode=ro", uri=True)
    try:
        return [float(conexao.execute(c).fetchone()[0]) for c in consultas]
    finally:
        conexao.close()


def estabilidade(execucoes: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [e for e in execucoes if "erro" not in e]
    return {
        "repeticoes_completas": len(ok),
        "mesma_rota": len({tuple(e["rotas"]) for e in ok}) <= 1 if ok else None,
        "mesma_recusa": len({e["recusa"] for e in ok}) <= 1 if ok else None,
        "respostas_distintas": len({e["resposta"] for e in ok}) if ok else None,
    }


def rodar(cenario: str, repeticoes: int, retomar: bool = False) -> dict[str, Any]:
    sys.path.insert(0, str(RAIZ))
    from config import Settings
    from src.fetch_corpus import pastas_do_cenario
    from src.generate import carregar_contexto, responder
    from src.router import localizar_banco

    pastas = pastas_do_cenario(cenario)
    ctx = carregar_contexto(Settings(scenario=cenario), pastas)
    try:
        banco = localizar_banco(pastas)
    except FileNotFoundError:
        banco = None
    perguntas = carregar_perguntas(cenario)
    for p in perguntas:
        p["_numeros"] = numeros_esperados(banco, p.get("numeros_sql") or [])
    execucoes: dict[str, list[dict[str, Any]]] = {p["id"]: [] for p in perguntas}
    interrompido = None
    destino = RAIZ / "logs" / f"avaliacao-respostas-{cenario}.json"
    destino.parent.mkdir(exist_ok=True)
    tokens_anteriores = {"entrada": 0, "saida": 0}
    if retomar and destino.exists():  # continua de onde parou: guarda o que deu certo e refaz o que falhou ou faltou
        antes = json.loads(destino.read_text(encoding="utf-8"))
        tokens_anteriores = antes["tokens"]
        for item in antes["itens"]:
            if item["id"] in execucoes:
                execucoes[item["id"]] = [e for e in item["execucoes"] if "erro" not in e]

    def salvar() -> dict[str, Any]:
        itens = []
        for p in perguntas:
            ex = execucoes[p["id"]]
            ok = [e for e in ex if "erro" not in e]
            itens.append({
                "id": p["id"], "pergunta": p["pergunta"], "esperado": p["esperado"], "execucoes": ex, "estabilidade": estabilidade(ex),
                "rota_ok": [e["rota_ok"] for e in ok], "numeros_ok": [e.get("numeros_ok") for e in ok if "numeros_ok" in e],
            })
        r = {"cenario": cenario, "repeticoes_pedidas": repeticoes, "interrompido": interrompido, "tokens": {"entrada": tokens_anteriores["entrada"] + ctx.tokens.prompt, "saida": tokens_anteriores["saida"] + ctx.tokens.completion}, "itens": itens}
        destino.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
        return r

    for rep in range(1, repeticoes + 1):
        for p in perguntas:
            if any(e["rep"] == rep for e in execucoes[p["id"]]):
                continue  # já feita numa rodada anterior
            t = time.time()
            ultima = None
            for tentativa in (1, 2):
                try:
                    r = responder(p["pergunta"], ctx)
                    rotas = [x.value for x in r.rotas]
                    e = {"rep": rep, "rotas": rotas, "recusa": r.is_refusal, "motivo": r.refusal_reason, "fontes": [f.rotulo for f in r.sources_used],
                         "resposta": r.answer, "rota_ok": rota_ok(p["esperado"], rotas, r.is_refusal), "s": round(time.time() - t, 1)}
                    if p["_numeros"]:
                        e["numeros_ok"] = {str(n): aparece(n, r.answer) for n in p["_numeros"]}
                    execucoes[p["id"]].append(e)
                    ultima = None
                    break
                except Exception as erro:  # noqa: BLE001
                    ultima = f"{type(erro).__name__}: {str(erro)[:160]}"
                    if "429" in ultima and tentativa == 1:
                        time.sleep(ESPERA_NO_LIMITE)
                        continue
                    break
            if ultima:
                execucoes[p["id"]].append({"rep": rep, "erro": ultima})
                if "429" in ultima:
                    interrompido = f"limite de uso do provedor na repetição {rep}, pergunta {p['id']}"
                    return salvar()
            salvar()
            print(f"[rep {rep}] {p['id']} rotas={execucoes[p['id']][-1].get('rotas')} recusa={execucoes[p['id']][-1].get('recusa')} {execucoes[p['id']][-1].get('erro', '')}", flush=True)
            if not execucoes[p["id"]][-1].get("erro") and execucoes[p["id"]][-1].get("rotas"):
                time.sleep(PAUSA_ENTRE_CHAMADAS)
    return salvar()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.avaliar_respostas", description=__doc__.split("\n\n")[0])
    ap.add_argument("--scenario", required=True, choices=("rj", "saude", "pesquisa"))
    ap.add_argument("--repeticoes", type=int, default=3)
    ap.add_argument("--retomar", action="store_true", help="continua a rodada gravada em logs/, refazendo só o que falta")
    args = ap.parse_args(argv)
    r = rodar(args.scenario, args.repeticoes, args.retomar)
    print(f"\n{args.scenario}: tokens {r['tokens']} | interrompido: {r['interrompido'] or 'não'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
