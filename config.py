"""Configuração central. Lê variáveis de ambiente e o arquivo .env (que nunca vai para o Git)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

RAIZ = Path(__file__).resolve().parent
CENARIOS_VALIDOS = ("rj", "saude", "pesquisa")
PROVEDORES_VALIDOS = ("openai", "ollama")


class Settings(BaseSettings):
    # hide_input_in_errors: se um valor for inválido, a mensagem de erro não repete o valor.
    # Sem isso, uma chave de API inválida poderia aparecer escrita em um log ou em um erro de teste.
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    scenario: str = "rj"
    database_url: str = "sqlite:///./app.db"

    llm_provider: str = "openai"
    llm_model: str = "gpt-4.1-mini"
    openai_api_key: SecretStr | None = None
    ollama_url: str = "http://localhost:11434/v1"

    # Embeddings: Ollama local por padrão (gratuito, sem chave, reproduzível). Um servidor
    # compartilhado pede lotes pequenos e pausa entre eles (por exemplo 50 e 10 s).
    embedding_provider: str = "ollama"
    embedding_model: str = "qwen3-embedding:0.6b"
    embedding_dimensions: int = 1024
    # Lote pequeno: em CPU (sem GPU), medimos ~0,4 trecho/s e o melhor rendimento com lotes de 4.
    embedding_batch_size: int = 4
    embedding_pause_seconds: float = 0.0

    log_dir: Path = Path("logs")

    @field_validator("scenario")
    @classmethod
    def _cenario_conhecido(cls, valor: str) -> str:
        if valor not in CENARIOS_VALIDOS:
            raise ValueError(f"cenário deve ser um de {CENARIOS_VALIDOS}")
        return valor

    @field_validator("llm_provider", "embedding_provider")
    @classmethod
    def _provedor_conhecido(cls, valor: str) -> str:
        if valor not in PROVEDORES_VALIDOS:
            raise ValueError(f"provedor deve ser um de {PROVEDORES_VALIDOS}")
        return valor

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
