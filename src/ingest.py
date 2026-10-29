"""Ingestão da base normativa: transforma os documentos coletados em trechos (chunks).

    python -m src.ingest --scenario rj [--versao v2] [--base editais_normativo]

Lê uma versão do corpus (data/raw/<cenário>/<versão>/), extrai o texto de cada documento,
corta em trechos que respeitam a estrutura do texto e grava um JSONL em
data/processed/<cenário>/. Cada linha é um `Chunk` validado pelo schema.

Como corta:
- Leis e decretos: um trecho por artigo ("Art. 75"). Artigo longo é dividido em partes
  com sobreposição, nas fronteiras de parágrafo, inciso ou alínea.
- Editais: um trecho por seção numerada (por exemplo "7. DA PARTICIPAÇÃO"), com a mesma
  divisão para seções longas.

O que não faz: não lê tabelas como tabelas (o texto delas sai linearizado) e não faz OCR.
Dados pessoais (CPF, e-mail, telefone com sinal claro) são mascarados antes de gravar.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from statistics import mean
from typing import Any

import pymupdf

from src.fetch_corpus import (
    Pastas,
    RegistroDaVersao,
    carregar_manifesto,
    pastas_do_cenario,
    versoes_existentes,
)
from src.pii import mascarar_dados_pessoais
from src.schema import Chunk, ChunkMetadata, Natureza, Sensibilidade

MAX_CHARS = 1800
OVERLAP = 200

MESES = {
    "JANEIRO": 1, "FEVEREIRO": 2, "MARÇO": 3, "ABRIL": 4, "MAIO": 5, "JUNHO": 6,
    "JULHO": 7, "AGOSTO": 8, "SETEMBRO": 9, "OUTUBRO": 10, "NOVEMBRO": 11, "DEZEMBRO": 12,
}

# --------------------------------------------------------------------------- extração de texto


class _ExtratorHtml(HTMLParser):
    """HTML para texto. Ignora scripts, estilos e o texto riscado (passagens revogadas)."""

    BLOCOS = {"p", "div", "br", "tr", "li", "table", "blockquote", "center", "h1", "h2", "h3", "h4", "h5", "h6"}
    IGNORAR = {"script", "style", "head", "noscript"}
    RISCADO = {"strike", "s", "del"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.partes: list[str] = []
        self._ignorar = 0
        self._riscado = 0
        self.trechos_riscados = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self.IGNORAR:
            self._ignorar += 1
        elif tag in self.RISCADO:
            self._riscado += 1
            self.trechos_riscados += 1
        elif tag in self.BLOCOS:
            self.partes.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.IGNORAR:
            self._ignorar = max(0, self._ignorar - 1)
        elif tag in self.RISCADO:
            self._riscado = max(0, self._riscado - 1)
        elif tag in self.BLOCOS:
            # Um <strike> esquecido aberto não pode engolir o resto do documento.
            if tag in {"p", "div", "tr", "li"}:
                self._riscado = 0
            self.partes.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignorar and not self._riscado:
            # Em HTML, espaços e quebras de linha dentro do texto valem um espaço só. Só as tags de
            # bloco (<p>, <div>, <br>...) separam linhas. Ex.: um "Art." e um "75." separados por uma
            # quebra no código-fonte da página são o texto "Art. 75.".
            self.partes.append(re.sub(r"\s+", " ", data))  # \s já cobre o espaço não separável


def texto_de_html(conteudo: bytes) -> tuple[str, int]:
    """Decodifica (UTF-8 ou cp1252) e converte para texto. Devolve o texto e quantos trechos riscados ignorou."""
    try:
        html = conteudo.decode("utf-8")
    except UnicodeDecodeError:
        html = conteudo.decode("cp1252")  # o Planalto serve o HTML sem declarar a codificação
    extrator = _ExtratorHtml()
    extrator.feed(html)
    texto = "".join(extrator.partes).replace("\xa0", " ")
    linhas = [re.sub(r"[ \t]+", " ", linha).strip() for linha in texto.splitlines()]
    return "\n".join(linha for linha in linhas if linha), extrator.trechos_riscados


def _limpar_repetidos(paginas: list[str]) -> list[str]:
    """Remove cabeçalhos e rodapés: linhas que se repetem na maioria das páginas, e "3/9"."""
    if len(paginas) < 3:
        return paginas
    contagem: Counter[str] = Counter()
    for pagina in paginas:
        for linha in {l.strip() for l in pagina.splitlines() if l.strip()}:
            contagem[linha] += 1
    limite = max(3, int(len(paginas) * 0.6))
    repetidas = {linha for linha, n in contagem.items() if n >= limite}
    limpas = []
    for pagina in paginas:
        mantidas = [
            l for l in pagina.splitlines()
            if l.strip() not in repetidas and not re.fullmatch(r"\s*\d+\s*/\s*\d+\s*", l)
        ]
        limpas.append("\n".join(mantidas))
    return limpas


# Um decreto salvo pelo navegador traz, no topo de cada página, o site, a hora da impressão e a
# URL. Essas linhas mudam de uma página para outra (a página "1/9", "2/9"...), então a regra das
# linhas repetidas não basta: removemos também o padrão fixo desse cabeçalho.
_CABECALHO_NAVEGADOR = re.compile(
    r"^(Leis Estaduais|Rio de Janeiro|\d{2}/\d{2}/\d{2},\s*\d{1,2}:\d{2}.*|https?://\S+.*|Decreto \d+ \d{4} de .*)$"
)


def texto_de_pdf(caminho: Path) -> str:
    with pymupdf.open(caminho) as documento:
        # sort=True ordena os blocos pela posição na página. Sem isso, o PDF de um decreto entrega o
        # rótulo "Art. 5º" separado do texto do artigo, e os dois acabam em lugares diferentes.
        paginas = [pagina.get_text(sort=True) for pagina in documento]
    paginas = _limpar_repetidos(paginas)
    saida = []
    for pagina in paginas:
        for linha in pagina.splitlines():
            if not _CABECALHO_NAVEGADOR.match(linha.strip()):
                saida.append(linha)
    return "\n".join(saida)


# --------------------------------------------------------------------------- estrutura do texto

_INICIO_ESTRUTURAL = re.compile(
    r"^\s*(?:[“\"]?Art\.|§|Parágrafo único|[IVXLC]+\s*[-–.)]|[a-z]\)|\d{1,2}(?:\.\d{1,2})+\.?\s+[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ]|\d{1,2}\.\s+[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ]"
    r"|(?i:CAP[ÍI]TULO|SE[ÇC][ÃA]O|SUBSE[ÇC][ÃA]O|T[ÍI]TULO)\s+(?:[IVXLC]+|[ÚU]NICO|\d+)\b|ANEXO|[-•●])"
)


def juntar_linhas(texto: str) -> str:
    """Desfaz a quebra de linha do PDF: junta as linhas de um mesmo parágrafo.

    Uma nova linha só é mantida quando a seguinte começa uma estrutura (artigo, parágrafo,
    inciso, alínea, cabeçalho numerado).
    """
    paragrafos: list[str] = []
    fechado = False  # um cabeçalho de seção ("7. DA PARTICIPAÇÃO") nunca recebe o texto da linha seguinte
    for linha in texto.splitlines():
        linha = linha.strip()
        if not linha:
            continue
        if not paragrafos or fechado or _INICIO_ESTRUTURAL.match(linha):
            paragrafos.append(linha)
        elif paragrafos[-1].endswith("-") and linha[:1].islower():
            paragrafos[-1] = paragrafos[-1][:-1] + linha  # palavra hifenizada na quebra
        else:
            paragrafos[-1] += " " + linha
        fechado = _parece_cabecalho(linha)
    return "\n".join(paragrafos)


_ARTIGO = re.compile(r"^[“\"]?Art\.\s*(\d+)(?:[º°]|o(?=\s|\.))?(?:\s*-\s*([A-Z])\b)?\.?(?:\s|$)")
_HIERARQUIA = re.compile(r"^(T[ÍI]TULO|CAP[ÍI]TULO|SE[ÇC][ÃA]O|SUBSE[ÇC][ÃA]O)\s+([IVXLC]+|[ÚU]NICO|\d+)\b(.*)$", re.IGNORECASE)
_NIVEIS = {"TITULO": 0, "CAPITULO": 1, "SECAO": 2, "SUBSECAO": 3}


def _nivel(palavra: str) -> int:
    chave = palavra.upper().replace("Í", "I").replace("Ç", "C").replace("Ã", "A")
    return _NIVEIS[chave]


def dividir_texto(texto: str, max_chars: int = MAX_CHARS, overlap: int = OVERLAP) -> list[str]:
    """Divide um texto em partes de até `max_chars`, nas fronteiras de parágrafo.

    Cada parte começa repetindo o final da anterior (`overlap` caracteres, em fronteira de palavra),
    para que uma frase partida ao meio continue legível nas duas.
    """
    if len(texto) <= max_chars:
        return [texto]
    unidades: list[str] = []
    for paragrafo in texto.split("\n"):
        while len(paragrafo) > max_chars:  # parágrafo gigante: corta na última frase ou palavra
            corte = max(paragrafo.rfind(". ", 0, max_chars), paragrafo.rfind("; ", 0, max_chars))
            if corte < max_chars // 2:
                corte = paragrafo.rfind(" ", 0, max_chars)
            if corte <= 0:
                corte = max_chars
            unidades.append(paragrafo[: corte + 1].strip())
            paragrafo = paragrafo[corte + 1:].strip()
        if paragrafo:
            unidades.append(paragrafo)
    partes: list[str] = []
    atual = ""
    for unidade in unidades:
        if atual and len(atual) + 1 + len(unidade) > max_chars:
            partes.append(atual)
            cauda = atual[-overlap:]
            cauda = cauda[cauda.find(" ") + 1:] if " " in cauda else cauda
            atual = cauda + "\n" + unidade
        else:
            atual = atual + "\n" + unidade if atual else unidade
    if atual:
        partes.append(atual)
    return partes


@dataclass
class Secao:
    rotulo: str  # "Art. 75", "Preâmbulo", "7. DA PARTICIPAÇÃO"
    chave: str  # parte do chunk_id: "art75", "preambulo", "s007"
    texto: str
    hierarquia: list[str] = field(default_factory=list)
    artigo: str | None = None


def cortar_por_artigos(texto: str) -> list[Secao]:
    """Leis e decretos: um bloco por artigo, mais o preâmbulo, com a hierarquia vigente em cada um."""
    texto = juntar_linhas(texto)
    secoes: list[Secao] = []
    niveis: dict[int, str] = {}
    pendente: list[str] = []
    atual: Secao | None = None
    vistos: Counter[str] = Counter()

    def fechar() -> None:
        nonlocal atual
        if atual and atual.texto.strip():
            secoes.append(atual)
        atual = None

    for linha in texto.splitlines():
        h = _HIERARQUIA.match(linha)
        if h and len(linha) < 160:
            nivel = _nivel(h.group(1))
            niveis = {k: v for k, v in niveis.items() if k < nivel}  # um nível novo encerra os de baixo
            niveis[nivel] = linha.strip()  # o título do capítulo já vem na mesma linha (ver juntar_linhas)
            continue
        a = _ARTIGO.match(linha)
        if a:
            fechar()
            numero = a.group(1) + (f"-{a.group(2)}" if a.group(2) else "")
            vistos[numero] += 1
            chave = f"art{numero}" + (f"-r{vistos[numero]}" if vistos[numero] > 1 else "")
            atual = Secao(f"Art. {numero}", chave, linha, [niveis[k] for k in sorted(niveis)], numero)
            continue
        if atual is None:
            pendente.append(linha)
        else:
            atual.texto += "\n" + linha
    fechar()
    if pendente:
        secoes.insert(0, Secao("Preâmbulo", "preambulo", "\n".join(pendente)))
    return secoes


_CABECALHO_SECAO = re.compile(r"^(?:\d{1,2}(?:\.\d{1,2})+\.?|\d{1,2}\.)\s+[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ]")


def _parece_cabecalho(linha: str) -> bool:
    if len(linha) > 120 or not _CABECALHO_SECAO.match(linha):
        return False
    letras = [c for c in linha if c.isalpha()]
    return bool(letras) and sum(c.isupper() for c in letras) / len(letras) >= 0.6


def cortar_por_secoes(texto: str) -> list[Secao]:
    """Editais: um bloco por seção numerada em maiúsculas. O que vem antes é a "abertura"."""
    texto = juntar_linhas(texto)
    secoes: list[Secao] = []
    atual = Secao("Abertura", "s000", "")
    ordem = 0
    for linha in texto.splitlines():
        if _parece_cabecalho(linha):
            if atual.texto.strip():
                secoes.append(atual)
            ordem += 1
            atual = Secao(linha.strip(), f"s{ordem:03d}", linha, [])
        else:
            atual.texto += ("\n" if atual.texto else "") + linha
    if atual.texto.strip():
        secoes.append(atual)
    return secoes


# Itens numerados (resoluções sem artigos, instruções, chamadas): "II.1", "1.1" ou "3.2 –", sob seções "1 – Objeto"
# ou "II - DOS TERMOS". Os subitens (II.3.1, a), b)) ficam dentro do item que os contém.
_INICIO_DE_ITEM = re.compile(
    r"^\s*(?:(?:[IVX]+|\d{1,2})(?:\.\d{1,2})*\s*[-–.)]\s+\S|\d{1,2}(?:\.\d{1,2})+\s+\S|[a-z]\)\s|[-•●]\s|§)"
)
_ITEM_PRINCIPAL = re.compile(r"^((?:[IVX]+|\d{1,2})\.\d{1,2})(?!\.?\d)(?:\s*[-–.)]\s*\S|\s+[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ(\d])")
_REMISSAO = re.compile(r"\s*\(voltar ao sumário\)\s*$", re.IGNORECASE)
_SECAO_EM_MAIUSCULAS = re.compile(r"^(?:[IVX]+|\d{1,2})\s*[-–.]?\s+[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][A-ZÁÀÂÃÉÊÍÓÔÕÚÇ0-9 ,;:/()'\-–]{4,}$")
_SECAO_COM_TRAVESSAO = re.compile(r"^\d{1,2}\s*[-–]\s+[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][^.:;]{2,70}$")
_SUMARIO = re.compile(r"\.{4,}|…{2,}")


def _juntar_itens(texto: str) -> list[str]:
    """Uma linha por parágrafo: só uma linha que abre um item, uma seção ou uma alínea começa um parágrafo novo."""
    paragrafos: list[str] = []
    fechado = False  # título de seção não recebe a linha seguinte
    for linha in texto.splitlines():
        linha = _REMISSAO.sub("", linha.strip())
        if not linha or _SUMARIO.search(linha):  # sumário com pontilhado: ruído que duplicaria os títulos
            continue
        titulo = bool(_SECAO_EM_MAIUSCULAS.match(linha) or _SECAO_COM_TRAVESSAO.match(linha))
        if not paragrafos or fechado or titulo or _INICIO_DE_ITEM.match(linha):
            paragrafos.append(linha)
        elif paragrafos[-1].endswith("-") and linha[:1].islower():
            paragrafos[-1] = paragrafos[-1][:-1] + linha
        else:
            paragrafos[-1] += " " + linha
        fechado = titulo
    return paragrafos


def cortar_por_itens(texto: str) -> list[Secao]:
    """Um bloco por item principal (II.1, 1.1), com a seção em que ele está; o que vem antes é o preâmbulo."""
    secoes: list[Secao] = []
    secao_atual = ""
    atual = Secao("Preâmbulo", "preambulo", "")
    vistos: Counter[str] = Counter()
    ordem = 0

    def fechar() -> None:
        if atual.texto.strip():
            secoes.append(atual)

    for linha in _juntar_itens(texto):
        if _SECAO_EM_MAIUSCULAS.match(linha) or _SECAO_COM_TRAVESSAO.match(linha):
            fechar()
            ordem += 1
            secao_atual = linha
            atual = Secao(linha, f"s{ordem:02d}", linha, [])
            continue
        m = _ITEM_PRINCIPAL.match(linha)
        if m:
            fechar()
            numero = m.group(1)
            vistos[numero] += 1
            chave = "item-" + numero.lower().replace(".", "-") + (f"-r{vistos[numero]}" if vistos[numero] > 1 else "")
            atual = Secao(f"Item {numero}", chave, linha, [secao_atual] if secao_atual else [], numero)
            continue
        atual.texto += ("\n" if atual.texto else "") + linha
    fechar()
    return _sem_restos_de_sumario(secoes)


def _sem_restos_de_sumario(secoes: list[Secao]) -> list[Secao]:
    """Um item curto cujo número volta depois com texto de verdade é resto do sumário (linha quebrada sem pontilhado)."""
    numeros = Counter(s.artigo for s in secoes if s.artigo)
    saida: list[Secao] = []
    vistos: Counter[str] = Counter()
    for s in secoes:
        if s.artigo and numeros[s.artigo] > 1:
            vistos[s.artigo] += 1
            if vistos[s.artigo] < numeros[s.artigo] and len(s.texto) < 200:
                continue
        saida.append(s)
    return saida


# --------------------------------------------------------------------------- documentos e metadados


@dataclass
class Documento:
    base_id: str
    caminho: Path
    stem: str
    norma: str
    doc_type: str
    data_publicacao: str | None
    origem: str
    altera: str | None = None


def _stem(origem: str, arquivo: Path) -> str:
    m = re.search(r"/orgaos/(\d+)/compras/(\d+)/(\d+)/arquivos/(\d+)", origem)
    if m:
        return "pncp_{}_{}_{}_arq{}".format(*m.groups())
    return re.sub(r"[^a-z0-9]+", "-", arquivo.stem.lower()).strip("-")


def descobrir_documentos(manifesto: dict[str, Any], base_id: str, pastas: Pastas, versao: str) -> list[Documento]:
    """Casa cada item do manifesto com o arquivo coletado, usando o registro da coleta."""
    base = next(b for b in manifesto["bases"] if b["base_id"] == base_id)
    acesso = base["acesso"]
    registros = RegistroDaVersao(pastas.versao(versao)).ler()
    origens_locais_path = pastas.versao(versao) / "_origens_locais.json"
    origens_locais = json.loads(origens_locais_path.read_text(encoding="utf-8")) if origens_locais_path.exists() else {}
    raiz = pastas.dados.parent
    docs: list[Documento] = []
    for item in acesso.get("urls") or []:
        meta = item if isinstance(item, dict) else {"url": item}
        r = registros.get(meta["url"])
        if r and r.status == "ok":
            caminho = raiz / r.path
            docs.append(Documento(
                base_id, caminho, _stem(meta["url"], caminho), meta.get("rotulo") or caminho.stem,
                meta.get("doc_type", "documento"), meta.get("data_publicacao"), meta["url"],
            ))
    for item in (acesso.get("arquivos_locais") or {}).get("arquivos", []):
        r = registros.get(f"local:{item['arquivo']}")
        if r and r.status == "ok":
            caminho = raiz / r.path
            docs.append(Documento(
                base_id, caminho, _stem("", caminho), item.get("norma") or caminho.stem,
                item.get("doc_type", "documento"), item.get("data_publicacao"),
                origens_locais.get(f"local:{item['arquivo']}", item.get("origem", "")), item.get("altera"),
            ))
    return docs


def data_do_cabecalho(texto: str) -> str | None:
    """Lê a data do cabeçalho de um decreto: "DECRETO Nº 48.820 DE 27 DE NOVEMBRO DE 2023".

    Aceita também a forma sem o "DE" antes do mês ("13 DEZEMBRO DE 2023"), que aparece em um decreto real.
    """
    m = re.search(r"DECRETO\s+N[ºO°]\s*[\d.]+\s+DE\s+(\d{1,2})\s*[ºO°]?\s+(?:DE\s+)?([A-ZÇÃ]+)\s+DE\s+(\d{4})", texto.upper())
    if not m or m.group(2) not in MESES:
        return None
    return f"{int(m.group(3)):04d}-{MESES[m.group(2)]:02d}-{int(m.group(1)):02d}"


def texto_do_documento(doc: Documento) -> tuple[str, dict[str, Any]]:
    sufixo = doc.caminho.suffix.lower()
    if sufixo in (".html", ".htm"):
        texto, riscados = texto_de_html(doc.caminho.read_bytes())
        return texto, {"trechos_riscados_ignorados": riscados}
    if sufixo == ".pdf" or doc.caminho.read_bytes()[:5] == b"%PDF-":  # sem extensão confiável, o conteúdo decide
        return texto_de_pdf(doc.caminho), {}
    raise ValueError(f"formato não suportado: {doc.caminho.name}")


def cortar_documento(doc: Documento, texto: str) -> list[Secao]:
    """Escolhe o cortador pelo tipo do documento; normas de ética sem artigos (como a Resolução 466) caem nos itens."""
    if doc.doc_type == "edital":
        secoes = cortar_por_secoes(texto)
        return secoes if len(secoes) > 1 else cortar_por_itens(texto)  # chamada com títulos "1 – Objeto"
    if doc.doc_type == "instrucao":
        return cortar_por_itens(texto)
    secoes = cortar_por_artigos(texto)
    if doc.doc_type == "norma_etica" and sum(1 for s in secoes if s.artigo) < 3:
        return cortar_por_itens(texto)
    return secoes


def montar_chunks(
    doc: Documento,
    secoes: list[Secao],
    sensibilidade: Sensibilidade,
    natureza: Natureza = Natureza.NORMATIVA,
    dominios_institucionais: tuple[str, ...] = (),
) -> tuple[list[Chunk], int]:
    """Transforma as seções em `Chunk`s. Devolve também quantos continham dado pessoal (mascarado)."""
    chunks: list[Chunk] = []
    com_dado_pessoal = 0
    for secao in secoes:
        partes = dividir_texto(secao.texto)
        for i, parte in enumerate(partes, start=1):
            rotulo = secao.rotulo + (f" (parte {i}/{len(partes)})" if len(partes) > 1 else "")
            corpo = mascarar_dados_pessoais(parte, dominios_institucionais)
            pessoal = corpo != parte
            com_dado_pessoal += pessoal
            extras: dict[str, Any] = {
                "norma": doc.norma,
                "rotulo": rotulo,
                "parte": i,
                "partes": len(partes),
                "origem": doc.origem,
                "hierarquia": secao.hierarquia,
            }
            if secao.artigo:
                extras["artigo"] = secao.artigo
            if doc.altera:
                extras["altera"] = doc.altera
            metadata = ChunkMetadata(
                source_file=doc.caminho.name,
                doc_type=doc.doc_type,
                chunk_id=f"{doc.base_id}:{doc.stem}:{secao.chave}:p{i}",
                sensitivity=sensibilidade,
                base_id=doc.base_id,
                natureza=natureza,
                data_publicacao=doc.data_publicacao,
                contem_dado_pessoal=pessoal,
                **extras,
            )
            chunks.append(Chunk(texto=f"{doc.norma} - {rotulo}\n{corpo}", metadata=metadata))
    return chunks, com_dado_pessoal


# --------------------------------------------------------------------------- orquestração


def ingerir(manifesto_path: Path, versao: str, base_id: str, pastas: Pastas) -> dict[str, Any]:
    manifesto = carregar_manifesto(manifesto_path)
    base = next((b for b in manifesto["bases"] if b["base_id"] == base_id), None)
    if base is None:
        raise ValueError(f"base inexistente no manifesto: {base_id}")
    sensibilidade = Sensibilidade(base.get("sensitivity_default", "publico"))
    natureza = Natureza(base["natureza"])
    documentos = descobrir_documentos(manifesto, base_id, pastas, versao)
    if not documentos:
        raise FileNotFoundError(f"nenhum documento coletado para {base_id} na versão {versao}")

    todos: list[Chunk] = []
    por_documento = []
    avisos: list[str] = []
    for doc in documentos:
        texto, info = texto_do_documento(doc)
        if doc.doc_type == "decreto":
            lida = data_do_cabecalho(texto)
            if lida and doc.data_publicacao and lida != doc.data_publicacao:
                avisos.append(f"{doc.caminho.name}: data do manifesto {doc.data_publicacao} difere da do texto {lida}")
            if not doc.data_publicacao and lida:
                doc.data_publicacao = lida
        if not doc.data_publicacao:
            avisos.append(f"{doc.caminho.name}: sem data de publicação (o metadado de vigência ficará vazio)")
        secoes = cortar_documento(doc, texto)
        chunks, pessoais = montar_chunks(doc, secoes, sensibilidade, natureza, tuple(base.get("dominios_institucionais") or ()))
        if not chunks:
            avisos.append(f"{doc.caminho.name}: nenhum trecho gerado")
        tamanhos = [len(c.texto) for c in chunks]
        por_documento.append({
            "arquivo": doc.caminho.name,
            "norma": doc.norma,
            "doc_type": doc.doc_type,
            "data_publicacao": doc.data_publicacao,
            "secoes": len(secoes),
            "chunks": len(chunks),
            "chunks_com_dado_pessoal": pessoais,
            "chars_min_medio_max": [min(tamanhos), round(mean(tamanhos)), max(tamanhos)] if tamanhos else None,
            **info,
        })
        todos.extend(chunks)

    ids = [c.metadata.chunk_id for c in todos]
    repetidos = [i for i, n in Counter(ids).items() if n > 1]
    if repetidos:
        raise ValueError(f"chunk_id repetido: {repetidos[:5]}")

    saida = pastas.dados / "processed" / pastas.cenario_id
    saida.mkdir(parents=True, exist_ok=True)
    arquivo = saida / f"{versao}_{base_id}.jsonl"
    conteudo = "".join(json.dumps(c.model_dump(mode="json"), ensure_ascii=False, sort_keys=True) + "\n" for c in todos)
    arquivo.write_text(conteudo, encoding="utf-8")
    relatorio = {
        "scenario_id": pastas.cenario_id,
        "versao": versao,
        "base_id": base_id,
        "total_chunks": len(todos),
        "chunks_com_dado_pessoal": sum(d["chunks_com_dado_pessoal"] for d in por_documento),
        "sha256_do_jsonl": hashlib.sha256(conteudo.encode("utf-8")).hexdigest(),
        "documentos": por_documento,
        "avisos": avisos,
    }
    (saida / f"{versao}_{base_id}.relatorio.json").write_text(
        json.dumps(relatorio, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return relatorio


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m src.ingest", description=__doc__.split("\n\n")[0])
    p.add_argument("--scenario", required=True, choices=("rj", "saude", "pesquisa"))
    p.add_argument("--versao", help="padrão: a versão mais recente coletada")
    p.add_argument("--base", default="editais_normativo")
    p.add_argument("--manifest", type=Path)
    args = p.parse_args(argv)
    pastas = pastas_do_cenario(args.scenario)
    versao = args.versao or (versoes_existentes(pastas) or [None])[-1]
    if not versao:
        print("Erro: nenhuma versão coletada. Rode primeiro o fetch_corpus.", file=sys.stderr)
        return 2
    try:
        r = ingerir(args.manifest or pastas.cenario / "corpus_manifest.yaml", versao, args.base, pastas)
    except (ValueError, FileNotFoundError) as erro:
        print(f"Erro: {erro}", file=sys.stderr)
        return 2
    print(f"{r['versao']} / {r['base_id']}: {r['total_chunks']} trechos, {r['chunks_com_dado_pessoal']} com dado pessoal mascarado.")
    for d in r["documentos"]:
        print(f"  {d['chunks']:4} trechos  {d['doc_type']:8} {d['arquivo'][:60]}")
    for aviso in r["avisos"]:
        print(f"  AVISO: {aviso}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
