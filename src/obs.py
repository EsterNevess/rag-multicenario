"""Observabilidade: um log estruturado (JSONL) por estágio do pipeline.

Quando uma resposta sai errada, o log diz em que estágio ela começou a dar errado, quanto
tempo cada estágio levou e quantos tokens gastou. Uma linha por estágio, em
logs/pipeline-AAAA-MM-DD.jsonl.
"""

from __future__ import annotations

import json
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Iterator

from src.pii import mascarar_dados_pessoais

LIMITE_TEXTO_LOG = 500


class Stage(str, Enum):
    """Nomes fixos dos estágios. O mesmo nome vai para o campo `stage` do log."""

    GUARDRAIL = "guardrail"
    QUERY_ANALYSIS = "query_analysis"
    ROTEAMENTO = "roteamento"
    RETRIEVAL_BM25 = "retrieval_bm25"
    RETRIEVAL_DENSO = "retrieval_denso"
    FUSAO_RRF = "fusao_rrf"
    RERANK = "rerank"
    GERACAO = "geracao"
    VALIDACAO = "validacao"
    FEEDBACK = "feedback"


@dataclass
class Tokens:
    prompt: int = 0
    completion: int = 0

    @property
    def total(self) -> int:
        return self.prompt + self.completion

    def somar(self, outro: "Tokens") -> None:
        self.prompt += outro.prompt
        self.completion += outro.completion


def tokens_de_uso(uso: Any) -> Tokens:
    """Lê o campo `usage` de uma resposta compatível com a API da OpenAI.

    Aceita um objeto (uso.prompt_tokens) ou um dicionário. Se não houver `usage`, devolve zero.
    """
    if uso is None:
        return Tokens()
    if isinstance(uso, dict):
        return Tokens(int(uso.get("prompt_tokens") or 0), int(uso.get("completion_tokens") or 0))
    return Tokens(
        int(getattr(uso, "prompt_tokens", 0) or 0),
        int(getattr(uso, "completion_tokens", 0) or 0),
    )


def _preparar_texto(texto: str) -> str:
    """Mascara dados pessoais e limita o tamanho antes de gravar no log."""
    texto = mascarar_dados_pessoais(str(texto))
    if len(texto) > LIMITE_TEXTO_LOG:
        return texto[:LIMITE_TEXTO_LOG] + "…"
    return texto


@dataclass
class ContextoEtapa:
    """Preenchido pelo código de dentro de uma etapa; vira campos da linha de log."""

    output: str = ""
    modelo: str | None = None
    tokens: Tokens = field(default_factory=Tokens)
    extra: dict[str, Any] = field(default_factory=dict)


class PipelineLogger:
    def __init__(self, log_dir: Path | str, run_id: str | None = None, query_id: str | None = None):
        self.log_dir = Path(log_dir)
        self.run_id = run_id or uuid.uuid4().hex[:8]
        self.query_id = query_id or f"q_{uuid.uuid4().hex[:6]}"

    def _arquivo(self) -> Path:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        return self.log_dir / f"pipeline-{datetime.now().astimezone():%Y-%m-%d}.jsonl"

    def registrar(
        self,
        stage: Stage,
        status: str,
        latencia_ms: float,
        input: str = "",
        output: str = "",
        tokens: Tokens | None = None,
        modelo: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        tokens = tokens or Tokens()
        linha = {
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "run_id": self.run_id,
            "query_id": self.query_id,
            "stage": Stage(stage).value,
            "status": status,
            "latencia_ms": round(latencia_ms),
            "input": _preparar_texto(input),
            "output": _preparar_texto(output),
            "tokens": {"prompt": tokens.prompt, "completion": tokens.completion, "total": tokens.total},
            "modelo": modelo,
            "extra": extra or {},
        }
        with self._arquivo().open("a", encoding="utf-8") as arquivo:
            arquivo.write(json.dumps(linha, ensure_ascii=False) + "\n")
        return linha

    @contextmanager
    def etapa(self, stage: Stage, input: str = "") -> Iterator[ContextoEtapa]:
        """Mede e registra um estágio. Se der erro, registra status "erro" e relança a exceção."""
        contexto = ContextoEtapa()
        inicio = time.perf_counter()
        try:
            yield contexto
        except Exception as erro:
            self.registrar(
                stage,
                "erro",
                (time.perf_counter() - inicio) * 1000,
                input=input,
                output=f"{type(erro).__name__}: {erro}",
                tokens=contexto.tokens,
                modelo=contexto.modelo,
                extra=contexto.extra,
            )
            raise
        self.registrar(
            stage,
            "ok",
            (time.perf_counter() - inicio) * 1000,
            input=input,
            output=contexto.output,
            tokens=contexto.tokens,
            modelo=contexto.modelo,
            extra=contexto.extra,
        )
