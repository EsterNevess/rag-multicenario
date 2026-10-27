import pytest
from pydantic import ValidationError

from config import RAIZ, Settings


def _settings(**valores) -> Settings:
    # _env_file=None: o teste não depende de um .env que exista na máquina.
    return Settings(_env_file=None, **valores)


def test_cenario_padrao_e_a_pasta_do_cenario():
    config = _settings()
    assert config.scenario == "rj"
    assert config.scenario_dir == RAIZ / "scenarios" / "rj"
    assert config.scenario_dir.is_dir()


def test_cenarios_do_repositorio_existem():
    for cenario in ("rj", "saude", "pesquisa"):
        assert _settings(scenario=cenario).scenario_dir.is_dir()


def test_cenario_desconhecido_e_rejeitado():
    with pytest.raises(ValidationError):
        _settings(scenario="outro")


def test_provedor_desconhecido_e_rejeitado():
    with pytest.raises(ValidationError):
        _settings(embedding_provider="inventado")


def test_identidade_do_embedding_inclui_provedor_modelo_e_dimensao():
    config = _settings(
        embedding_provider="ollama", embedding_model="qwen3-embedding", embedding_dimensions=1024
    )
    assert config.embedding_identity == "ollama:qwen3-embedding:1024"


def test_chave_de_api_nao_aparece_no_texto_do_objeto():
    config = _settings(openai_api_key="sk-segredo-de-teste-123456789")
    assert "sk-segredo" not in repr(config)
    assert "sk-segredo" not in str(config)


def test_valor_invalido_nao_vaza_na_mensagem_de_erro():
    with pytest.raises(ValidationError) as erro:
        _settings(embedding_provider="sk-segredo-de-teste-123456789")
    assert "sk-segredo" not in str(erro.value)
