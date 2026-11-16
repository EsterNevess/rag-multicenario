"""Redação de trechos que identificam alguém, aplicada na ingestão (antes de qualquer índice).

O mascaramento de `src.pii` cobre dado pessoal com forma fixa (CPF, telefone, e-mail, senha). Aqui ficam os trechos
cuja forma é uma frase, como a autocitação de um revisor ("como demonstrei em Fulano (2019)") ou o lugar onde ele
trabalha ("no laboratório X, onde atuo"). Cada cenário declara em `capacidades.yaml` as regras que valem para ele.

A redação é por frase: a frase inteira que casa com um padrão é trocada pela marca, e o resto do documento fica.
Ela só pega o que as regras conhecem. Não é prova de anonimato: por isso há outras camadas depois (entrada, prompt e saída).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

# Não corta depois de abreviação de tratamento ("Dr. Anderson Costa"): o nome ficaria numa frase sem o título que o denuncia.
_ABREVIACOES = ("dr", "dra", "sr", "sra", "srta", "prof", "profa", "sto", "sta")
_NAO_CORTA = "".join(rf"(?<!\b{a}\.)(?<!\b{a.capitalize()}\.)" for a in _ABREVIACOES)
_FIM_DE_FRASE = re.compile(rf"(?<=[.!?]){_NAO_CORTA}\s+")


@dataclass(frozen=True)
class RegraDeRedacao:
    categoria: str
    marca: str
    padroes: tuple[re.Pattern[str], ...]
    aplica_a: tuple[str, ...]  # tipos de documento (doc_type) em que a regra vale


def compilar(brutas: list[dict[str, Any]]) -> tuple[RegraDeRedacao, ...]:
    regras = []
    for r in brutas or []:
        regras.append(RegraDeRedacao(
            categoria=r["categoria"],
            marca=r.get("marca", "[TRECHO REMOVIDO]"),
            padroes=tuple(re.compile(p) for p in r["padroes"]),
            aplica_a=tuple(r.get("aplica_a") or ()),
        ))
    return tuple(regras)


def _sem_acento(texto: str) -> str:
    """Tira o acento e mantém a caixa: as regras escolhem com (?i) quando a caixa não importa."""
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


def redigir(texto: str, doc_type: str, regras: tuple[RegraDeRedacao, ...]) -> tuple[str, dict[str, int]]:
    """Devolve o texto com as frases identificadoras trocadas pela marca e quantas houve por categoria."""
    aplicaveis = [r for r in regras if not r.aplica_a or doc_type in r.aplica_a]
    if not aplicaveis:
        return texto, {}
    contagem: dict[str, int] = {}
    linhas = []
    for linha in texto.split("\n"):
        frases = _FIM_DE_FRASE.split(linha)
        saida = []
        for frase in frases:
            comparavel = _sem_acento(frase)
            regra = next((r for r in aplicaveis if any(p.search(comparavel) for p in r.padroes)), None)
            if regra:
                contagem[regra.categoria] = contagem.get(regra.categoria, 0) + 1
                saida.append(regra.marca)
            else:
                saida.append(frase)
        linhas.append(" ".join(saida) if len(frases) > 1 else saida[0])
    return "\n".join(linhas), contagem
