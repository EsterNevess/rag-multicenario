"""Embedders falsos para os testes: determinísticos, sem rede e sem modelo."""

from __future__ import annotations

import hashlib
import re

import numpy as np


class EmbedderPalavras:
    """Cada palavra tem um vetor aleatório fixo; o texto é a soma normalizada das suas palavras.

    Textos que compartilham palavras ficam próximos, então a busca por vetores se comporta de um
    jeito previsível nos testes, sem precisar de um modelo de verdade.
    """

    def __init__(self, dimensions: int = 64, identity: str | None = None):
        self.dimensions = dimensions
        self.identity = identity or f"fake:palavras:{dimensions}"
        self.chamadas = 0
        self.textos: list[str] = []

    def _palavra(self, palavra: str) -> np.ndarray:
        semente = int(hashlib.sha256(palavra.encode()).hexdigest()[:12], 16)
        return np.random.default_rng(semente).normal(size=self.dimensions).astype(np.float32)

    def _vetor(self, texto: str) -> np.ndarray:
        palavras = re.findall(r"\w+", texto.lower())
        v = sum((self._palavra(p) for p in palavras), np.zeros(self.dimensions, dtype=np.float32))
        norma = np.linalg.norm(v)
        return (v / norma if norma else v).astype(np.float32)

    def embed_documentos(self, textos: list[str]) -> np.ndarray:
        self.chamadas += 1
        self.textos.extend(textos)
        return np.array([self._vetor(t) for t in textos])

    def embed_consulta(self, textos: list[str]) -> np.ndarray:
        return np.array([self._vetor(t) for t in textos])


class LLMRoteirizado:
    """Modelo de linguagem falso: devolve as respostas em ordem e guarda o que recebeu."""

    modelo = "fake-llm"

    def __init__(self, *respostas: str):
        self.respostas = list(respostas)
        self.chamadas: list[tuple[str, str, bool]] = []

    def completar(self, sistema, usuario, *, json=False):
        from src.llm import Completacao
        from src.obs import Tokens

        self.chamadas.append((sistema, usuario, json))
        if not self.respostas:
            raise AssertionError("o modelo foi chamado mais vezes do que o previsto")
        return Completacao(self.respostas.pop(0), Tokens(10, 5), self.modelo)
