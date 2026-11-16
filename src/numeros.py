"""Conferência dos números de uma resposta contra a evidência.

O código já confere as citações (`[1]` precisa apontar para uma fonte que existe), mas um modelo pode citar a fonte certa e escrever um
número que a fonte não traz. Aqui cada número do texto da resposta é procurado na evidência recebida, aceitando as formas de escrever
o mesmo valor: separador de milhar (25.005, 25 005, 25005), vírgula ou ponto decimal (70,6 e 70.6), percentual de uma fração
(70,6% para 0,7059), arredondamento ao número de casas escrito, "milhão" e "mil", e as partes de uma data (2026-01-19, 19/01/2026).

O que a conferência NÃO faz: não confere o sentido ("25.005 é o total de quê?"), não valida contas (uma soma ou diferença que o modelo
calcula não está na evidência e aparece como não conferida, o que é o desejado) e ignora os inteiros de 0 a 10, que são contagens e
ordinais demais para valer a checagem.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

MAIOR_INTEIRO_IGNORADO = 10
_CITACAO = re.compile(r"\[\d+(?:\s*,\s*\d+)*\]")
_MARCADOR_DE_LISTA = re.compile(r"(?m)^\s*(?:\d{1,3}[.)]|[IVXLC]+\s*[–-]|[-•*])\s+")
_ESPACOS_DE_MILHAR = "   "
_NUMERO = re.compile(
    rf"(?<![\w.,])(?:\d{{1,3}}(?:[.{_ESPACOS_DE_MILHAR}]\d{{3}})+(?:,\d+)?|\d+(?:[.,]\d+)?)(?!\w)"
    r"(?:\s*(?P<escala>mil\b|milh(?:ão|ao|ões|oes)\b|bilh(?:ão|ao|ões|oes)\b))?",
    re.IGNORECASE,
)
_ESCALAS = {"mil": 1_000, "milhão": 1_000_000, "milhao": 1_000_000, "milhões": 1_000_000, "milhoes": 1_000_000,
            "bilhão": 1_000_000_000, "bilhao": 1_000_000_000, "bilhões": 1_000_000_000, "bilhoes": 1_000_000_000}

Interpretacao = tuple[float, int]  # (valor, casas decimais escritas)


def interpretacoes(token: str) -> list[Interpretacao]:
    """Os valores que um token pode representar. '25.005' é 25005 (ponto de milhar, 0 casas) ou 25,005 (decimal, 3 casas): as duas entram,
    cada uma com a sua precisão, para que 14.134 não passe por 14.133 só porque, lido como decimal, os dois diferem de 0,001."""
    t = token.strip()
    if any(c in t for c in _ESPACOS_DE_MILHAR):
        partes = re.split(f"[{_ESPACOS_DE_MILHAR}]", t)
        saida: list[Interpretacao] = []
        for p in partes:
            if re.fullmatch(r"\d+(?:,\d+)?", p):
                saida.append((float(p.replace(",", ".")), len(p.split(",")[1]) if "," in p else 0))
        juntos = "".join(partes)
        try:
            saida.append((float(juntos.replace(".", "").replace(",", ".")), len(juntos.split(",")[1]) if "," in juntos else 0))
        except ValueError:
            pass
        return saida
    if "," in t:  # formato brasileiro: ponto é milhar, vírgula é decimal
        return [(float(t.replace(".", "").replace(",", ".")), len(t.rsplit(",", 1)[1]))]
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", t):  # 25.005 ou 1.062.796: milhar; com um só ponto, também pode ser decimal de três casas
        saida = [(float(t.replace(".", "")), 0)]
        if t.count(".") == 1:
            saida.append((float(t), 3))
        return saida
    return [(float(t), len(t.split(".")[1]) if "." in t else 0)]


def extrair(texto: str) -> list[tuple[str, list[Interpretacao], float]]:
    """(token como escrito, interpretações, fator de escala) de cada número do texto, na ordem em que aparecem."""
    achados = []
    for m in _NUMERO.finditer(_MARCADOR_DE_LISTA.sub("", _CITACAO.sub(" ", texto))):
        token = m.group(0)
        escala = m.group("escala")
        numero = token[: len(token) - len(escala)].strip() if escala else token
        achados.append((token.strip(), interpretacoes(numero), float(_ESCALAS[escala.lower()]) if escala else 1.0))
    return achados


def _confere(valor: float, casas: int, fator: float, conhecidos: set[float]) -> bool:
    v = valor * fator
    tolerancia = 0.5 * 10 ** (-casas) * fator + 1e-9
    for e in conhecidos:
        if abs(v - e) <= tolerancia:
            return True
        # percentual de uma fração (70,6 para 0,7059) e a fração de um percentual: só sem escala e só para frações de verdade
        if fator == 1 and 0 < e <= 1 and abs(v - e * 100) <= tolerancia:
            return True
        if fator == 1 and 0 < v <= 1 and 1 < e <= 100 and abs(v * 100 - e) <= tolerancia * 100:
            return True
    return False


def numeros_nao_conferidos(resposta: str, evidencias: Iterable[str], pergunta: str = "") -> list[str]:
    """Os números da resposta que não aparecem na evidência nem na pergunta, como estão escritos e sem repetição."""
    conhecidos: set[float] = set()
    for texto in [*evidencias, pergunta]:
        for _, interp, fator in extrair(texto):
            conhecidos |= {v * fator for v, _ in interp} | {v for v, _ in interp}
    suspeitos: list[str] = []
    for token, interp, fator in extrair(resposta):
        if fator == 1 and all(c == 0 and v == int(v) and v <= MAIOR_INTEIRO_IGNORADO for v, c in interp):
            continue
        if any(_confere(v, c, fator, conhecidos) for v, c in interp):
            continue
        if token not in suspeitos:
            suspeitos.append(token)
    return suspeitos
