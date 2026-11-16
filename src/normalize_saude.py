"""Normalização da saúde: a RENAME 2024 (PDF de tabelas) e o catálogo de versões de PCDT viram tabelas SQL.

Gera `data/normalized/saude/<versão>.sqlite` com:

  medicamento   uma linha por medicamento + concentração + forma farmacêutica (Apêndice A da RENAME, por classificação ATC)
  pcdt_versao   uma linha por versão de PCDT do corpus, com a data de publicação e a de fim da vigência
  pcdt_medicamento  uma linha por medicamento da seção "Fármacos" de cada versão de PCDT, com a marca de constar ou não na RENAME
  metadados     versão, cenário e o relatório da normalização

mais um `<versão>.relatorio.json`. O Apêndice B da RENAME (os mesmos medicamentos reagrupados por anexo) não é lido: o
Apêndice A já traz todos os medicamentos com o código ATC. As colunas são as que `scenarios/saude/capacidades.yaml` declara.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

import pymupdf

from src.fetch_corpus import Pastas, carregar_manifesto
from src.fsutil import com_repeticao

COLUNAS_MEDICAMENTO = ["id", "dcb", "concentracao", "forma", "componente", "atc", "aware", "grupo_atc", "grupo_atc_nome", "pagina", "ano_edicao"]
COLUNAS_PCDT = ["id", "doenca", "versao", "rotulo", "data_publicacao", "data_vigencia_fim", "situacao"]
COLUNAS_PCDT_MEDICAMENTO = ["id", "doenca", "versao", "data_publicacao", "situacao", "medicamento", "apresentacao", "classe", "na_rename", "dcb_na_rename"]
TIPOS_SQL = {"id": "INTEGER", "pagina": "INTEGER", "ano_edicao": "INTEGER"}
INDICES = [
    "CREATE INDEX ix_medicamento_dcb ON medicamento (dcb)",
    "CREATE INDEX ix_medicamento_componente ON medicamento (componente)",
    "CREATE INDEX ix_medicamento_atc ON medicamento (atc)",
    "CREATE INDEX ix_pcdt_doenca ON pcdt_versao (doenca, data_publicacao)",
    "CREATE INDEX ix_pcdt_med_doenca ON pcdt_medicamento (doenca, na_rename)",
]
GRUPO_ATC = re.compile(r"^([A-Z]): (.+)$")
CODIGO_ATC = re.compile(r"^[A-Z]\d{2}[A-Z]{2}\d{2}$")
_ESPACOS = re.compile(r"[\s​]+")
_NOTA_DE_RODAPE = re.compile(r"\s*\(\d\)$")
INICIO_DO_APENDICE_B = "RENAME POR ANEXOS"
DATA_DA_RENAME = "2024-12-26"  # Portaria GM/MS 6.324/2024


def _ddl(nome: str, colunas: list[str]) -> str:
    campos = [f"{c} {TIPOS_SQL.get(c, 'TEXT')}" for c in colunas]
    return f"CREATE TABLE {nome} ({', '.join(campos)})"


def limpar(texto: str | None) -> str | None:
    """Junta quebras de linha e tira espaços (inclusive o espaço de largura zero que o PDF deixa); vazio ou '-' vira None."""
    if texto is None:
        return None
    t = _NOTA_DE_RODAPE.sub("", _ESPACOS.sub(" ", texto).strip())  # "Procedimento Hospitalar(1)": o (1) é nota de rodapé
    return None if t in ("", "-") else t


def papel_da_coluna(cabecalho: str | None) -> str | None:
    """Que campo uma coluna da tabela guarda, pelo texto do cabeçalho; o PDF às vezes cola as palavras ("DenominaçãoComum")."""
    t = _ESPACOS.sub("", (cabecalho or "").lower())
    if "denomina" in t:
        return "dcb"
    if "concentra" in t:
        return "concentracao"
    if t.startswith("forma"):
        return "forma"
    if "componente" in t or ("financiamento" in t and "classif" not in t):  # "i fnanciamento" também aparece
        return "componente"
    if "awar" in t:
        return "aware"
    if t.endswith("atc") and ("código" in t or "codigo" in t) and "classif" not in t:
        return "atc"
    return None


def _tem_codigo_atc(linha: list[str | None], papeis: dict[int, str | None]) -> bool:
    coluna = next((i for i, p in papeis.items() if p == "atc"), None)
    return coluna is not None and bool(CODIGO_ATC.match(limpar(linha[coluna]) or ""))


def _celulas_da_linha(pagina: "pymupdf.Page", linha: Any) -> list[str | None]:
    """O texto de cada célula da linha, lido por recorte da área, que preserva os espaços e as vírgulas decimais."""
    saida: list[str | None] = []
    for caixa in linha.cells:
        saida.append(None if caixa is None else pagina.get_text("text", clip=pymupdf.Rect(*caixa)))
    return saida


def _grupo_da_pagina(pagina: "pymupdf.Page") -> tuple[str, str] | None:
    for linha in pagina.get_text("text").splitlines():
        achado = GRUPO_ATC.match(linha.strip())
        if achado:
            return achado.group(1), _ESPACOS.sub(" ", achado.group(2)).strip()
    return None


def extrair_medicamentos(caminho: Path, ano_edicao: int | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Lê as tabelas com código ATC do PDF. Devolve as linhas e o relatório (páginas, linhas sem código ATC...)."""
    linhas: list[dict[str, Any]] = []
    paginas_com_tabela = 0
    sem_atc = 0
    inicio_por_grupo: dict[str, int] = {}
    papeis: dict[int, str | None] = {}
    grupo: tuple[str, str] | None = None
    with pymupdf.open(caminho) as documento:
        for numero, pagina in enumerate(documento, start=1):
            if linhas and INICIO_DO_APENDICE_B in pagina.get_text("text")[:200]:
                break  # o Apêndice B repete os medicamentos agrupados por anexo
            novo = _grupo_da_pagina(pagina)
            if novo is not None:
                grupo = novo
            for tabela in pagina.find_tables().tables:
                if not tabela.rows:
                    continue
                primeira_linha = tabela.extract()[0]
                cabecalho = [papel_da_coluna(t) for t in primeira_linha]
                if "atc" in cabecalho and "dcb" in cabecalho:
                    papeis = dict(enumerate(cabecalho))  # é uma tabela do Apêndice A, com cabeçalho
                    primeira = 1
                elif papeis and len(primeira_linha) == len(papeis) and _tem_codigo_atc(primeira_linha, papeis):
                    primeira = 0  # continuação da tabela sem repetir o cabeçalho
                else:
                    continue
                paginas_com_tabela += 1
                if grupo is not None:
                    inicio_por_grupo.setdefault(grupo[0], numero)
                for linha in tabela.rows[primeira:]:
                    textos = _celulas_da_linha(pagina, linha)
                    registro = {papel: limpar(textos[i]) for i, papel in papeis.items() if papel and i < len(textos)}
                    if not registro.get("atc"):
                        sem_atc += 1
                    if registro.get("dcb") is None and linhas:
                        registro["dcb"] = linhas[-1]["dcb"]  # a célula do nome é uma só para as várias apresentações
                    if registro.get("dcb") is None:
                        continue
                    registro["grupo_atc"], registro["grupo_atc_nome"] = grupo if grupo else (None, None)
                    registro["pagina"] = numero
                    registro["ano_edicao"] = ano_edicao
                    linhas.append(registro)
    for i, linha in enumerate(linhas, start=1):
        linha["id"] = i
    relatorio = {"paginas_com_tabela": paginas_com_tabela, "linhas_sem_codigo_atc": sem_atc, "primeira_pagina_de_cada_grupo": inicio_por_grupo}
    return linhas, relatorio

