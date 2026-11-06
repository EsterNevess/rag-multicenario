"""Mascaramento de dados pessoais.

Usado em dois lugares: na ingestão (antes de indexar um texto) e nos logs (antes de gravar
uma pergunta). Assim um CPF, um telefone ou um e-mail pessoal nunca chega ao índice nem
a um arquivo de log.

A regra é conservadora com o que é pessoal e cuidadosa para não apagar o que é conteúdo:
editais têm tabelas com códigos de produto de 11 dígitos, números de processo com hífen e
e-mails funcionais de órgãos públicos, e nada disso é dado pessoal.
"""

from __future__ import annotations

import re

# (?<!\d) e (?!\d) impedem que um pedaço de um número maior seja confundido com um CPF
# ou telefone, por exemplo dentro de um CNPJ ou do número de controle de um contrato.

# CPF com a pontuação típica (123.456.789-09): sinal forte, sempre mascarado.
_CPF_FORMATADO = re.compile(r"(?<!\d)\d{3}\.\d{3}\.\d{3}-\d{2}(?!\d)")
# CPF sem pontuação: 11 dígitos seguidos também são código de produto, de item, de processo.
# Só é mascarado se os dois dígitos verificadores fecharem (um número qualquer passa em ~1%).
_CPF_SOLTO = re.compile(r"(?<!\d)\d{11}(?!\d)")

_EMAIL = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")
# E-mail funcional de um órgão público é contato institucional, público por natureza.
_DOMINIO_INSTITUCIONAL = re.compile(r"\.(?:gov|leg|jus|mp|mil)\.br$", re.IGNORECASE)

# Um número "NNNN-NNNN" sozinho é ambíguo: pode ser telefone, mas também "2025-2026",
# "Edital 1234-5678" ou "Processo 2026-0001", comuns em contratações. Por isso o telefone só é
# mascarado quando há um sinal claro: DDD entre parênteses, celular com 9 na frente (9NNNN-NNNN)
# ou uma palavra como "tel", "fone", "celular", "whatsapp" ou "contato" logo antes.
_TELEFONE = re.compile(
    r"(?<!\d)\(\d{2}\)\s?9?\d{4}-\d{4}(?!\d)"
    r"|(?<!\d)9\d{4}-\d{4}(?!\d)"
    r"|(?i:\b(?:tel(?:efone)?|fone|cel(?:ular)?|whats(?:app)?|contato)\b)\D{0,15}(?<!\d)\d{4}-\d{4}(?!\d)"
)

# Credencial colada em texto ("a senha do sistema é 12345", "token: abc"): o valor nunca deve chegar ao índice.
_CREDENCIAL = re.compile(
    r"(?i)\b(senha|password|passwd|token|chave\s+de\s+acesso|api[\s-]?key)\b"
    r"((?:\s+(?:de\s+acesso|do\s+sistema|provis[óo]ria|tempor[áa]ria|inicial|de\s+rede))*)"
    r"(\s*[:=]\s*|\s+(?:é|eh)\s+(?=\S*[\d!@#$%&*_-]))\S+"
)
# Dado bancário de fornecedor: agência e conta, ou chave Pix.
_CONTA_BANCARIA = re.compile(
    r"(?i)\bag(?:[êe]ncia|\.)?\s*:?\s*\d{3,5}(?:-\d)?[\s,;]*(?:c/?c|conta(?:\s+corrente)?)\s*:?\s*\d{4,12}(?:-\d)?"
)
_PIX = re.compile(r"(?i)\bchave\s+pix\b\s*(?:é|eh|:|=)?\s*\S+")

MARCA_CPF = "[CPF]"
MARCA_CREDENCIAL = "[CREDENCIAL]"
MARCA_BANCARIO = "[DADO BANCÁRIO]"
MARCA_EMAIL = "[EMAIL]"
MARCA_TELEFONE = "[TELEFONE]"


def cpf_valido(texto: str) -> bool:
    """Confere os dois dígitos verificadores de um CPF (módulo 11)."""
    digitos = [int(c) for c in texto if c.isdigit()]
    if len(digitos) != 11 or len(set(digitos)) == 1:
        return False
    for tamanho in (9, 10):
        soma = sum(d * peso for d, peso in zip(digitos[:tamanho], range(tamanho + 1, 1, -1)))
        resto = (soma * 10) % 11 % 10
        if resto != digitos[tamanho]:
            return False
    return True


def _institucional(dominio: str, extras: tuple[str, ...]) -> bool:
    dominio = dominio.lower()
    return bool(_DOMINIO_INSTITUCIONAL.search(dominio)) or any(dominio == d or dominio.endswith("." + d) for d in extras)


def mascarar_dados_pessoais(texto: str, dominios_institucionais: tuple[str, ...] = ()) -> str:
    """Troca CPF, e-mail pessoal e telefone por marcas. CNPJ (pessoa jurídica) não é alterado.

    `dominios_institucionais`: domínios que o corpus declara como de órgãos (por exemplo `faperj.br`), além dos
    `.gov.br` e equivalentes; os e-mails deles são contato público e ficam no texto.
    """
    extras = tuple(d.lower().lstrip("@.") for d in dominios_institucionais)
    texto = _EMAIL.sub(lambda m: m.group(0) if _institucional(m.group(1), extras) else MARCA_EMAIL, texto)
    texto = _CPF_FORMATADO.sub(MARCA_CPF, texto)
    texto = _CPF_SOLTO.sub(lambda m: MARCA_CPF if cpf_valido(m.group()) else m.group(), texto)
    texto = _TELEFONE.sub(MARCA_TELEFONE, texto)
    texto = _CREDENCIAL.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{MARCA_CREDENCIAL}", texto)
    texto = _CONTA_BANCARIA.sub(MARCA_BANCARIO, texto)
    texto = _PIX.sub(MARCA_BANCARIO, texto)
    return texto


def contem_dado_pessoal(texto: str) -> bool:
    return mascarar_dados_pessoais(texto) != texto
