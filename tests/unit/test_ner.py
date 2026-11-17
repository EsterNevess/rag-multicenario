"""src/ner.py com tokenizador e sessão simulados: nada de modelo de verdade nem de download."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from src import ner
from src.ner import NerIndisponivel, ReconhecedorDeNomes, carregar_ner

ROTULOS = {0: "O", 1: "B-PESSOA", 2: "I-PESSOA", 3: "B-LOCAL", 4: "I-LOCAL"}


class TokenizadorFalso:
    """Cada palavra separada por espaço vira um token; um token entre (0, 0) no começo e no fim faz o papel de [CLS] e [SEP]."""

    def __init__(self) -> None:
        self.truncado: int | None = None

    def enable_truncation(self, max_length: int) -> None:
        self.truncado = max_length

    def encode(self, texto: str) -> SimpleNamespace:
        offsets = [(0, 0)]
        posicao = 0
        for palavra in texto.split(" "):
            offsets.append((posicao, posicao + len(palavra)))
            posicao += len(palavra) + 1
        offsets.append((0, 0))
        return SimpleNamespace(ids=list(range(len(offsets))), attention_mask=[1] * len(offsets), offsets=offsets)


class SessaoFalsa:
    def __init__(self, rotulos_por_palavra: list[int]) -> None:
        self._rotulos = [0, *rotulos_por_palavra, 0]

    def run(self, _saidas, entrada):
        assert set(entrada) <= {"input_ids", "attention_mask", "token_type_ids"}
        logits = np.zeros((1, len(self._rotulos), len(ROTULOS)), dtype=np.float32)
        for i, r in enumerate(self._rotulos):
            logits[0, i, r] = 5.0
        return [logits]


def reconhecedor(rotulos_por_palavra: list[int]) -> ReconhecedorDeNomes:
    return ReconhecedorDeNomes(SessaoFalsa(rotulos_por_palavra), TokenizadorFalso(), {"input_ids", "attention_mask", "token_type_ids"}, ROTULOS)


def test_nome_composto_vira_uma_entidade():
    r = reconhecedor([0, 1, 2, 0])  # "Ontem Maria Silva ligou"
    achadas = r.entidades("Ontem Maria Silva ligou")
    assert [(e.texto, e.categoria) for e in achadas] == [("Maria Silva", "PESSOA")]
    assert (achadas[0].inicio, achadas[0].fim) == (6, 17)


def test_dois_nomes_separados_sao_duas_entidades():
    r = reconhecedor([1, 0, 1])  # "Ana e Bia"
    assert [e.texto for e in r.entidades("Ana e Bia")] == ["Ana", "Bia"]


def test_b_seguido_de_b_com_espaco_abre_outra_entidade():
    r = reconhecedor([1, 1])  # "Ana Bia", cada uma marcada como começo
    assert [e.texto for e in r.entidades("Ana Bia")] == ["Ana", "Bia"]


def test_so_a_categoria_pedida_conta():
    r = reconhecedor([3, 4, 0, 1])  # "Rio Branco fica Ana"
    assert [e.texto for e in r.entidades("Rio Branco fica Ana")] == ["Ana"]
    assert [e.texto for e in r.entidades("Rio Branco fica Ana", categorias=("LOCAL",))] == ["Rio Branco"]


def test_pedaco_de_palavra_colado_continua_a_entidade():
    class Pedacos(TokenizadorFalso):
        def encode(self, texto):  # "Cl" + "audia" colados, depois "foi"
            return SimpleNamespace(ids=[0, 1, 2, 3, 4], attention_mask=[1] * 5, offsets=[(0, 0), (0, 2), (2, 7), (8, 11), (0, 0)])

    sessao = SessaoFalsa([1, 1, 0])  # o modelo marca o segundo pedaço com B, mas ele está colado ao primeiro
    r = ReconhecedorDeNomes(sessao, Pedacos(), {"input_ids", "attention_mask"}, ROTULOS)
    assert [e.texto for e in r.entidades("Claudia foi")] == ["Claudia"]


def test_sem_entidade_devolve_lista_vazia_e_texto_vazio_nao_chama_o_modelo():
    r = reconhecedor([0, 0])
    assert r.entidades("nada aqui") == []
    assert r.entidades("   ") == []


def test_mascarar_nomes_troca_pelo_fim_e_conta():
    r = reconhecedor([0, 1, 0, 1])  # "Fui Ana com Bia"
    texto, quantos = r.mascarar_nomes("Fui Ana com Bia")
    assert (texto, quantos) == ("Fui [NOME] com [NOME]", 2)


def test_o_tamanho_maximo_vai_ao_tokenizador():
    tok = TokenizadorFalso()
    ReconhecedorDeNomes(SessaoFalsa([]), tok, set(), ROTULOS, max_tokens=128)
    assert tok.truncado == 128


def test_sem_os_arquivos_levanta_ner_indisponivel(tmp_path):
    with pytest.raises(NerIndisponivel, match="faltam"):
        carregar_ner(tmp_path)


def test_a_marca_e_o_nome_dos_arquivos_estao_declarados():
    assert ner.MARCA_NOME == "[NOME]"
    assert "model_quantized.onnx" in ner.ARQUIVOS