SECAO_FARMACOS = re.compile(r"\b\d+(?:\.\d+)*\.?\s+F[ÁA]RMACOS\b")
_ENTRADA_DE_FARMACO = re.compile(r"(?:^|\n|\s{2,})-?\s*([A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-Za-zÀ-ÿ()/+ ,.-]{2,70}?):\s*([^\n]*?)(?=\s{3,}[A-ZÁÉÍÓÚ]|\n|$)")
_USO_PRIVADO = re.compile("[" + chr(0xE000) + "-" + chr(0xF8FF) + "]")  # o PDF usa um caractere de uso privado como marcador de lista
_FORMA_FARMACEUTICA = re.compile(r"(?i)comprimid|c[áa]psula|frasco|seringa|solu[çc][ãa]o|ampola|caneta|injet|\d\s?(mg|ml|mcg|ui)\b")
_SAIS = {
    "cloridrato", "dicloridrato", "mesilato", "lactato", "sulfato", "acetato", "succinato", "fosfato", "citrato", "maleato", "tartarato",
    "besilato", "bromidrato", "nitrato", "fumarato", "valerato", "propionato", "hemissuccinato", "sodico", "calcico", "potassico",
    "difosfato", "dipropionato", "dimesilato", "hidrocloreto", "tartrato", "bitartarato", "oxalato", "gluconato", "carbonato", "estearato",
}


def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn").lower()


