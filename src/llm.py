"""Cliente de geração: fala com qualquer servidor que implemente POST /chat/completions (OpenAI, Ollama)."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import requests

try:  # usa o repositório de certificados do sistema (necessário onde a rede troca o certificado, como em alguns Windows)
    import truststore

    truststore.inject_into_ssl()
except Exception:  # pragma: no cover - sem truststore, o requests usa o certifi
    pass

from config import Settings
from src.obs import Tokens, tokens_de_uso

Dormir = Callable[[float], None]
URL_OPENAI = "https://api.openai.com/v1"
URL_GROQ = "https://api.groq.com/openai/v1"


class LLMIndisponivel(RuntimeError):
    """O modelo de geração não pôde ser usado (sem chave, sem servidor, chave recusada, limite)."""


class PedidoGrandeDemais(LLMIndisponivel):
    """O provedor recusou o pedido pelo tamanho (HTTP 413, por exemplo o limite de tokens por pedido do plano gratuito da Groq).

    Repetir o mesmo pedido não adianta: quem chamou tem de mandar menos texto.
    """


@dataclass
class Completacao:
    texto: str
    tokens: Tokens
    modelo: str


class LLM(Protocol):
    modelo: str

    def completar(self, sistema: str, usuario: str, *, json: bool = False) -> Completacao: ...


class ClienteLLM:
    def __init__(
        self,
        provider: str,
        base_url: str,
        model: str,
        *,
        api_key: str | None = None,
        sessao: requests.Session | None = None,
        dormir: Dormir = time.sleep,
        tentativas: int = 3,
        timeout: float = 120.0,
        corpo_extra: dict[str, Any] | None = None,
    ):
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self.modelo = model
        self.api_key = api_key
        self.sessao = sessao or requests.Session()
        self.dormir = dormir
        self.tentativas = tentativas
        self.timeout = timeout
        self.corpo_extra = corpo_extra or {}  # campos a mais no pedido (por exemplo, reasoning_effort no Ollama)

    def completar(self, sistema: str, usuario: str, *, json: bool = False) -> Completacao:
        corpo: dict[str, Any] = {
            "model": self.modelo,
            "temperature": 0,
            "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": usuario}],
        }
        corpo.update(self.corpo_extra)
        if json:
            corpo["response_format"] = {"type": "json_object"}
        cabecalhos = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        ultimo = "sem resposta"
        for tentativa in range(1, self.tentativas + 1):
            pedido: float | None = None
            try:
                r = self.sessao.post(f"{self.base_url}/chat/completions", json=corpo, headers=cabecalhos, timeout=self.timeout)
            except requests.ConnectionError as erro:
                raise LLMIndisponivel(
                    f"O servidor do modelo não respondeu em {self.base_url}. "
                    + ("Confira se o Ollama está rodando." if self.provider == "ollama" else "Confira a conexão com a internet.")
                ) from erro
            except requests.Timeout:
                ultimo = "tempo esgotado"
            else:
                if r.ok:
                    dados = r.json()
                    try:
                        texto = dados["choices"][0]["message"]["content"] or ""
                    except (KeyError, IndexError, TypeError) as erro:
                        raise LLMIndisponivel("A resposta do modelo veio em um formato inesperado.") from erro
                    return Completacao(texto, tokens_de_uso(dados.get("usage")), dados.get("model") or self.modelo)
                if r.status_code in (401, 403):
                    raise LLMIndisponivel(f"O servidor recusou a chave de API (HTTP {r.status_code}). Confira a chave do provedor {self.provider} no .env.")
                if r.status_code == 413:
                    raise PedidoGrandeDemais("O pedido passou do limite de tamanho do provedor (HTTP 413).")
                if r.status_code == 404 and self.provider == "ollama":
                    raise LLMIndisponivel(f"O Ollama não tem o modelo {self.modelo}. Baixe com: ollama pull {self.modelo}")
                if r.status_code not in (429, 500, 502, 503, 504):
                    raise LLMIndisponivel(f"O servidor do modelo respondeu HTTP {r.status_code}.")
                ultimo = f"HTTP {r.status_code}"
                pedido = _espera_pedida(r)
            if tentativa < self.tentativas:
                self.dormir(pedido if pedido is not None else 2.0 * 2 ** (tentativa - 1))
        raise LLMIndisponivel(f"O modelo não respondeu depois de {self.tentativas} tentativas ({ultimo}).")


def _espera_pedida(resposta: Any) -> float | None:
    """Segundos que o servidor pediu para esperar (Retry-After), se forem poucos."""
    try:
        segundos = float(getattr(resposta, "headers", {}).get("Retry-After"))
    except (TypeError, ValueError):
        return None
    return segundos if 0 <= segundos <= 60 else None


def criar_llm(config: Settings | None = None, **opcoes: Any) -> ClienteLLM:
    config = config or Settings()
    if config.llm_provider == "openai":
        if config.openai_api_key is None:
            raise LLMIndisponivel(
                "Falta a chave da OpenAI. Coloque OPENAI_API_KEY=... no arquivo .env "
                "(ele nunca vai para o Git) ou use LLM_PROVIDER=ollama para um modelo local."
            )
        return ClienteLLM("openai", URL_OPENAI, config.modelo_llm, api_key=config.openai_api_key.get_secret_value(), **opcoes)
    if config.llm_provider == "groq":
        if config.groq_api_key is None:
            raise LLMIndisponivel(
                "Falta a chave da Groq. Crie uma gratuita em console.groq.com e coloque GROQ_API_KEY=... "
                "no arquivo .env (ele nunca vai para o Git)."
            )
        return ClienteLLM("groq", URL_GROQ, config.modelo_llm, api_key=config.groq_api_key.get_secret_value(), **opcoes)
    # Modelos que raciocinam (Qwen3) levam minutos numa pergunta simples em CPU se o raciocínio ficar ligado: o padrão o desliga.
    extra = {"reasoning_effort": config.ollama_reasoning_effort} if config.ollama_reasoning_effort else {}
    opcoes.setdefault("timeout", config.llm_timeout_seconds)
    return ClienteLLM("ollama", config.ollama_url, config.modelo_llm, corpo_extra=extra, **opcoes)
