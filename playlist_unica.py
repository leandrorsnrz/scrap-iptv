#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gera UMA lista M3U unica juntando as duas fontes:

  * EmbedTV        -> embedtv_update.py       (cache: stream_urls.tsv)
  * Embed Canais   -> embedcanais_update.py   (cache: stream_urls_canais.tsv)

O resultado e um unico arquivo (padrao: canais.m3u) com os grupos unificados
("Filmes e Series" e "Filmes e Series" viram um so grupo, etc.), sem canais
repetidos e com os logos de cada fonte.

Depois de gerar, use o publicar.py (ou o atualizar_tudo.bat) para subir a lista
no GitHub e pegar o link raw.

Uso:
    python playlist_unica.py                  # so atualiza o que falta / venceu
    python playlist_unica.py --force          # re-extrai TODAS as URLs
    python playlist_unica.py -o canais.m3u    # arquivo de saida
    python playlist_unica.py --ttl 12         # renova URLs com mais de 12h
    python playlist_unica.py --workers 4      # mais rapido (pode tomar rate-limit)
    python playlist_unica.py --sem-dedupe     # nao remover canais repetidos

Requer somente a biblioteca padrao do Python (sem pip install).
"""

import argparse
import os
import sys
import time
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import embedcanais_update as ecn
import embedtv_update as etv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Grupos com nomes diferentes nas duas fontes -> nome unico no arquivo final
GRUPOS_CANONICOS = {
    "portugal": "Portugal",
    "esportes": "Esportes",
    "filmes e series": "Filmes e Séries",
    "infantil": "Infantil",
    "documentarios": "Documentários",
    "noticias": "Notícias",
    "abertos": "Canais Abertos",
    "canais abertos": "Canais Abertos",
    "religiosos": "Religiosos",
    "variedades": "Variedades",
}

# Ordem dos grupos na lista final (o que nao estiver aqui vai no fim, A-Z)
ORDEM_GRUPOS = [
    "Portugal",
    "Esportes",
    "Filmes e Séries",
    "Infantil",
    "Documentários",
    "Notícias",
    "Canais Abertos",
    "Religiosos",
    "Variedades",
]


def norm(texto):
    """Remove acentos, pontuacao e caixa para comparar nomes/grupos."""
    texto = unicodedata.normalize("NFKD", texto or "")
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return " ".join(texto.casefold().split())


def grupo_canonico(grupo):
    """Nome de grupo unificado entre as duas fontes."""
    return GRUPOS_CANONICOS.get(norm(grupo), (grupo or "Outros").strip() or "Outros")


def chave_ordem_grupo(grupo):
    chave = norm(grupo)
    nomes = [norm(g) for g in ORDEM_GRUPOS]
    return (nomes.index(chave), "") if chave in nomes else (len(nomes), chave)


# --------------------------------------------------------------------------- #
# Cache: "slug<TAB>url<TAB>epoch" (arquivos antigos de 2 colunas continuam ok)
# --------------------------------------------------------------------------- #
def carregar_cache(caminho):
    cache = {}
    if not caminho or not os.path.exists(caminho):
        return cache
    with open(caminho, "r", encoding="utf-8") as fh:
        for linha in fh:
            partes = linha.rstrip("\n").split("\t")
            if len(partes) < 2 or not partes[0]:
                continue
            stamp = 0.0
            if len(partes) >= 3:
                try:
                    stamp = float(partes[2])
                except ValueError:
                    stamp = 0.0
            cache[partes[0]] = (partes[1], stamp)
    return cache


def gravar_cache(caminho, cache):
    with open(caminho, "w", encoding="utf-8", newline="\n") as fh:
        for slug in sorted(cache):
            url, stamp = cache[slug]
            fh.write(f"{slug}\t{url}\t{int(stamp)}\n")


# --------------------------------------------------------------------------- #
# Leitura de cada fonte
# --------------------------------------------------------------------------- #
def ler_embedtv(args):
    """Canais do EmbedTV: lista de linhas + funcao que extrai a URL de um slug."""
    home = etv.http_get(etv.HOME_URL, timeout=args.timeout)
    canais = etv.parse_channels(home)
    if not canais:
        raise RuntimeError("nenhum canal encontrado em " + etv.HOME_URL)
    linhas = etv.build_rows(canais)

    def extrair(slug):
        return etv.extract_stream_url(slug, args.timeout, canais[slug].get("player"))

    return linhas, extrair


def ler_embedcanais(args):
    """Canais do Embed Canais: lista de linhas + funcao que extrai a URL de um slug."""
    home = ecn.http_get(ecn.HOME_URL, timeout=args.timeout)
    canais, ordem = ecn.parse_channels(home)
    if not canais:
        raise RuntimeError("nenhum canal encontrado em " + ecn.HOME_URL)
    linhas = ecn.build_rows(canais, ordem)

    def extrair(slug):
        return ecn.extract_stream_url(canais[slug], args.timeout)

    return linhas, extrair


FONTES = [
    {"nome": "EmbedTV", "ler": ler_embedtv, "cache": "stream_urls.tsv",
     "referer": None},
    {"nome": "Embed Canais", "ler": ler_embedcanais, "cache": "stream_urls_canais.tsv",
     "referer": ecn.PLAYER_REFERER},
]


def extrair_faltantes(rotulo, pendentes, extrair, cache, args):
    """Baixa as URLs que faltam/vencem, em paralelo, e devolve as que falharam."""
    if not pendentes:
        return []
    falhas = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futuros = {pool.submit(extrair, slug): slug for slug in pendentes}
        for futuro in as_completed(futuros):
            slug = futuros[futuro]
            try:
                cache[slug] = (futuro.result(), time.time())
                if not args.silencioso:
                    print(f"        OK      {slug}")
            except Exception as exc:
                falhas.append(slug)
                print(f"        FALHOU  {slug}: {exc}")
            if args.sleep:
                time.sleep(args.sleep)
    return falhas


def atualizar_fonte(fonte, args):
    """Le a fonte, atualiza a cache e devolve as entradas prontas para a M3U."""
    print(f"[{fonte['nome']}] lendo {fonte['cache']}")
    linhas, extrair = fonte["ler"](args)
    cache_path = os.path.join(BASE_DIR, fonte["cache"])
    cache = carregar_cache(cache_path)

    conhecidos = {linha["slug"] for linha in linhas}
    antigos = [slug for slug in cache if slug not in conhecidos]
    for slug in antigos:
        cache.pop(slug, None)
    if antigos:
        print(f"        {len(antigos)} canais antigos sairam da cache")

    limite = time.time() - (args.ttl * 3600) if args.ttl > 0 else None
    pendentes = []
    for slug in conhecidos:
        entrada = cache.get(slug)
        vencida = entrada is not None and limite is not None and entrada[1] < limite
        if args.force or entrada is None or vencida:
            pendentes.append(slug)

    print(f"        {len(conhecidos)} canais | {len(cache)} na cache | "
          f"{len(pendentes)} para baixar")
    falhas = extrair_faltantes(fonte["nome"], sorted(pendentes), extrair, cache, args)
    gravar_cache(cache_path, cache)

    entradas = []
    for linha in linhas:
        url = cache.get(linha["slug"], (None,))[0]
        if not url:
            continue
        entradas.append({
            "grupo": grupo_canonico(linha["group"]),
            "nome": linha["name"].strip(),
            "slug": linha["slug"],
            "logo": linha.get("logo") or "",
            "url": url,
            "referer": fonte["referer"],
            "fonte": fonte["nome"],
        })

    if falhas:
        print(f"        {len(falhas)} canais sem URL nesta rodada: {', '.join(sorted(falhas))}")
    return entradas


def montar_lista(entradas, dedupe=True):
    """Ordena, deduplica e garante tvg-id unico em toda a lista."""
    if dedupe:
        vistos = {}
        unicas = []
        for entrada in entradas:
            chave = (norm(entrada["grupo"]), norm(entrada["nome"]))
            if chave in vistos:
                continue
            vistos[chave] = True
            unicas.append(entrada)
        removidas = len(entradas) - len(unicas)
        entradas = unicas
    else:
        removidas = 0

    entradas.sort(key=lambda e: (chave_ordem_grupo(e["grupo"]), norm(e["nome"]), e["fonte"]))

    usados = set()
    for entrada in entradas:
        base = entrada["slug"] or f"canal-{abs(hash(entrada['nome']))}"
        tvg_id, n = base, 2
        while tvg_id in usados:
            tvg_id = f"{base}-{n}"
            n += 1
        usados.add(tvg_id)
        entrada["tvg_id"] = tvg_id

    return entradas, removidas


def gravar_m3u(caminho, entradas, com_referer=True):
    with open(caminho, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("#EXTM3U\n")
        for e in entradas:
            nome = e["nome"].replace('"', "'")
            grupo = e["grupo"].replace('"', "'")
            atributos = (f'tvg-id="{e["tvg_id"]}" tvg-name="{nome}" '
                         f'tvg-logo="{e["logo"]}" group-title="{grupo}"')
            fh.write(f"#EXTINF:-1 {atributos},{nome}\n")
            if com_referer and e["referer"]:
                fh.write(f"#EXTVLCOPT:http-referrer={e['referer']}\n")
            fh.write(f"{e['url']}\n")


def main():
    ap = argparse.ArgumentParser(
        description="Gera uma unica lista M3U juntando EmbedTV + Embed Canais")
    ap.add_argument("-o", "--output", default=os.path.join(BASE_DIR, "canais.m3u"),
                    help="arquivo M3U de saida (padrao: canais.m3u)")
    ap.add_argument("-f", "--force", action="store_true",
                    help="re-extrai as URLs de TODOS os canais, ignorando a cache")
    ap.add_argument("--ttl", type=float, default=24.0,
                    help="renova URLs com mais de N horas (padrao 24; 0 = nunca)")
    ap.add_argument("-w", "--workers", type=int, default=4,
                    help="downloads em paralelo (padrao 4)")
    ap.add_argument("--sleep", type=float, default=0.2,
                    help="pausa entre requisicoes em segundos (padrao 0.2)")
    ap.add_argument("--timeout", type=int, default=20,
                    help="timeout de cada requisicao em segundos (padrao 20)")
    ap.add_argument("--sem-referrer", action="store_true",
                    help="nao gravar as linhas #EXTVLCOPT:http-referrer")
    ap.add_argument("--sem-dedupe", action="store_true",
                    help="nao remover canais repetidos entre as duas fontes")
    ap.add_argument("--tolerar-falhas", action="store_true",
                    help="se uma fonte cair, gera a lista so com a outra")
    ap.add_argument("-q", "--silencioso", action="store_true",
                    help="nao imprimir um OK para cada canal baixado")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    todas, falhas_fonte = [], []
    for fonte in FONTES:
        try:
            todas.extend(atualizar_fonte(fonte, args))
        except Exception as exc:
            print(f"        ERRO em {fonte['nome']}: {exc}")
            falhas_fonte.append(fonte["nome"])

    if falhas_fonte and not args.tolerar_falhas:
        sys.exit("Nada foi gravado: falhou " + ", ".join(falhas_fonte) +
                 ".\nUse --tolerar-falhas para gerar so com a fonte que respondeu.")
    if not todas:
        sys.exit("ERRO: nenhum canal com URL de stream; arquivo nao foi gravado.")

    entradas, removidas = montar_lista(todas, dedupe=not args.sem_dedupe)

    if os.path.dirname(args.output):
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    gravar_m3u(args.output, entradas, com_referer=not args.sem_referrer)

    por_fonte = Counter(e["fonte"] for e in entradas)
    por_grupo = Counter(e["grupo"] for e in entradas)
    print()
    print(f"Lista gravada: {args.output}")
    print(f"  canais: {len(entradas)}"
          + (f" ({removidas} repetidos removidos)" if removidas else ""))
    print("  fontes: " + " | ".join(f"{k}={v}" for k, v in por_fonte.most_common()))
    print("  grupos: " + " | ".join(f"{k}={v}" for k, v in por_grupo.most_common()))


if __name__ == "__main__":
    main()