def componentes_do_nome(nome: str) -> frozenset[str]:
    """Os princípios ativos de um nome de medicamento, sem sal, sem parênteses e sem acento: 'Levodopa/carbidopa' e 'levodopa + carbidopa' dão o mesmo conjunto."""
    t = re.sub(r"\([^)]*\)", " ", _sem_acento(nome))
    saida = set()
    for parte in re.split(r"\s*(?:/|\+|\be\b)\s*", t):
        palavras = [w for w in re.split(r"[\s,;]+", parte) if w and w != "de" and w not in _SAIS]
        if palavras:
            saida.add(" ".join(palavras))
    return frozenset(saida)


_CLASSE_DE_MEDICAMENTOS = re.compile(r"MEDICAMENTOS MODIFICADORES DO CURSO DA DOEN[ÇC]A [A-ZÇÃÕÁÉÍÓÚ ]+\((MMCD\w*)\)\s*[:–—-]\s*(.+?)(?=;|\.\s|\n|$)")
_ITEM_SEM_DOIS_PONTOS = re.compile(r"([A-ZÁÉÍÓÚ][\wÀ-ÿ ]*?)\s+(\d+(?:[.,]\d+)?\s?[A-Za-zµ]+\s?/\s?[A-Za-z]+)\s*([a-zçãáéíóú ]*?)(?=\s+[A-ZÁÉÍÓÚ]|$)")


def _linha_de_pcdt(metadata: dict[str, Any], nome: str, apresentacao: str | None, classe: str | None = None) -> dict[str, Any]:
    return {
        "doenca": metadata.get("doenca"), "versao": metadata.get("versao"), "data_publicacao": metadata.get("data_publicacao"),
        "situacao": "substituída" if metadata.get("data_vigencia_fim") else "vigente",
        "medicamento": re.sub(r"\s+", " ", nome).strip(), "apresentacao": re.sub(r"\s+", " ", apresentacao).strip() if apresentacao else None,
        "classe": classe,
    }


def _corpo(chunk: dict[str, Any]) -> str:
    texto = chunk.get("texto", "")
    return texto.split("\n", 1)[1] if "\n" in texto else texto


