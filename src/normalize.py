"""Normalização da base estruturada: das páginas JSON (API do PNCP e snapshot) para tabelas SQL.

    python -m src.normalize --scenario rj [--versao v3]

Lê as bases estruturadas de uma versão do corpus (data/raw/<cenário>/<versão>/) e grava um banco SQLite em
data/normalized/<cenário>/<versão>.sqlite com as tabelas `contratacao`, `contrato` e `orgao`. É nesse banco, e
só nele, que a consulta SQL controlada do roteador vai trabalhar (a LLM nunca escreve SQL).

Regras:
- Uma linha por número de controle do PNCP. Se o mesmo registro vem da API e do snapshot, vale o de
  `dataAtualizacaoGlobal` mais recente; no empate, o da API. A coluna `fonte` guarda de onde veio.
- Registro sem número de controle válido é rejeitado e contado no relatório; nada é descartado em silêncio.
- CPF de fornecedor pessoa física é mascarado; CNPJ e identificador de estrangeiro, não. Texto livre passa
  pelo mascaramento de dados pessoais.
- A saída é determinística: a mesma entrada gera as mesmas linhas, na mesma ordem.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from src.fetch_corpus import Pastas, carregar_manifesto, pastas_do_cenario, versoes_existentes
from src.fsutil import com_repeticao
from src.pii import MARCA_CPF, cpf_valido, mascarar_dados_pessoais

ID_PNCP = re.compile(r"^\d{14}-\d-\d{1,9}/\d{4}$")
ENTIDADES = {"contratacoes": "contratacao", "contratos": "contrato"}
TIPOS_DE_FONTE = {"rest_api": "api", "snapshot_sqlite": "snapshot"}

COLUNAS_CONTRATACAO = [
    "numero_controle_pncp", "orgao_cnpj", "orgao_razao_social", "esfera_id", "poder_id",
    "unidade_codigo", "unidade_nome", "uf_sigla", "municipio_nome", "codigo_ibge",
    "ano_compra", "sequencial_compra", "numero_compra", "processo",
    "modalidade_id", "modalidade_nome", "modo_disputa_id", "modo_disputa_nome",
    "situacao_id", "situacao_nome", "srp", "objeto_compra",
    "valor_total_estimado", "valor_total_homologado", "amparo_legal_codigo", "amparo_legal_nome",
    "data_publicacao", "data_abertura_proposta", "data_encerramento_proposta", "data_atualizacao_global", "fonte",
]
COLUNAS_CONTRATO = [
    "numero_controle_pncp", "numero_controle_pncp_compra", "orgao_cnpj", "orgao_razao_social", "esfera_id", "poder_id",
    "unidade_codigo", "unidade_nome", "uf_sigla", "municipio_nome", "codigo_ibge",
    "ano_contrato", "sequencial_contrato", "numero_contrato_empenho", "processo",
    "tipo_contrato_id", "tipo_contrato_nome", "categoria_processo_id", "categoria_processo_nome", "objeto_contrato",
    "tipo_pessoa", "ni_fornecedor", "nome_fornecedor", "fornecedor_pessoa_fisica",
    "valor_inicial", "valor_global", "valor_acumulado", "valor_parcela", "numero_parcelas",
    "data_assinatura", "data_vigencia_inicio", "data_vigencia_fim", "data_publicacao", "data_atualizacao_global", "fonte",
]
TIPOS_SQL = {
    "ano_compra": "INTEGER", "sequencial_compra": "INTEGER", "modalidade_id": "INTEGER", "modo_disputa_id": "INTEGER",
    "situacao_id": "INTEGER", "srp": "INTEGER", "valor_total_estimado": "REAL", "valor_total_homologado": "REAL",
    "amparo_legal_codigo": "INTEGER", "ano_contrato": "INTEGER", "sequencial_contrato": "INTEGER",
    "tipo_contrato_id": "INTEGER", "categoria_processo_id": "INTEGER", "fornecedor_pessoa_fisica": "INTEGER",
    "valor_inicial": "REAL", "valor_global": "REAL", "valor_acumulado": "REAL", "valor_parcela": "REAL",
    "numero_parcelas": "INTEGER",
}


def _ddl(nome: str, colunas: list[str]) -> str:
    campos = [f"{c} {TIPOS_SQL.get(c, 'TEXT')}{' PRIMARY KEY' if c == 'numero_controle_pncp' else ''}" for c in colunas]
    return f"CREATE TABLE {nome} ({', '.join(campos)})"


INDICES = [
    "CREATE INDEX ix_contratacao_ano ON contratacao (ano_compra)",
    "CREATE INDEX ix_contratacao_modalidade ON contratacao (modalidade_id)",
    "CREATE INDEX ix_contratacao_orgao ON contratacao (orgao_cnpj)",
    "CREATE INDEX ix_contratacao_municipio ON contratacao (uf_sigla, municipio_nome)",
    "CREATE INDEX ix_contratacao_data ON contratacao (data_publicacao)",
    "CREATE INDEX ix_contrato_ano ON contrato (ano_contrato)",
    "CREATE INDEX ix_contrato_orgao ON contrato (orgao_cnpj)",
    "CREATE INDEX ix_contrato_assinatura ON contrato (data_assinatura)",
    "CREATE INDEX ix_contrato_compra ON contrato (numero_controle_pncp_compra)",
    "CREATE INDEX ix_contrato_fornecedor ON contrato (ni_fornecedor)",
]


# --------------------------------------------------------------------------- conversões


def _texto(valor: Any, livre: bool = False) -> str | None:
    if valor is None:
        return None
    texto = str(valor).strip()
    if not texto:
        return None
    return mascarar_dados_pessoais(texto) if livre else texto


def _numero(valor: Any) -> float | None:
    if isinstance(valor, bool) or valor is None:
        return None
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def _inteiro(valor: Any) -> int | None:
    numero = _numero(valor)
    return int(numero) if numero is not None and numero == int(numero) else None


def _data(valor: Any) -> str | None:
    """AAAA-MM-DD, ou nada se a data não existe (por exemplo, 2026-13-45)."""
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(valor or ""))
    if not m:
        return None
    try:
        datetime.strptime(m.group(1), "%Y-%m-%d")
    except ValueError:
        return None
    return m.group(1)


def _instante(valor: Any) -> str | None:
    texto = str(valor or "")[:19]
    return texto if re.match(r"\d{4}-\d{2}-\d{2}", texto) else None


def _comuns(d: dict[str, Any]) -> dict[str, Any]:
    orgao, unidade = d.get("orgaoEntidade") or {}, d.get("unidadeOrgao") or {}
    return {
        "orgao_cnpj": _texto(orgao.get("cnpj")), "orgao_razao_social": _texto(orgao.get("razaoSocial")),
        "esfera_id": _texto(orgao.get("esferaId")), "poder_id": _texto(orgao.get("poderId")),
        "unidade_codigo": _texto(unidade.get("codigoUnidade")), "unidade_nome": _texto(unidade.get("nomeUnidade")),
        "uf_sigla": _texto(unidade.get("ufSigla")), "municipio_nome": _texto(unidade.get("municipioNome")),
        "codigo_ibge": _texto(unidade.get("codigoIbge")),
        "processo": _texto(d.get("processo")),
        "data_publicacao": _data(d.get("dataPublicacaoPncp")),
        "data_atualizacao_global": _instante(d.get("dataAtualizacaoGlobal")),
    }


def linha_contratacao(d: dict[str, Any], fonte: str) -> dict[str, Any]:
    amparo = d.get("amparoLegal") or {}
    return {
        **_comuns(d),
        "numero_controle_pncp": d["numeroControlePNCP"],
        "ano_compra": _inteiro(d.get("anoCompra")), "sequencial_compra": _inteiro(d.get("sequencialCompra")),
        "numero_compra": _texto(d.get("numeroCompra")),
        "modalidade_id": _inteiro(d.get("modalidadeId")), "modalidade_nome": _texto(d.get("modalidadeNome")),
        "modo_disputa_id": _inteiro(d.get("modoDisputaId")), "modo_disputa_nome": _texto(d.get("modoDisputaNome")),
        "situacao_id": _inteiro(d.get("situacaoCompraId")), "situacao_nome": _texto(d.get("situacaoCompraNome")),
        "srp": 1 if d.get("srp") else 0,
        "objeto_compra": _texto(d.get("objetoCompra"), livre=True),
        "valor_total_estimado": _numero(d.get("valorTotalEstimado")),
        "valor_total_homologado": _numero(d.get("valorTotalHomologado")),
        "amparo_legal_codigo": _inteiro(amparo.get("codigo")), "amparo_legal_nome": _texto(amparo.get("nome")),
        "data_abertura_proposta": _instante(d.get("dataAberturaProposta")),
        "data_encerramento_proposta": _instante(d.get("dataEncerramentoProposta")),
        "fonte": fonte,
    }


def linha_contrato(d: dict[str, Any], fonte: str) -> tuple[dict[str, Any], bool]:
    """Devolve a linha e se o CPF de um fornecedor pessoa física foi mascarado."""
    tipo_pessoa = _texto(d.get("tipoPessoa"))
    ni = re.sub(r"\D", "", str(d.get("niFornecedor") or ""))
    pessoa_fisica = tipo_pessoa == "PF" or (tipo_pessoa not in ("PJ", "PE") and len(ni) == 11 and cpf_valido(ni))
    tipo_contrato, categoria = d.get("tipoContrato") or {}, d.get("categoriaProcesso") or {}
    linha = {
        **_comuns(d),
        "numero_controle_pncp": d["numeroControlePNCP"],
        "numero_controle_pncp_compra": _texto(d.get("numeroControlePncpCompra")),
        "ano_contrato": _inteiro(d.get("anoContrato")), "sequencial_contrato": _inteiro(d.get("sequencialContrato")),
        "numero_contrato_empenho": _texto(d.get("numeroContratoEmpenho")),
        "tipo_contrato_id": _inteiro(tipo_contrato.get("id")), "tipo_contrato_nome": _texto(tipo_contrato.get("nome")),
        "categoria_processo_id": _inteiro(categoria.get("id")), "categoria_processo_nome": _texto(categoria.get("nome")),
        "objeto_contrato": _texto(d.get("objetoContrato"), livre=True),
        "tipo_pessoa": tipo_pessoa,
        "ni_fornecedor": MARCA_CPF if pessoa_fisica else (_texto(d.get("niFornecedor"))),
        "nome_fornecedor": _texto(d.get("nomeRazaoSocialFornecedor")),
        "fornecedor_pessoa_fisica": 1 if pessoa_fisica else 0,
        "valor_inicial": _numero(d.get("valorInicial")), "valor_global": _numero(d.get("valorGlobal")),
        "valor_acumulado": _numero(d.get("valorAcumulado")), "valor_parcela": _numero(d.get("valorParcela")),
        "numero_parcelas": _inteiro(d.get("numeroParcelas")),
        "data_assinatura": _data(d.get("dataAssinatura")),
        "data_vigencia_inicio": _data(d.get("dataVigenciaInicio")), "data_vigencia_fim": _data(d.get("dataVigenciaFim")),
        "fonte": fonte,
    }
    return linha, pessoa_fisica


# --------------------------------------------------------------------------- leitura das páginas


def _bases_estruturadas(manifesto: dict[str, Any]) -> dict[str, str]:
    """base_id -> tipo de fonte (api ou snapshot), só das bases estruturadas que têm páginas JSON."""
    return {
        b["base_id"]: TIPOS_DE_FONTE[b["acesso"]["tipo"]]
        for b in manifesto["bases"]
        if b["natureza"] == "estruturada" and b["acesso"]["tipo"] in TIPOS_DE_FONTE
    }


def _registros(pasta_versao: Path, bases: dict[str, str]):
    """Gera (entidade, fonte, registro) em ordem fixa."""
    for base_id, fonte in bases.items():
        pasta_base = pasta_versao / base_id
        if not pasta_base.is_dir():
            continue
        for pagina in sorted(pasta_base.rglob("pagina_*.json")):
            entidade = ENTIDADES.get(pagina.relative_to(pasta_base).parts[0])
            if entidade is None:
                continue
            conteudo = json.loads(pagina.read_text(encoding="utf-8"))
            for registro in (conteudo.get("data", []) if isinstance(conteudo, dict) else conteudo):
                yield entidade, fonte, registro


# --------------------------------------------------------------------------- normalização


def normalizar(manifesto_path: Path, versao: str, pastas: Pastas) -> dict[str, Any]:
    manifesto = carregar_manifesto(manifesto_path)
    bases = _bases_estruturadas(manifesto)
    pasta_versao = pastas.versao(versao)
    if not pasta_versao.is_dir():
        raise FileNotFoundError(f"a versão {versao} não foi coletada")

    escolhidas: dict[str, dict[str, tuple[dict[str, Any], set[str]]]] = {"contratacao": {}, "contrato": {}}
    lidos: Counter[str] = Counter()
    rejeitados: Counter[str] = Counter()
    exemplos_rejeitados: list[str] = []
    entre_fontes: Counter[str] = Counter()
    divergentes: Counter[str] = Counter()
    pf_mascarados = 0

    for entidade, fonte, bruto in _registros(pasta_versao, bases):
        lidos[f"{entidade}:{fonte}"] += 1
        numero = bruto.get("numeroControlePNCP") if isinstance(bruto, dict) else None
        if not isinstance(numero, str) or not ID_PNCP.match(numero):
            rejeitados[f"{entidade}: sem número de controle válido"] += 1
            if len(exemplos_rejeitados) < 5:
                exemplos_rejeitados.append(f"{entidade}/{fonte}: {numero!r}")
            continue
        if entidade == "contratacao":
            linha = linha_contratacao(bruto, fonte)
        else:
            linha, era_pf = linha_contrato(bruto, fonte)
        anterior = escolhidas[entidade].get(numero)
        if anterior is None:
            escolhidas[entidade][numero] = (linha, {fonte})
            pf_mascarados += entidade == "contrato" and linha["fornecedor_pessoa_fisica"]
            continue
        antiga, fontes = anterior
        entre_fontes[entidade] += fonte not in fontes
        mesmo_conteudo = {k: v for k, v in antiga.items() if k != "fonte"} == {k: v for k, v in linha.items() if k != "fonte"}
        divergentes[entidade] += not mesmo_conteudo
        novo_e_melhor = (linha["data_atualizacao_global"] or "") > (antiga["data_atualizacao_global"] or "") or (
            (linha["data_atualizacao_global"] or "") == (antiga["data_atualizacao_global"] or "") and fonte == "api" and "api" not in fontes
        )
        escolhidas[entidade][numero] = (linha if novo_e_melhor else antiga, fontes | {fonte})

    if not any(escolhidas.values()):
        raise FileNotFoundError(f"nenhuma página de base estruturada na versão {versao}")

    saida = pastas.dados / "normalized" / pastas.cenario_id
    saida.mkdir(parents=True, exist_ok=True)
    destino = saida / f"{versao}.sqlite"
    temporario = saida / f"{versao}.sqlite.tmp"
    if temporario.exists():
        com_repeticao(temporario.unlink)

    conexao = sqlite3.connect(temporario)
    try:
        conexao.execute(_ddl("contratacao", COLUNAS_CONTRATACAO))
        conexao.execute(_ddl("contrato", COLUNAS_CONTRATO))
        conexao.execute(
            "CREATE TABLE orgao (cnpj TEXT PRIMARY KEY, razao_social TEXT, esfera_id TEXT, poder_id TEXT)"
        )
        conexao.execute("CREATE TABLE metadados (chave TEXT PRIMARY KEY, valor TEXT)")
        for nome, colunas in (("contratacao", COLUNAS_CONTRATACAO), ("contrato", COLUNAS_CONTRATO)):
            linhas = []
            for numero in sorted(escolhidas[nome]):
                linha, fontes = escolhidas[nome][numero]
                linha = {**linha, "fonte": "+".join(sorted(fontes))}
                linhas.append(tuple(linha.get(c) for c in colunas))
            marcas = ",".join("?" * len(colunas))
            conexao.executemany(f"INSERT INTO {nome} ({','.join(colunas)}) VALUES ({marcas})", linhas)
        conexao.execute(
            "INSERT INTO orgao SELECT orgao_cnpj, MAX(orgao_razao_social), MAX(esfera_id), MAX(poder_id) FROM ("
            "SELECT orgao_cnpj, orgao_razao_social, esfera_id, poder_id FROM contratacao UNION ALL "
            "SELECT orgao_cnpj, orgao_razao_social, esfera_id, poder_id FROM contrato) "
            "WHERE orgao_cnpj IS NOT NULL GROUP BY orgao_cnpj ORDER BY orgao_cnpj"
        )
        for comando in INDICES:
            conexao.execute(comando)

        total = {t: conexao.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("contratacao", "contrato", "orgao")}
        com_compra = conexao.execute(
            "SELECT COUNT(*) FROM contrato c WHERE c.numero_controle_pncp_compra IN (SELECT numero_controle_pncp FROM contratacao)"
        ).fetchone()[0]
        periodo = conexao.execute(
            "SELECT MIN(d), MAX(d) FROM (SELECT data_publicacao d FROM contratacao UNION ALL SELECT data_publicacao FROM contrato)"
        ).fetchone()
        relatorio = {
            "scenario_id": pastas.cenario_id,
            "versao": versao,
            "bases_lidas": bases,
            "tabelas": total,
            "registros_lidos": dict(lidos),
            "rejeitados": dict(rejeitados),
            "exemplos_rejeitados": exemplos_rejeitados,
            "presentes_em_mais_de_uma_fonte": dict(entre_fontes),
            "com_conteudo_diferente_entre_fontes": dict(divergentes),
            "fornecedores_pessoa_fisica_mascarados": int(pf_mascarados),
            "contratos_com_contratacao_encontrada": {
                "total": com_compra,
                "percentual": round(100 * com_compra / total["contrato"], 1) if total["contrato"] else None,
            },
            "periodo_de_publicacao": {"de": periodo[0], "ate": periodo[1]},
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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m src.normalize", description=__doc__.split("\n\n")[0])
    p.add_argument("--scenario", required=True, choices=("rj", "saude", "pesquisa"))
    p.add_argument("--versao", help="padrão: a versão mais recente coletada")
    p.add_argument("--manifest", type=Path)
    args = p.parse_args(argv)
    pastas = pastas_do_cenario(args.scenario)
    versao = args.versao or (versoes_existentes(pastas) or [None])[-1]
    if not versao:
        print("Erro: nenhuma versão coletada. Rode primeiro o fetch_corpus.", file=sys.stderr)
        return 2
    try:
        r = normalizar(args.manifest or pastas.cenario / "corpus_manifest.yaml", versao, pastas)
    except FileNotFoundError as erro:
        print(f"Erro: {erro}", file=sys.stderr)
        return 2
    t = r["tabelas"]
    print(f"{versao}: {t['contratacao']} contratações, {t['contrato']} contratos, {t['orgao']} órgãos.")
    print(f"  período: {r['periodo_de_publicacao']['de']} a {r['periodo_de_publicacao']['ate']}")
    print(f"  em mais de uma fonte: {r['presentes_em_mais_de_uma_fonte']} | com conteúdo diferente: {r['com_conteudo_diferente_entre_fontes']}")
    print(f"  rejeitados: {sum(r['rejeitados'].values())} | CPF de fornecedor mascarado: {r['fornecedores_pessoa_fisica_mascarados']}")
    print(f"  contratos com a contratação encontrada: {r['contratos_com_contratacao_encontrada']['percentual']}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
