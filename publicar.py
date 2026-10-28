#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Publica a lista gerada no GitHub (via GitHub CLI) e mostra o link raw.

Usa `gh api` com o token que ja esta logado (gh auth login), entao nao precisa
de git, nem de clonar o repositorio, nem de criar token na mao.

Uso:
    python publicar.py                                  # sobe canais.m3u
    python publicar.py --arquivo canais.m3u --arquivo stream_urls.tsv
    python publicar.py --repo usuario/repo --branch main
    python publicar.py --so-link                        # apenas mostra o link

Requer o GitHub CLI instalado e autenticado: https://cli.github.com/
"""

import argparse
import base64
import json
import os
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_PADRAO = "leandrorsnrz/scrap-iptv"
ARQUIVOS_PADRAO = ["canais.m3u", "stream_urls.tsv", "stream_urls_canais.tsv"]


def rodar_gh(argumentos, entrada=None):
    """Executa o gh e devolve (codigo, stdout, stderr)."""
    processo = subprocess.run(
        ["gh"] + argumentos,
        input=entrada,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return processo.returncode, processo.stdout.strip(), processo.stderr.strip()


def verificar_gh():
    try:
        codigo, saida, erro = rodar_gh(["auth", "status"])
    except FileNotFoundError:
        sys.exit("ERRO: GitHub CLI (gh) nao encontrado. Instale em https://cli.github.com/\n"
                 "Depois rode: gh auth login")
    if codigo != 0:
        sys.exit("ERRO: GitHub CLI sem login.\nRode: gh auth login\n\n" + (erro or saida))


def branch_padrao(repo):
    codigo, saida, erro = rodar_gh(["repo", "view", repo, "--json", "defaultBranchRef",
                                    "--jq", ".defaultBranchRef.name"])
    if codigo != 0 or not saida:
        sys.exit(f"ERRO: nao consegui acessar o repositorio {repo}.\n{erro}")
    return saida


def sha_atual(repo, caminho, branch):
    codigo, saida, _ = rodar_gh(["api", f"repos/{repo}/contents/{caminho}?ref={branch}",
                                 "--jq", ".sha"])
    return saida if codigo == 0 else ""


def publicar(repo, branch, caminho, mensagem):
    """Cria ou atualiza um arquivo do repositorio. Devolve o nome do arquivo."""
    if not os.path.exists(caminho):
        print(f"  pulando {caminho} (nao existe)")
        return False
    with open(caminho, "rb") as fh:
        conteudo = base64.b64encode(fh.read()).decode("ascii")

    corpo = {"message": mensagem, "content": conteudo, "branch": branch}
    sha = sha_atual(repo, caminho, branch)
    if sha:
        corpo["sha"] = sha

    codigo, saida, erro = rodar_gh(
        ["api", "--method", "PUT", f"repos/{repo}/contents/{caminho}", "--input", "-"],
        entrada=json.dumps(corpo),
    )
    if codigo != 0:
        print(f"  FALHOU {caminho}: {erro or saida}")
        return False
    acao = "atualizado" if sha else "criado"
    print(f"  {caminho} {acao}")
    return True


def main():
    ap = argparse.ArgumentParser(description="Publica a lista M3U no GitHub e mostra o link raw")
    ap.add_argument("--repo", default=REPO_PADRAO, help=f"repositorio (padrao: {REPO_PADRAO})")
    ap.add_argument("--branch", default="", help="branch (padrao: a branch principal do repo)")
    ap.add_argument("--arquivo", action="append", default=None,
                    help="arquivo para publicar (pode repetir; padrao: canais.m3u + caches)")
    ap.add_argument("-m", "--mensagem", default="", help="mensagem do commit")
    ap.add_argument("--so-link", action="store_true", help="nao publica, so mostra o link raw")
    args = ap.parse_args()

    branch = args.branch or "master"

    if not args.so_link:
        verificar_gh()
        branch = args.branch or branch_padrao(args.repo)
        arquivos = args.arquivo or ARQUIVOS_PADRAO
        mensagem = args.mensagem or "chore: atualiza lista m3u"
        print(f"Publicando em {args.repo} ({branch}):")
        for arquivo in arquivos:
            publicar(args.repo, branch, arquivo, mensagem)

    base_raw = f"https://raw.githubusercontent.com/{args.repo}/{branch}"

    print()
    print("Link raw da lista:")
    print(f"  {base_raw}/canais.m3u")


if __name__ == "__main__":
    main()