def _lista_com_dois_pontos(grupo: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """'Pramipexol: comprimidos de 1 mg.' na seção Fármacos (só no primeiro trecho dela): Parkinson 2017 e artrite 2017 a 2021."""
    saida: list[dict[str, Any]] = []
    for c in grupo:
        m = c.get("metadata", {})
        corpo = _corpo(c)
        achado = SECAO_FARMACOS.search(corpo[:400])
        if not achado or m.get("parte") not in (None, 1):
            continue
        for nome, apresentacao in _ENTRADA_DE_FARMACO.findall(corpo[achado.end():]):
            apresentacao = _USO_PRIVADO.sub(" ", apresentacao).strip()  # marcador de lista do PDF
            apresentacao = re.split(r"\.\s+(?=[A-ZÁÉÍÓÚ])", apresentacao)[0].rstrip(". ")
            if _FORMA_FARMACEUTICA.search(apresentacao):
                saida.append(_linha_de_pcdt(m, nome, apresentacao))
    return saida


def _lista_por_classe(grupo: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """'MEDICAMENTOS MODIFICADORES DO CURSO DA DOENÇA BIOLÓGICOS (MMCDbio) - Abatacepte, adalimumabe e ...': artrite 2026, sem apresentação."""
    saida: list[dict[str, Any]] = []
    vistos: set[str] = set()
    for c in grupo:
        for classe, lista in _CLASSE_DE_MEDICAMENTOS.findall(_corpo(c)):
            for nome in re.split(r",\s*|\s+e\s+", lista.strip().rstrip(".")):
                nome = nome.strip()
                if nome and len(nome.split()) <= 3 and nome.lower() not in vistos:
                    vistos.add(nome.lower())
                    saida.append(_linha_de_pcdt(c["metadata"], nome, None, classe))
    return saida


def _lista_sem_dois_pontos(grupo: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """'Insulina NPH 100U/mL suspensão injetável Insulina regular 100U/mL solução injetável ...' logo após o título Fármacos: diabetes tipo 1."""
    saida: list[dict[str, Any]] = []
    for c in grupo:
        corpo = _corpo(c)
        titulos = list(SECAO_FARMACOS.finditer(corpo[:200]))
        if not titulos or c.get("metadata", {}).get("parte") not in (None, 1):
            continue
        bloco = corpo[titulos[-1].end():][:400].lstrip(" \n")
        bloco = re.split(r"MINIST[ÉE]RIO DA SA[ÚU]DE", bloco)[0].strip()  # rodapé da página
        for nome, dose, forma in _ITEM_SEM_DOIS_PONTOS.findall(bloco):
            saida.append(_linha_de_pcdt(c["metadata"], nome, f"{dose} {forma}".strip()))
    return saida


def medicamentos_do_pcdt(chunks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Lê a lista de medicamentos de cada versão de PCDT e devolve (medicamentos, versões em que nenhuma lista foi lida).

    Há três formatos, cada um com seu leitor (a primeira que achar algo vale para a versão): 'nome: apresentação' na seção Fármacos,
    listas por classe no texto do tratamento (sem apresentação) e 'nome dose forma' sem dois-pontos. Cada leitor serve ao formato de
    documentos conhecidos: se um protocolo mudar de formato, a versão aparece como sem lista no relatório, em vez de sair uma lista errada.
    """
    por_versao: dict[Any, list[dict[str, Any]]] = {}
    for c in chunks:
        por_versao.setdefault(c.get("metadata", {}).get("versao"), []).append(c)
    achados: list[dict[str, Any]] = []
    sem_lista: list[str] = []
    for versao, grupo in por_versao.items():
        linhas = _lista_com_dois_pontos(grupo) or _lista_por_classe(grupo) or _lista_sem_dois_pontos(grupo)
        if linhas:
            achados += linhas
        else:
            sem_lista.append(str(versao))
    return achados, sorted(sem_lista)


def marcar_constar_na_rename(medicamentos_pcdt: list[dict[str, Any]], dcbs_da_rename: list[str]) -> list[tuple[str, str]]:
    """Preenche na_rename ('sim' ou 'não') e dcb_na_rename comparando os princípios ativos; os nomes da RENAME vêm com sal (cloridrato de ...).

    Se o nome não casa por inteiro, aceita-se um nome da RENAME que o contenha (palavra por palavra) quando só um nome o contém ('insulina NPH'
    no protocolo, 'insulina humana NPH' na RENAME). Essas correspondências aproximadas são devolvidas, para constar no relatório.
    """
    por_conjunto: dict[frozenset[str], str] = {}
    for dcb in dcbs_da_rename:
        por_conjunto.setdefault(componentes_do_nome(dcb), dcb)
    aproximadas: list[tuple[str, str]] = []
    for i, linha in enumerate(medicamentos_pcdt, 1):
        componentes = componentes_do_nome(linha["medicamento"])
        achado = por_conjunto.get(componentes)
        if achado is None and len(componentes) == 1:
            palavras = set(next(iter(componentes)).split())
            candidatos = [dcb for conj, dcb in por_conjunto.items() if len(conj) == 1 and palavras <= set(next(iter(conj)).split())]
            if len(candidatos) == 1:
                achado = candidatos[0]
                aproximadas.append((linha["medicamento"], achado))
        linha.update({"id": i, "na_rename": "sim" if achado else "não", "dcb_na_rename": achado})
    return aproximadas


def linhas_de_pcdt(manifesto: dict[str, Any]) -> list[dict[str, Any]]:
    """Uma linha por versão de PCDT declarada no manifesto. 'vigente' = sem data de fim na data da coleta."""
    saida: list[dict[str, Any]] = []
    for base in manifesto.get("bases", []):
        if base.get("base_id") != "pcdt_normativo":
            continue
        for item in (base.get("acesso") or {}).get("urls", []):
            fim = item.get("data_vigencia_fim")
            saida.append({
                "id": len(saida) + 1, "doenca": item.get("doenca"), "versao": item.get("versao"), "rotulo": item.get("rotulo"),
                "data_publicacao": item.get("data_publicacao"), "data_vigencia_fim": fim,
                "situacao": "substituída" if fim else "vigente",
            })
    return saida


def _chunks_de_pcdt(pastas: Pastas, versao: str) -> list[dict[str, Any]]:
    """Trechos já ingeridos dos PCDT (a ingestão roda antes); sem eles, não há lista de fármacos."""
    caminho = pastas.dados / "processed" / pastas.cenario_id / f"{versao}_pcdt_normativo.jsonl"
    if not caminho.exists():
        return []
    return [json.loads(l) for l in caminho.read_text(encoding="utf-8").splitlines() if l.strip()]


def _pdf_da_rename(pasta_versao: Path, base_id: str) -> Path:
    candidatos = sorted((pasta_versao / base_id).glob("*.pdf")) if (pasta_versao / base_id).is_dir() else []
    if not candidatos:
        raise FileNotFoundError(f"nenhum PDF da base {base_id} em {pasta_versao}")
    return candidatos[0]


def normalizar(manifesto_path: Path, versao: str, pastas: Pastas) -> dict[str, Any]:
    manifesto = carregar_manifesto(manifesto_path)
    pasta_versao = pastas.raw / versao
    if not pasta_versao.is_dir():
        raise FileNotFoundError(f"versão {versao} não encontrada em {pasta_versao}")
    pdf = _pdf_da_rename(pasta_versao, "rename_estruturado")
    medicamentos, rel_pdf = extrair_medicamentos(pdf, ano_edicao=2024)
    if not medicamentos:
        raise FileNotFoundError(f"nenhuma linha de medicamento lida em {pdf.name}")
    versoes = linhas_de_pcdt(manifesto)
    chunks_pcdt = _chunks_de_pcdt(pastas, versao)
    farmacos, sem_secao = medicamentos_do_pcdt(chunks_pcdt)
    aproximadas = marcar_constar_na_rename(farmacos, sorted({m["dcb"] for m in medicamentos if m.get("dcb")}))

    saida = pastas.dados / "normalized" / pastas.cenario_id
    saida.mkdir(parents=True, exist_ok=True)
    destino = saida / f"{versao}.sqlite"
    temporario = saida / f"{versao}.sqlite.tmp"
    if temporario.exists():
        com_repeticao(temporario.unlink)

    conexao = sqlite3.connect(temporario)
    try:
        conexao.execute(_ddl("medicamento", COLUNAS_MEDICAMENTO))
        conexao.execute(_ddl("pcdt_versao", COLUNAS_PCDT))
        conexao.execute(_ddl("pcdt_medicamento", COLUNAS_PCDT_MEDICAMENTO))
        conexao.execute("CREATE TABLE metadados (chave TEXT PRIMARY KEY, valor TEXT)")
        for nome, colunas, linhas in (("medicamento", COLUNAS_MEDICAMENTO, medicamentos), ("pcdt_versao", COLUNAS_PCDT, versoes), ("pcdt_medicamento", COLUNAS_PCDT_MEDICAMENTO, farmacos)):
            conexao.executemany(
                f"INSERT INTO {nome} ({','.join(colunas)}) VALUES ({','.join('?' * len(colunas))})",
                [tuple(l.get(c) for c in colunas) for l in linhas],
            )
        for comando in INDICES:
            conexao.execute(comando)
        total = {t: conexao.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("medicamento", "pcdt_versao", "pcdt_medicamento")}
        por_componente = dict(conexao.execute("SELECT componente, COUNT(*) FROM medicamento GROUP BY componente ORDER BY 2 DESC").fetchall())
        distintos = conexao.execute("SELECT COUNT(DISTINCT dcb) FROM medicamento").fetchone()[0]
        por_grupo = dict(conexao.execute("SELECT grupo_atc, COUNT(*) FROM medicamento GROUP BY grupo_atc ORDER BY 1").fetchall())
        sem_forma = conexao.execute("SELECT COUNT(*) FROM medicamento WHERE forma IS NULL").fetchone()[0]
        datas = [d for (d,) in conexao.execute("SELECT data_publicacao FROM pcdt_versao WHERE data_publicacao IS NOT NULL")] + [DATA_DA_RENAME]
        relatorio = {
            "scenario_id": pastas.cenario_id,
            "versao": versao,
            "arquivo_rename": pdf.name,
            "tabelas": total,
            "medicamentos_distintos": distintos,
            "linhas_por_componente": por_componente,
            "linhas_por_grupo_atc": por_grupo,
            "linhas_sem_forma": sem_forma,
            "pcdt_correspondencias_aproximadas": aproximadas,  # nome do protocolo contido num único nome da RENAME (ex.: insulina NPH e insulina humana NPH)
            "pcdt_versoes_sem_secao_farmacos": sem_secao,  # a lista de fármacos não foi lida nestas versões (o cruzamento com a RENAME não as cobre)
            "rejeitados": {},
            "periodo_de_publicacao": {"de": min(datas), "ate": max(datas)},  # das portarias dos protocolos e da RENAME
            **rel_pdf,
        }
        conexao.executemany(
            "INSERT INTO metadados VALUES (?, ?)",
            [("versao", versao), ("cenario", pastas.cenario_id), ("relatorio", json.dumps(relatorio, ensure_ascii=False))],
        )
        conexao.commit()
    finally:
        conexao.close()

    if destino.exists():
        com_repeticao(destino.unlink)
    com_repeticao(lambda: temporario.rename(destino))
    (saida / f"{versao}.relatorio.json").write_text(json.dumps(relatorio, ensure_ascii=False, indent=2), encoding="utf-8")
    return relatorio
