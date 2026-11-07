"""Normalização da saúde: a RENAME 2024 (PDF de tabelas) e o catálogo de versões de PCDT viram tabelas SQL.

Gera `data/normalized/saude/<versão>.sqlite` com:

  medicamento   uma linha por medicamento + concentração + forma farmacêutica (Apêndice A da RENAME, por classificação ATC)
  pcdt_versao   uma linha por versão de PCDT do corpus, com a data de publicação e a de fim da vigência
  metadados     versão, cenário e o relatório da normalização

mais um `<versão>.relatorio.json`. O Apêndice B da RENAME (os mesmos medicamentos reagrupados por anexo) não é lido: o
Apêndice A já traz todos os medicamentos com o código ATC. As colunas são as que `scenarios/saude/capacidades.yaml` declara.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

import pymupdf

from src.fetch_corpus import Pastas, carregar_manifesto
from src.fsutil import com_repeticao

COLUNAS_MEDICAMENTO = ["id", "dcb", "concentracao", "forma", "componente", "atc", "aware", "grupo_atc", "grupo_atc_nome", "pagina", "ano_edicao"]
COLUNAS_PCDT = ["id", "doenca", "versao", "rotulo", "data_publicacao", "data_vigencia_fim", "situacao"]
TIPOS_SQL = {"id": "INTEGER", "pagina": "INTEGER", "ano_edicao": "INTEGER"}
INDICES = [
    "CREATE INDEX ix_medicamento_dcb ON medicamento (dcb)",
    "CREATE INDEX ix_medicamento_componente ON medicamento (componente)",
    "CREATE INDEX ix_medicamento_atc ON medicamento (atc)",
    "CREATE INDEX ix_pcdt_doenca ON pcdt_versao (doenca, data_publicacao)",
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
        conexao.execute("CREATE TABLE metadados (chave TEXT PRIMARY KEY, valor TEXT)")
        for nome, colunas, linhas in (("medicamento", COLUNAS_MEDICAMENTO, medicamentos), ("pcdt_versao", COLUNAS_PCDT, versoes)):
            conexao.executemany(
                f"INSERT INTO {nome} ({','.join(colunas)}) VALUES ({','.join('?' * len(colunas))})",
                [tuple(l.get(c) for c in colunas) for l in linhas],
            )
        for comando in INDICES:
            conexao.execute(comando)
        total = {t: conexao.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("medicamento", "pcdt_versao")}
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
