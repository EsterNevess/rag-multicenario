"""Configuração central. Lê variáveis de ambiente e o arquivo .env (que nunca vai para o Git)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

RAIZ = Path(__file__).resolve().parent
CENARIOS_VALIDOS = ("rj", "saude", "pesquisa")
PROVEDORES_LLM = ("openai", "groq", "ollama")
PROVEDORES_EMBEDDING = ("openai", "ollama")  # a Groq não oferece embeddings
MODELO_PADRAO_DO_LLM = {"openai": "gpt-4.1-mini", "groq": "openai/gpt-oss-120b", "ollama": "qwen3:4b-instruct-2507-q4_K_M"}


class Settings(BaseSettings):
    # hide_input_in_errors: se um valor for inválido, a mensagem de erro não repete o valor.
    # Sem isso, uma chave de API inválida poderia aparecer escrita em um log ou em um erro de teste.
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    scenario: str = "rj"
    database_url: str = "sqlite:///./app.db"

    llm_provider: str = "openai"
    llm_model: str | None = None  # vazio: usa o modelo padrão do provedor
    openai_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    ollama_url: str = "http://localhost:11434/v1"
    ollama_reasoning_effort: str = "none"  # vazio: não manda o campo (para modelos que não o conhecem)
    llm_timeout_seconds: float = 120.0  # um modelo local em CPU pode precisar de mais

    # Embeddings: Ollama local por padrão (gratuito, sem chave, reproduzível). Um servidor
    # compartilhado pede lotes pequenos e pausa entre eles (por exemplo 50 e 10 s).
    embedding_provider: str = "ollama"
    embedding_model: str = "qwen3-embedding:0.6b"
    embedding_dimensions: int = 1024
    # Lote pequeno: em CPU (sem GPU), medimos ~0,4 trecho/s e o melhor rendimento com lotes de 4.
    embedding_batch_size: int = 4
    embedding_pause_seconds: float = 0.0

    # Rerank por cross-encoder (opcional, desligado): exige os arquivos do modelo em data/models (veja src/rerank.py).
    # Em CPU, com os padrões (10 candidatos, 256 tokens por trecho) custa ~4 s por busca; com 20 e 512, ~17 s (medido em AVALIACAO.md).
    rerank: bool = False
    rerank_candidatos: int = 10
    rerank_max_tokens: int = 256

    log_dir: Path = Path("logs")

    @field_validator("scenario")
    @classmethod
    def _cenario_conhecido(cls, valor: str) -> str:
        if valor not in CENARIOS_VALIDOS:
            raise ValueError(f"cenário deve ser um de {CENARIOS_VALIDOS}")
        return valor

    @field_validator("llm_provider")
    @classmethod
    def _provedor_de_geracao_conhecido(cls, valor: str) -> str:
        if valor not in PROVEDORES_LLM:
            raise ValueError(f"provedor deve ser um de {PROVEDORES_LLM}")
        return valor

    @field_validator("embedding_provider")
    @classmethod
    def _provedor_de_embedding_conhecido(cls, valor: str) -> str:
        if valor not in PROVEDORES_EMBEDDING:
            raise ValueError(f"provedor deve ser um de {PROVEDORES_EMBEDDING}")
        return valor

    @property
    def modelo_llm(self) -> str:
        return self.llm_model or MODELO_PADRAO_DO_LLM[self.llm_provider]

    @property
    def scenario_dir(self) -> Path:
        """Pasta com tudo o que é específico do cenário ativo."""
        return RAIZ / "scenarios" / self.scenario

    @property
    def embedding_identity(self) -> str:
        """Identificador gravado junto do índice: provedor, modelo e dimensão.

        Vetores de provedores, modelos ou dimensões diferentes não podem ser misturados
        no mesmo índice; o carregamento compara este texto e recusa um índice incompatível.
        """
        return f"{self.embedding_provider}:{self.embedding_model}:{self.embedding_dimensions}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
