"""Comparação de versões de um PCDT, seção por seção, calculada em código.

O modelo de linguagem não compara versões bem a partir de trechos soltos: um assunto que aparece num trecho de uma versão
quase sempre também está na outra, em trecho que não veio (ver AVALIACAO.md, SA-02). Aqui a comparação é feita sobre o texto
inteiro de cada versão, antes de qualquer pergunta, e vira a tabela `pcdt_secao` da base normalizada.

O que a tabela diz, por par de versões consecutivas (anterior -> nova) de uma mesma doença:
  - quais seções existem só numa das versões e quais existem nas duas (casadas pelo título, sem a numeração);
  - nas seções das duas, quantas frases da nova não têm correspondente na anterior (frases novas) e quantas da anterior sumiram;
  - até três frases novas, como exemplo.

O que ela NÃO diz: o que mudou clinicamente. Uma frase "nova" pode ser a mesma ideia reescrita, e o PDF tem ruído (colunas, quebras).
Os números são evidência para quem lê, não um parecer.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

COLUNAS_SECAO = [
    "id", "doenca", "versao_nova", "data_nova", "versao_anterior", "data_anterior", "secao", "presenca",
    "caracteres_nova", "caracteres_anterior", "frases_novas", "frases_removidas", "frases_mantidas_pct", "exemplos_de_frases_novas",
]
LIMITE_DE_SIMILARIDADE_DA_FRASE = 0.7  # Jaccard das palavras
MIN_CARACTERES_DA_FRASE = 40
_FIM_DE_FRASE = re.compile(r"(?<=[.;:!?])\s+")
_NUMERACAO = re.compile(r"^\s*\d+(?:\.\d+)*\.?\s+")
_PARTE = re.compile(r"\s*\(parte \d+/\d+\)$")
_USO_PRIVADO = re.compile("[" + chr(0xE000) + "-" + chr(0xF8FF) + "]")


def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn").lower()


@dataclass
class Secao:
    titulo: str
    chave: str
    partes: list[str] = field(default_factory=list)

    @property
    def texto(self) -> str:
        return "\n".join(self.partes)


def chave_do_titulo(titulo: str) -> str:
    """Título sem numeração, acento, caixa e pontuação: '7    FÁRMACOS' e '8. Fármacos' dão a mesma chave."""
    t = _NUMERACAO.sub("", _sem_acento(titulo))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", t)).strip()


def _corpo(chunk: dict[str, Any]) -> str:
    texto = chunk.get("texto", "")
    return texto.split("\n", 1)[1] if "\n" in texto else texto


def secoes_da_versao(chunks: list[dict[str, Any]]) -> tuple[list[Secao], int]:
    """Seções na ordem do documento. Os trechos 'Item N.M' entram na seção titulada que os precede.

    Só vale a primeira ocorrência de cada título: alguns PDFs trazem, depois do protocolo, anexos com os mesmos títulos
    (introdução, tratamento), e misturá-los inflaria a seção. Devolve também quantos títulos repetidos foram ignorados.
    """
    secoes: list[Secao] = []
    vistos: dict[str, Secao] = {}
    atual: Secao | None = None
    ignoradas = 0
    for c in chunks:
        rotulo = _PARTE.sub("", (c.get("metadata", {}).get("rotulo") or "").strip())
        corpo = _USO_PRIVADO.sub(" ", _corpo(c))
        if rotulo.startswith("Item "):
            if atual is not None:
                atual.partes.append(corpo)
            continue
        chave = chave_do_titulo(rotulo)
        if not chave:
            continue
        if chave in vistos:
            atual = vistos[chave] if atual is vistos[chave] else None  # continuação do mesmo título (partes) ou repetição de anexo
            if atual is not None:
                atual.partes.append(corpo)
            elif chave not in {s.chave for s in secoes[-1:]}:
                ignoradas += 1
            continue
        atual = Secao(rotulo, chave, [corpo])
        vistos[chave] = atual
        secoes.append(atual)
    return secoes, ignoradas


def casar_secoes(novas: list[Secao], anteriores: list[Secao]) -> list[tuple[Secao | None, Secao | None]]:
    """Pares (nova, anterior), na ordem da nova e depois as só da anterior. Casa por título igual; depois por um conter o outro
    (≥ 12 caracteres: 'diagnostico e classificacao' dentro de 'diagnostico e classificacao de artrite reumatoide'); depois por semelhança ≥ 0,85."""
    livres = list(anteriores)
    pares: list[tuple[Secao | None, Secao | None]] = []

    def procurar(nova: Secao) -> Secao | None:
        for s in livres:
            if s.chave == nova.chave:
                return s
        for s in livres:
            menor, maior = sorted((s.chave, nova.chave), key=len)
            if len(menor) >= 12 and maior.startswith(menor):
                return s
        melhor = max(livres, key=lambda s: SequenceMatcher(None, s.chave, nova.chave).ratio(), default=None)
        if melhor is not None and SequenceMatcher(None, melhor.chave, nova.chave).ratio() >= 0.85:
            return melhor
        return None

    for nova in novas:
        par = procurar(nova)
        if par is not None:
            livres.remove(par)
        pares.append((nova, par))
    pares += [(None, s) for s in livres]
    return pares


def frases(texto: str) -> list[str]:
    saida = []
    for bloco in re.split(r"\n+", texto):
        for f in _FIM_DE_FRASE.split(bloco):
            f = re.sub(r"\s+", " ", f).strip()
            if len(f) >= MIN_CARACTERES_DA_FRASE:
                saida.append(f)
    return saida


def _palavras(frase: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z0-9]+", _sem_acento(frase)))


def _sem_correspondente(origem: list[str], alvo: list[str]) -> list[str]:
    """Frases de `origem` sem nenhuma frase parecida (Jaccard das palavras ≥ limite) em `alvo`."""
    conjuntos = [_palavras(f) for f in alvo]
    achadas: list[str] = []
    for f in origem:
        a = _palavras(f)
        if not a:
            continue
        if not any(len(a & b) / len(a | b) >= LIMITE_DE_SIMILARIDADE_DA_FRASE for b in conjuntos if b):
            achadas.append(f)
    return achadas


def comparar_versoes(nova: list[dict[str, Any]], anterior: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Linhas de `pcdt_secao` (sem id nem doença/versões) para um par de versões, e um resumo com os títulos repetidos ignorados."""
    s_nova, ign_nova = secoes_da_versao(nova)
    s_ant, ign_ant = secoes_da_versao(anterior)
    linhas: list[dict[str, Any]] = []
    for sn, sa in casar_secoes(s_nova, s_ant):
        if sn is not None and sa is not None:
            f_nova, f_ant = frases(sn.texto), frases(sa.texto)
            novas_f = _sem_correspondente(f_nova, f_ant)
            sumidas = _sem_correspondente(f_ant, f_nova)
            mantidas = 100.0 * (len(f_nova) - len(novas_f)) / len(f_nova) if f_nova else None
            linhas.append({
                "secao": sn.titulo, "presenca": "nas duas versões", "caracteres_nova": len(sn.texto), "caracteres_anterior": len(sa.texto),
                "frases_novas": len(novas_f), "frases_removidas": len(sumidas),
                "frases_mantidas_pct": None if mantidas is None else round(mantidas, 1),
                "exemplos_de_frases_novas": " | ".join(f[:160] for f in novas_f[:3]) or None,
            })
        elif sn is not None:
            linhas.append({"secao": sn.titulo, "presenca": "só na versão nova", "caracteres_nova": len(sn.texto), "caracteres_anterior": None,
                           "frases_novas": None, "frases_removidas": None, "frases_mantidas_pct": None, "exemplos_de_frases_novas": None})
        else:
            linhas.append({"secao": sa.titulo, "presenca": "só na versão anterior", "caracteres_nova": None, "caracteres_anterior": len(sa.texto),
                           "frases_novas": None, "frases_removidas": None, "frases_mantidas_pct": None, "exemplos_de_frases_novas": None})
    return linhas, {"titulos_repetidos_ignorados_nova": ign_nova, "titulos_repetidos_ignorados_anterior": ign_ant}


