#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Publica a lista gerada no GitHub (via GitHub CLI) e mostra o link raw.

Usa `gh api` com o token que ja esta logado (gh auth login), entao nao precisa
de git, nem de clonar o repositorio, nem de criar token na mao. Tudo o que for
publicado vai em UM commit so.

Uso:
    python publicar.py                                  # sobe canais.m3u + caches
    python publicar.py --arquivo canais.m3u
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


def gh_json(metodo, rota, corpo=None):
    """Chama a API do GitHub e devolve o JSON da resposta (ou None)."""
    argumentos = ["api", "--method", metodo, rota]
    if corpo is not None:
        argumentos += ["--input", "-"]
    codigo, saida, erro = rodar_gh(argumentos, json.dumps(corpo) if corpo is not None else None)
    if codigo != 0:
        raise RuntimeError(erro or saida or f"falha em {metodo} {rota}")
    return json.loads(saida) if saida else None


def verificar_gh():
    try:
        codigo, saida, erro = rodar_gh(["auth", "status"])
    except FileNotFoundError:
        sys.exit("ERRO: GitHub CLI (gh) nao encontrado. Instale em https://cli.github.com/\n"
                 "Depois rode: gh auth login")
    if codigo != 0:
        sys.exit("ERRO: GitHub CLI sem login.\nRode: gh auth login\n\n" + (erro or saida))


def branch_padrao(repo):
    dados = gh_json("GET", f"repos/{repo}")
    return (dados or {}).get("default_branch") or "master"


def publicar(repo, branch, caminhos, mensagem):
    """Cria um commit unico com todos os arquivos e atualiza a branch."""
    ref = gh_json("GET", f"repos/{repo}/git/ref/heads/{branch}")
    pai = ref["object"]["sha"]
    arvore_pai = gh_json("GET", f"repos/{repo}/git/commits/{pai}")["tree"]["sha"]

    itens = []
    for caminho in caminhos:
        with open(caminho, "rb") as fh:
            conteudo = base64.b64encode(fh.read()).decode("ascii")
        blob = gh_json("POST", f"repos/{repo}/git/blobs",
                       {"content": conteudo, "encoding": "base64"})
        destino = os.path.relpath(os.path.abspath(caminho), BASE_DIR)
        if destino.startswith(".."):
            destino = os.path.basename(caminho)
        itens.append({"path": destino.replace(os.sep, "/"), "mode": "100644",
                      "type": "blob", "sha": blob["sha"]})
        print(f"  enviado: {destino}")

    arvore = gh_json("POST", f"repos/{repo}/git/trees",
                     {"base_tree": arvore_pai, "tree": itens})
    commit = gh_json("POST", f"repos/{repo}/git/commits",
                     {"message": mensagem, "tree": arvore["sha"], "parents": [pai]})
    gh_json("PATCH", f"repos/{repo}/git/refs/heads/{branch}",
            {"sha": commit["sha"], "force": False})
    print(f"  commit: {commit['sha'][:10]} em {branch}")


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
        caminhos = args.arquivo or [os.path.join(BASE_DIR, a) for a in ARQUIVOS_PADRAO]
        faltando = [c for c in caminhos if not os.path.exists(c)]
        caminhos = [c for c in caminhos if os.path.exists(c)]
        for caminho in faltando:
            print(f"  pulando {caminho} (nao existe)")
        if not caminhos:
            sys.exit("ERRO: nenhum arquivo para publicar.")
        mensagem = args.mensagem or "chore: atualiza lista m3u"
        print(f"Publicando em {args.repo} ({branch}):")
        publicar(args.repo, branch, caminhos, mensagem)

    print()
    print("Link raw da lista:")
    print(f"  https://raw.githubusercontent.com/{args.repo}/{branch}/canais.m3u")


if __name__ == "__main__":
    main()
