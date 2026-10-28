#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Atualizador da lista M3U de canais do Embed Canais (https://embedcanais.online/)

Gera uma lista M3U com nome, logo e URL de stream real (index.m3u8) de cada
canal, igual ao que o player do proprio site usa.

A lista de canais vem do array "const CHANNELS=[...]" embutido na pagina
inicial, e a URL do stream fica na pagina do player
(https://embedcanaisdetv.xyz/e/index.php?canal=SLUG/).

Importante: a CDN do stream (cdnNNembed.xyz) exige o cabecalho Referer de
embedcanaisdetv.xyz (a m3u8 e os segmentos .ts devolvem 403 sem ele).
Por padrao o script grava, depois de cada canal, a linha

    #EXTVLCOPT:http-referrer=https://embedcanaisdetv.xyz/

que faz o VLC (e players que a entendem) enviar o Referer corretamente.
Players que nao enviam Referer podem nao conseguir abrir os streams.
Use --sem-referrer para gerar a M3U sem essas linhas.

Uso:
    python embedcanais_update.py                 # usa a cache; extrai so o que faltar
    python embedcanais_update.py --force         # re-extrai as URLs de TODOS os canais
    python embedcanais_update.py -o minha.m3u -c cache.tsv
    python embedcanais_update.py --workers 4     # mais rapido (pode tomar rate-limit)
    python embedcanais_update.py --sem-referrer  # M3U pura, sem linhas EXTVLCOPT

Requer somente a biblioteca padrao do Python (sem pip install).
"""

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

HOME_URL = "https://embedcanais.online/"
PLAYER_REFERER = "https://embedcanaisdetv.xyz/"

# Array JSON embutido na pagina inicial com todos os canais
CHANNELS_RE = re.compile(r'const CHANNELS\s*=\s*(\[)')

# URL do stream na pagina do player (host da CDN de embed + caminho .m3u8)
STREAM_RE = re.compile(r'https://[a-z0-9.-]*\.cdn\d+embed\.xyz/[^"\'\s<>\\]+\.m3u8')
ANY_M3U8_RE = re.compile(r'https?://[^"\'\s<>\\]+\.m3u8')


def http_get(url, timeout=20, tries=2, referer=None):
    """Baixa uma URL como texto, com tentativas e cabecalhos de navegador."""
    last = None
    headers = {"User-Agent": UA, "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer
    for attempt in range(tries):
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except (URLError, HTTPError, TimeoutError, OSError) as exc:
            last = exc
            time.sleep(1.0 * (attempt + 1))
    raise last


def parse_channels(home_html):
    """Extrai slug, nome, categoria, logo e URL do player de cada canal.

    Retorna (canais, ordem): canais e um dict slug->info (dedupe pelo slug,
    mantendo a primeira aparicao) e ordem e a posicao de cada slug no site,
    usada como prioridade quando o mesmo canal aparece em duas categorias.
    """
    m = CHANNELS_RE.search(home_html)
    if not m:
        return {}, {}
    data, _ = json.JSONDecoder().raw_decode(home_html, m.start(1))

    channels = {}
    order = {}
    for c in data:
        slug = str(c.get("slug") or "").strip()
        if not slug:
            continue
        name = str(c.get("name") or slug).strip() or slug
        cat = str(c.get("category") or "").strip() or "Outros"
        logo = str(c.get("image") or "").strip()
        href = str(c.get("href") or "").strip() or \
            f"https://embedcanaisdetv.xyz/e/index.php?canal={slug}/"

        if slug in channels:
            continue  # mesmo canal em duas categorias (ex.: TNT)
        order[slug] = len(order)
        channels[slug] = {"slug": slug, "name": name, "category": cat, "logo": logo, "href": href}

    return channels, order


def extract_stream_url(info, timeout=20):
    """Abre a pagina do player do canal e extrai a URL real do stream (.m3u8)."""
    page = http_get(info["href"], timeout=timeout, referer=HOME_URL)
    slug = info["slug"]

    cands = STREAM_RE.findall(page)
    same = [u for u in cands if slug in u]  # preferencia: o stream do proprio canal
    if same:
        return same[0]
    if cands:
        return cands[0]
    m = ANY_M3U8_RE.search(page)
    if m:
        return m.group(0)
    raise RuntimeError("nenhum stream .m3u8 encontrado na pagina do player")


def load_cache(path):
    cache = {}
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.rstrip("\n")
                if "\t" in line:
                    s, u = line.split("\t", 1)
                    cache[s] = u
    return cache


def build_rows(channels, order):
    """Ordena os canais: categorias na ordem do site, canais em ordem alfabetica."""
    rows = []
    for slug, info in channels.items():
        rows.append({"group": info["category"], "name": info["name"],
                     "slug": slug, "logo": info["logo"], "order": order[slug]})

    cat_order = {}
    for r in rows:
        cat_order.setdefault(r["group"], r["order"])
    rows.sort(key=lambda r: (cat_order[r["group"]], r["name"].casefold()))
    return rows


def main():
    ap = argparse.ArgumentParser(description="Atualiza a lista M3U de canais do Embed Canais")
    ap.add_argument("-o", "--output", default="embedcanais.m3u",
                    help="arquivo M3U de saida (padrao: embedcanais.m3u)")
    ap.add_argument("-c", "--cache", default="stream_urls_canais.tsv",
                    help="arquivo de cache slug->URL (padrao: stream_urls_canais.tsv)")
    ap.add_argument("-f", "--force", action="store_true",
                    help="re-extrair as URLs de stream de todos os canais, ignorando a cache")
    ap.add_argument("-w", "--workers", type=int, default=1,
                    help="numero de downloads em paralelo (padrao 1; valores altos podem sofrer rate-limit)")
    ap.add_argument("--sleep", type=float, default=0.35,
                    help="pausa entre requisicoes em segundos (padrao 0.35)")
    ap.add_argument("--timeout", type=int, default=20,
                    help="timeout de cada requisicao em segundos (padrao 20)")
    ap.add_argument("--sem-referrer", action="store_true",
                    help="nao gravar as linhas #EXTVLCOPT:http-referrer na M3U")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    # 1) Pagina inicial: lista CHANNELS com slug, nome, categoria e logo
    print(f"[1/3] Buscando canais em {HOME_URL}")
    home = http_get(HOME_URL, timeout=args.timeout)
    channels, order = parse_channels(home)
    if not channels:
        sys.exit("ERRO: nenhum canal encontrado na pagina inicial (estrutura mudou?)")
    print(f"      {len(channels)} canais unicos")

    # 2) URLs de stream (uma por slug, via pagina do player)
    cache = load_cache(args.cache)
    known = set(channels)
    stale = [s for s in cache if s not in known]
    missing = [s for s in known if args.force or s not in cache]
    if stale:
        print(f"      {len(stale)} canais antigos removidos da cache")
        for s in stale:
            cache.pop(s, None)
    print(f"[2/3] URLs de stream: {len(cache)} na cache, {len(missing)} para extrair")

    if missing:
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = {pool.submit(extract_stream_url, channels[slug], args.timeout): slug
                       for slug in missing}
            for future in as_completed(futures):
                slug = futures[future]
                try:
                    cache[slug] = future.result()
                    print(f"      OK   {slug}")
                except Exception as exc:
                    print(f"      FALHOU {slug}: {exc}")
                time.sleep(args.sleep)

    # grava a cache atualizada
    with open(args.cache, "w", encoding="utf-8", newline="\n") as fh:
        for slug in sorted(cache):
            fh.write(f"{slug}\t{cache[slug]}\n")

    # 3) Monta e grava a M3U
    print(f"[3/3] Gerando {args.output}")
    rows = build_rows(channels, order)
    written = 0
    sem_url = []
    with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("#EXTM3U\n")
        for r in rows:
            url = cache.get(r["slug"])
            if not url:
                sem_url.append(r["slug"])
                continue
            name = r["name"].replace('"', "'")
            attrs = (f'tvg-id="{r["slug"]}" tvg-name="{name}" '
                     f'tvg-logo="{r["logo"]}" group-title="{r["group"]}"')
            fh.write(f"#EXTINF:-1 {attrs},{name}\n")
            if not args.sem_referrer:
                fh.write(f"#EXTVLCOPT:http-referrer={PLAYER_REFERER}\n")
            fh.write(f"{url}\n")
            written += 1

    if sem_url:
        print(f"      AVISO: sem URL de stream (pulados): {', '.join(sem_url)}")
    print(f"      {written} canais gravados em {args.output}")
    grupos = Counter(r["group"] for r in rows if r["slug"] in cache)
    print("      Grupos: " + ", ".join(f"{g}={n}" for g, n in grupos.most_common()))
    if missing:
        falhas = [s for s in missing if s not in cache]
        if falhas:
            print(f"      {len(falhas)} canais falharam: {', '.join(falhas)}")
            print("      Rode o script de novo para tentar extrair apenas os que faltam.")


if __name__ == "__main__":
    main()
