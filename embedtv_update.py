#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Atualizador da lista M3U de canais do EmbedTV (https://embedtv.cc/)

Gera uma lista M3U com nome, logo e URL de stream real (HLS na CDN cloudfront)
de cada canal, igual ao que o player do proprio site usa.

Uso:
    python embedtv_update.py                 # usa a cache; extrai so o que faltar
    python embedtv_update.py --force         # re-extrai as URLs de TODOS os canais
    python embedtv_update.py -o minha.m3u -c cache.tsv
    python embedtv_update.py --workers 4     # mais rapido (pode tomar rate-limit do CDN)

Requer somente a biblioteca padrao do Python (sem pip install).
"""

import argparse
import html
import os
import re
import sys
import time
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

HOME_URL = "https://embedtv.cc/"
PLAYER_DOMAIN = "embedtv.cc"
PLAYER_HOSTS = ("ww0", "ww1", "ww2", "ww3")   # hosts de player conhecidos (fallback)
IMG_BASE = "https://img.faz-o-eli.online"

# Ordem de prioridade quando um canal aparece em mais de uma secao
PRIORITY = ["Portugal", "Esportes", "Infantil", "Documentarios",
            "Noticias", "Filmes e Series", "Abertos", "Variedades"]

# Canais cujo logo padrao (slug.png) esta quebrado no proprio site -> usar preview
BROKEN_LOGO = {"pt_canal11": "pt_canal11.prev.png"}

# Cards de canal: <article data-id="slug" data-busca="Nome"> ... </article>
CARD_RE = re.compile(r'<article\s[^>]*data-id="([^"]*)"[^>]*data-busca="([^"]*)"')
# Cabecalho de cada secao/categoria da pagina
SECAO_RE = re.compile(r'aria-labelledby="titulo-cat-\d+"')
SECAO_TITULO_RE = re.compile(r'<span class="truncate">([^<]+)</span>')
LOGO_RE = re.compile(r'<img[^>]+src="([^"]+)"')
LINK_RE = re.compile(r'value="(//[^"]+)"')
# Player: const src = "...";  (a CDN responde a mesma playlist em .txt/.m3u8)
STREAM_SRC_RE = re.compile(r'const\s+src\s*=\s*"([^"]+)"')
STREAM_OLD_RE = re.compile(r'https://[a-z0-9.-]*cloudfront[^"\'< ]*?file\.txt')


def http_get(url, timeout=20, tries=2):
    """Baixa uma URL como texto, com tentativas e User-Agent de navegador."""
    last = None
    for attempt in range(tries):
        try:
            req = Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except (URLError, HTTPError, TimeoutError, OSError) as exc:
            last = exc
            time.sleep(1.0 * (attempt + 1))
    raise last


def parse_channels(home_html):
    """Extrai slug, nome, logo, secao e link do player de cada card da pagina."""
    secoes = []
    for m in SECAO_RE.finditer(home_html):
        titulo = SECAO_TITULO_RE.search(home_html, m.end(), m.end() + 1500)
        if titulo:
            secoes.append((m.start(), html.unescape(titulo.group(1)).strip()))

    cards = list(CARD_RE.finditer(home_html))
    channels = {}

    for i, m in enumerate(cards):
        pos = m.start()
        end = cards[i + 1].start() if i + 1 < len(cards) else min(len(home_html), pos + 6000)
        chunk = home_html[pos:end]

        secao = ""
        for s_pos, s_nome in secoes:
            if s_pos < pos:
                secao = s_nome
            else:
                break
        if not secao:
            continue  # cards de evento do topo, sem categoria (mudam toda hora)

        slug = html.unescape(m.group(1)).strip()
        if not slug:
            continue
        nome = html.unescape(m.group(2)).strip() or slug

        logo = ""
        for img in LOGO_RE.finditer(chunk):
            src = img.group(1)
            if not src.startswith("http") or src.endswith(".prev.png"):
                continue
            if src.endswith("/.png"):
                continue
            logo = src
            break

        link = LINK_RE.search(chunk)
        player = "https:" + link.group(1) if link else ""

        info = channels.setdefault(slug, {"sections": [], "names": {}, "logos": {},
                                          "player": player})
        if not info["player"]:
            info["player"] = player
        info["sections"].append(secao)
        info["names"].setdefault(secao, nome)
        info["logos"].setdefault(secao, logo)

    return channels


def normalizar_stream(url):
    """Troca o nome do arquivo por index.m3u8 (a CDN aceita qualquer extensao)."""
    base, _, arquivo = url.rpartition("/")
    if not base or arquivo.endswith(".m3u8"):
        return url
    return f"{base}/index.m3u8"


def extract_stream_url(slug, timeout=20, player_url=None):
    """Abre a pagina do player do canal e extrai a URL real do stream (HLS)."""
    urls = [player_url] if player_url else []
    urls += [f"https://{host}.{PLAYER_DOMAIN}/{slug}" for host in PLAYER_HOSTS]

    ultimo_erro = None
    for url in urls:
        try:
            page = http_get(url, timeout=timeout)
        except Exception as exc:
            ultimo_erro = exc
            continue
        m = STREAM_SRC_RE.search(page)
        if m:
            return normalizar_stream(m.group(1))
        m = STREAM_OLD_RE.search(page)
        if m:
            return m.group(0)
    raise RuntimeError(f"nenhum stream encontrado na pagina do player ({ultimo_erro})")


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


def norm(s):
    """Remove acentos e normaliza para comparacoes de nome de secao."""
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).casefold()


def pick_group(secs):
    """Escolhe a secao/grupo final seguindo a PRIORITY (comparacao sem acentos)."""
    nprio = [norm(p) for p in PRIORITY]
    for p in nprio:
        for s in secs:
            if norm(s) == p:
                return s  # nome real da secao como aparece no site
    return secs[0] if secs else "Outros"


def build_rows(channels):
    """Define a secao/grupo final, nome e logo de cada canal (dedupe entre secoes)."""
    rows = []
    for slug, info in channels.items():
        secs = [s for s in info["sections"] if s]
        group = pick_group(secs)
        name = info["names"].get(group) or next(iter(info["names"].values()))
        logo = info["logos"].get(group) or f"{IMG_BASE}/{slug}.png"
        if slug in BROKEN_LOGO:
            logo = f"{IMG_BASE}/{BROKEN_LOGO[slug]}"
        rows.append({"group": group, "name": name, "slug": slug, "logo": logo,
                     "player": info.get("player", "")})

    nprio = [norm(p) for p in PRIORITY]
    rows.sort(key=lambda r: (nprio.index(norm(r["group"])) if norm(r["group"]) in nprio else 99,
                             r["name"].lower()))
    return rows


def main():
    ap = argparse.ArgumentParser(description="Atualiza a lista M3U de canais do EmbedTV")
    ap.add_argument("-o", "--output", default="embedtv_canais.m3u",
                    help="arquivo M3U de saida (padrao: embedtv_canais.m3u)")
    ap.add_argument("-c", "--cache", default="stream_urls.tsv",
                    help="arquivo de cache slug->URL (padrao: stream_urls.tsv)")
    ap.add_argument("-f", "--force", action="store_true",
                    help="re-extrair as URLs de stream de todos os canais, ignorando a cache")
    ap.add_argument("-w", "--workers", type=int, default=1,
                    help="numero de downloads em paralelo (padrao 1; valores altos podem sofrer rate-limit)")
    ap.add_argument("--sleep", type=float, default=0.35,
                    help="pausa entre requisicoes em segundos (padrao 0.35)")
    ap.add_argument("--timeout", type=int, default=20,
                    help="timeout de cada requisicao em segundos (padrao 20)")
    args = ap.parse_args()

    # 1) Pagina inicial: canais, nomes, logos, secoes
    print(f"[1/3] Buscando canais em {HOME_URL}")
    home = http_get(HOME_URL, timeout=args.timeout)
    channels = parse_channels(home)
    if not channels:
        sys.exit("ERRO: nenhum canal encontrado na pagina inicial (estrutura mudou?)")
    print(f"      {len(channels)} canais unicos")

    # 2) URLs de stream
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
            futures = {pool.submit(extract_stream_url, slug, args.timeout): slug for slug in missing}
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
    rows = build_rows(channels)
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
