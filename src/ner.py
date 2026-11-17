"""Reconhecimento de nomes de pessoa (NER) para a redação de dados pessoais, sem PyTorch.

As regras de `src.redacao` só pegam o que conhecem: um nome fora de uma construção prevista ("a Cida", "o menino Davi") escapa.
Este módulo lê a frase com um modelo treinado e devolve os trechos que ele marca como pessoa. O modelo é um BERTimbau treinado
no HAREM (`rchuluc/bertimbau-large-ner-total-onnx`, MIT), na conversão ONNX quantizada em 8 bits, executada com `onnxruntime`;
o texto é tokenizado com a biblioteca `tokenizers`. Os arquivos ficam em `data/models/bertimbau-ner-onnx/` (fora do Git).

O módulo não faz download nenhum: sem os arquivos, `carregar_ner` levanta `NerIndisponivel`.
O HAREM é texto formal (jornais, entrevistas); em texto informal de ouvidoria o modelo erra, e AVALIACAO.md mede quanto.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PASTA_PADRAO = Path(__file__).resolve().parent.parent / "data" / "models" / "bertimbau-ner-onnx"
ARQUIVOS = ("model_quantized.onnx", "tokenizer.json", "config.json")
MAX_TOKENS = 512
MARCA_NOME = "[NOME]"


class NerIndisponivel(RuntimeError):
    """Faltam os arquivos do modelo ou as bibliotecas; a redação segue só com as regras."""


@dataclass(frozen=True)
class Entidade:
    inicio: int
    fim: int
    categoria: str
    texto: str


class ReconhecedorDeNomes:
    def __init__(self, sessao: Any, tokenizador: Any, entradas: set[str], rotulos: dict[int, str], max_tokens: int = MAX_TOKENS) -> None:
        self._sessao = sessao
        self._tokenizador = tokenizador
        self._entradas = entradas
        self._rotulos = rotulos
        tokenizador.enable_truncation(max_length=max_tokens)

    def entidades(self, texto: str, categorias: tuple[str, ...] = ("PESSOA",)) -> list[Entidade]:
        """Os trechos do texto que o modelo marca com uma das categorias, em ordem, com a posição (em caracteres) de cada um."""
        import numpy as np

        if not texto.strip():
            return []
        codificado = self._tokenizador.encode(texto)
        ids = np.array([codificado.ids], dtype=np.int64)
        entrada = {"input_ids": ids, "attention_mask": np.array([codificado.attention_mask], dtype=np.int64), "token_type_ids": np.zeros_like(ids)}
        logits = np.asarray(self._sessao.run(None, {k: v for k, v in entrada.items() if k in self._entradas})[0])[0]
        previstos = [self._rotulos[int(i)] for i in logits.argmax(axis=-1)]
        achadas: list[Entidade] = []
        aberta: list[int] | None = None  # [inicio, fim] da entidade em curso
        categoria_aberta = ""

        def fechar() -> None:
            if aberta is not None:
                achadas.append(Entidade(aberta[0], aberta[1], categoria_aberta, texto[aberta[0] : aberta[1]]))

        for rotulo, (ini, fim) in zip(previstos, codificado.offsets, strict=False):
            if ini == fim:  # [CLS], [SEP] e preenchimento
                continue
            prefixo, _, categoria = rotulo.partition("-")
            if categoria not in categorias:
                fechar()
                aberta = None
                continue
            # "I-" continua a entidade; um pedaço de palavra colado ao anterior também, mesmo que o modelo o marque com "B-".
            if aberta is not None and categoria == categoria_aberta and (prefixo == "I" or aberta[1] == ini):
                aberta[1] = fim
            else:
                fechar()
                aberta, categoria_aberta = [ini, fim], categoria
        fechar()
        return achadas

    def mascarar_nomes(self, texto: str, marca: str = MARCA_NOME) -> tuple[str, int]:
        """Troca cada nome de pessoa achado pela marca. Devolve o texto e quantos nomes foram trocados."""
        achadas = self.entidades(texto)
        for e in sorted(achadas, key=lambda x: x.inicio, reverse=True):
            texto = texto[: e.inicio] + marca + texto[e.fim :]
        return texto, len(achadas)


def carregar_ner(pasta: Path | None = None, max_tokens: int = MAX_TOKENS) -> ReconhecedorDeNomes:
    pasta = pasta or PASTA_PADRAO
    faltam = [a for a in ARQUIVOS if not (pasta / a).is_file()]
    if faltam:
        raise NerIndisponivel(f"faltam em {pasta}: {', '.join(faltam)}")
    try:
        import onnxruntime
        from tokenizers import Tokenizer
    except ImportError as erro:  # pragma: no cover - depende do ambiente
        raise NerIndisponivel(f"biblioteca ausente: {erro}") from erro
    rotulos = {int(i): r for i, r in json.loads((pasta / "config.json").read_text(encoding="utf-8"))["id2label"].items()}
    sessao = onnxruntime.InferenceSession(str(pasta / "model_quantized.onnx"), providers=["CPUExecutionProvider"])
    entradas = {i.name for i in sessao.get_inputs()}
    return ReconhecedorDeNomes(sessao, Tokenizer.from_file(str(pasta / "tokenizer.json")), entradas, rotulos, max_tokens)
