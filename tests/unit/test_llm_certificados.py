"""src/llm.py usa o repositório de certificados do sistema, e continua importando se o truststore faltar."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

ARQUIVO = Path(__file__).resolve().parents[2] / "src" / "llm.py"


def test_ativa_o_truststore_ao_ser_importado(monkeypatch):
    chamadas = []
    monkeypatch.setitem(sys.modules, "truststore", SimpleNamespace(inject_into_ssl=lambda: chamadas.append(1)))
    runpy.run_path(str(ARQUIVO), run_name="llm_de_teste")  # executa numa cópia, sem trocar o módulo que os outros testes usam
    assert chamadas == [1]


def test_sem_truststore_o_modulo_continua_importando(monkeypatch):
    monkeypatch.setitem(sys.modules, "truststore", None)  # faz "import truststore" levantar ImportError
    espaco = runpy.run_path(str(ARQUIVO), run_name="llm_de_teste")
    assert "ClienteLLM" in espaco
