"""Reranqueamento por cross-encoder (a etapa `rerank` do ARQUITETURA.md), sem PyTorch.

Reordena os melhores candidatos da fusão (BM25 + denso, via RRF) lendo a pergunta e cada trecho JUNTOS, o que a busca por vetor
não faz. O modelo é o `BAAI/bge-reranker-v2-m3` (multilíngue, Apache-2.0) na conversão ONNX quantizada em 8 bits
(`onnx-community/bge-reranker-v2-m3-ONNX`), executada com `onnxruntime`; o texto é tokenizado com a biblioteca `tokenizers`.
Os arquivos ficam em `data/models/bge-reranker-v2-m3-onnx/` (fora do Git) e são baixados à mão: veja `scripts/` ou o README.

O módulo não faz download nenhum: sem os arquivos, `carregar_reranker` levanta `RerankerIndisponivel` e o motor segue sem rerank.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

PASTA_PADRAO = Path(__file__).resolve().parent.parent / "data" / "models" / "bge-reranker-v2-m3-onnx"
ARQUIVOS = ("model_int8.onnx", "tokenizer.json")
MAX_TOKENS = 256  # padrão: 256 mantém quase todo o ganho de 512 por metade do custo (AVALIACAO.md); 512 é o máximo útil do modelo
TAMANHO_DO_LOTE = 8


class RerankerIndisponivel(RuntimeError):
    """Faltam os arquivos do modelo ou as bibliotecas; o motor deve seguir sem rerank."""


class Reranker:
    def __init__(self, sessao: Any, tokenizador: Any, entradas: set[str], max_tokens: int = MAX_TOKENS) -> None:
        self._sessao = sessao
        self._tokenizador = tokenizador
        self._entradas = entradas
        self.max_tokens = max_tokens
        tokenizador.enable_truncation(max_length=max_tokens)
        tokenizador.enable_padding()

    def pontuar(self, consulta: str, textos: list[str]) -> list[float]:
        """Uma nota por texto (o logit do modelo; maior = mais relevante), na mesma ordem de `textos`."""
        import numpy as np

        notas: list[float] = []
        for i in range(0, len(textos), TAMANHO_DO_LOTE):
            lote = textos[i : i + TAMANHO_DO_LOTE]
            codificados = self._tokenizador.encode_batch([(consulta, t) for t in lote])
            ids = np.array([c.ids for c in codificados], dtype=np.int64)
            mascara = np.array([c.attention_mask for c in codificados], dtype=np.int64)
            entrada = {"input_ids": ids, "attention_mask": mascara}
            if "token_type_ids" in self._entradas:
                entrada["token_type_ids"] = np.zeros_like(ids)
            saida = self._sessao.run(None, {k: v for k, v in entrada.items() if k in self._entradas})[0]
            notas.extend(float(x) for x in np.asarray(saida).reshape(len(lote), -1)[:, 0])
        return notas

    def reordenar(self, consulta: str, candidatos: list[tuple[str, str]]) -> list[str]:
        """`candidatos`: (id, texto) na ordem atual. Devolve os ids do mais para o menos relevante; empates mantêm a ordem recebida."""
        if not candidatos:
            return []
        notas = self.pontuar(consulta, [t for _, t in candidatos])
        ordem = sorted(range(len(candidatos)), key=lambda i: (-notas[i], i))
        return [candidatos[i][0] for i in ordem]


def probabilidade(nota: float) -> float:
    """O logit como probabilidade (0 a 1), só para exibir."""
    return 1.0 / (1.0 + math.exp(-nota))


def carregar_reranker(pasta: Path | None = None, max_tokens: int = MAX_TOKENS) -> Reranker:
    pasta = pasta or PASTA_PADRAO
    faltam = [a for a in ARQUIVOS if not (pasta / a).is_file()]
    if faltam:
        raise RerankerIndisponivel(f"faltam em {pasta}: {', '.join(faltam)}")
    try:
        import onnxruntime
        from tokenizers import Tokenizer
    except ImportError as erro:  # pragma: no cover - depende do ambiente
        raise RerankerIndisponivel(f"biblioteca ausente: {erro}") from erro
    sessao = onnxruntime.InferenceSession(str(pasta / "model_int8.onnx"), providers=["CPUExecutionProvider"])
    entradas = {i.name for i in sessao.get_inputs()}
    return Reranker(sessao, Tokenizer.from_file(str(pasta / "tokenizer.json")), entradas, max_tokens)
