"""Mascaramento de dados pessoais.

Usado em dois lugares: na ingestão (antes de indexar um texto) e nos logs (antes de gravar
uma pergunta). Assim um CPF, um telefone ou um e-mail pessoal nunca chega ao índice nem
a um arquivo de log.
"""

from __future__ import annotations

import re

# (?<!\d) e (?!\d) impedem que um pedaço de um número maior seja confundido com um CPF
# ou telefone, por exemplo dentro de um CNPJ ou do número de controle de um contrato.
_CPF = re.compile(r"(?<!\d)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\d)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Um número "NNNN-NNNN" sozinho é ambíguo: pode ser telefone, mas também "2025-2026",
# "Edital 1234-5678" ou "Processo 2026-0001", comuns em contratações. Por isso o telefone só é
# mascarado quando há um sinal claro: DDD entre parênteses, celular com 9 na frente (9NNNN-NNNN)
# ou uma palavra como "tel", "fone", "celular", "whatsapp" ou "contato" logo antes.
_TELEFONE = re.compile(
    r"(?<!\d)\(\d{2}\)\s?9?\d{4}-\d{4}(?!\d)"
    r"|(?<!\d)9\d{4}-\d{4}(?!\d)"
    r"|(?i:\b(?:tel(?:efone)?|fone|cel(?:ular)?|whats(?:app)?|contato)\b)\D{0,15}(?<!\d)\d{4}-\d{4}(?!\d)"
)

MARCA_CPF = "[CPF]"
MARCA_EMAIL = "[EMAIL]"
MARCA_TELEFONE = "[TELEFONE]"


def mascarar_dados_pessoais(texto: str) -> str:
    """Troca CPF, e-mail e telefone por marcas. CNPJ (pessoa jurídica) não é alterado."""
    texto = _EMAIL.sub(MARCA_EMAIL, texto)
    texto = _CPF.sub(MARCA_CPF, texto)
    texto = _TELEFONE.sub(MARCA_TELEFONE, texto)
    return texto


def contem_dado_pessoal(texto: str) -> bool:
    return mascarar_dados_pessoais(texto) != texto