def tabela_de_secoes(chunks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """`pcdt_secao` para todos os pares consecutivos de versões de cada doença, e um relatório (doenças com uma só versão, ignorados)."""
    por_doenca: dict[str, dict[str, dict[str, Any]]] = {}
    for c in chunks:
        m = c.get("metadata", {})
        d = por_doenca.setdefault(m.get("doenca"), {})
        v = d.setdefault(m.get("versao"), {"data": m.get("data_publicacao"), "chunks": []})
        v["chunks"].append(c)
    linhas: list[dict[str, Any]] = []
    relatorio: dict[str, Any] = {"doencas_com_uma_so_versao": [], "pares": []}
    for doenca, versoes in por_doenca.items():
        ordenadas = sorted(versoes.items(), key=lambda kv: kv[1]["data"] or "")
        if len(ordenadas) < 2:
            relatorio["doencas_com_uma_so_versao"].append(doenca)
            continue
        for (v_ant, dados_ant), (v_nova, dados_nova) in zip(ordenadas, ordenadas[1:]):
            partes, resumo = comparar_versoes(dados_nova["chunks"], dados_ant["chunks"])
            for p in partes:
                linhas.append({"id": len(linhas) + 1, "doenca": doenca, "versao_nova": v_nova, "data_nova": dados_nova["data"],
                               "versao_anterior": v_ant, "data_anterior": dados_ant["data"], **p})
            relatorio["pares"].append({"doenca": doenca, "nova": v_nova, "anterior": v_ant, "secoes": len(partes), **resumo})
    return linhas, relatorio
