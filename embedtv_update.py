#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
embedtv_update.py — Atualiza a playlist M3U dos canais do embedtv.lat e embedcanais.online

Uso:
    python embedtv_update.py                 # gera embedtv.m3u na pasta do script
    python embedtv_update.py -o saida.m3u    # escolhe o arquivo de saida
    python embedtv_update.py -j 4            # define a concorrencia (padrao: 8)
    python embedtv_update.py -n 10           # testa rapido so os primeiros N canais
    python embedtv_update.py -v              # modo verboso (mostra detalhes/erros)

Sem dependencias externas (somente biblioteca padrao do Python 3).
"""

import argparse
import concurrent.futures
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request

HOME_URL = "https://embedtv.lat/"
PLAYER_URL = "https://w7.embedtv.lat/{cid}"
EMBEDCANAIS_HOME = "https://embedcanais.online/"
EMBEDCANAIS_PLAYER = "https://embedcanaisdetv.xyz/e/index.php?canal={cid}"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

ARTICLE_RE = re.compile(r'<article\b.*?</article>', re.S)
ID_RE = re.compile(r'data-id="([^"]+)"')
BUSCA_RE = re.compile(r'data-busca="([^"]*)"')
ALT_RE = re.compile(r'alt="([^"]+)"')
MANIFEST_RE = re.compile(r'https://[a-z0-9.-]*cloudfront[^"\'\s]*/stream/[^"\'\s]+', re.I)
SRC_RE = re.compile(r'const\s+src\s*=\s*"([^"]+)"')
M3U8_RE = re.compile(r'https://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', re.I)
CHANNELS_JS_RE = re.compile(r'const\s+CHANNELS\s*=\s*(\[.*?\]);', re.S)

# Canais que nao têm logo com alt na home: nome limpo fixo.
NAME_FIXES = {
    "max4": "Max 4",
    "max5": "Max 5",
    "max6": "Max 6",
}


def prettify_id(cid):
    """Deriva um nome legivel do id (ex.: 24h_simpsons -> 24h Simpsons)."""
    s = cid.replace("_", " ").replace("-", " ").strip()
    words = []
    for w in s.split():
        if w.islower() and w.isalpha() and len(w) <= 3:
            words.append(w.upper())
        elif w.isalpha() and w.islower():
            words.append(w.capitalize())
        else:
            words.append(w)
    return " ".join(words)


def http_get(url, timeout=25, referer=None):
    headers = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7"}
    if referer:
        headers["Referer"] = referer
    else:
        headers["Referer"] = HOME_URL
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def get_channels(html):
    """Extrai (nome, id) dos canais, um por id, sem duplicatas.

    Cada card da home e um <article data-id="..." data-busca="...">. O mesmo
    id pode repetir (o canal aparece em varios eventos); mantemos um nome de
    canal limpo a partir do atributo alt da logo (ex.: "SBT SP"), usando o
    data-busca apenas como reserva.
    """
    by_id = {}  # cid -> (nome, veio_do_alt)
    for art in ARTICLE_RE.findall(html):
        mid = ID_RE.search(art)
        if not mid:
            continue
        cid = mid.group(1).strip()
        if not cid:
            continue
        alt = ALT_RE.search(art)
        busca = BUSCA_RE.search(art)
        alt_name = alt.group(1).strip() if alt else ""
        busca_name = busca.group(1).strip() if busca else ""
        nome, from_alt = by_id.get(cid, ("", False))
        # Prefere sempre o alt (nome real do canal); senao data-busca; senao o id.
        if alt_name and not from_alt:
            by_id[cid] = (alt_name, True)
        elif not nome:
            by_id[cid] = (busca_name or cid, False)
    out = []
    for cid, (nome, from_alt) in by_id.items():
        if not from_alt:
            # Sem logo com alt: o nome herdado costuma ser o texto de um evento
            # (ex.: "Liga dos Campeoes X vs Y"). Usa nome fixo/derivado do id.
            nome = NAME_FIXES.get(cid) or prettify_id(cid) or cid
        out.append((nome, cid))
    return out


def get_manifest(cid, tries=2, verbose=False):
    """Busca a URL do manifesto HLS (file.txt) na pagina do player."""
    last = None
    player_url = PLAYER_URL.format(cid=cid)
    for attempt in range(tries):
        try:
            html = http_get(player_url, referer=HOME_URL)
            m = MANIFEST_RE.search(html)
            if m:
                return m.group(0)
            m = SRC_RE.search(html)
            if m:
                return m.group(1)
            m = M3U8_RE.search(html)
            if m:
                return m.group(0)
            last = "pagina sem URL de manifesto"
        except Exception as e:
            last = f"erro HTTP: {e}"
        if verbose and attempt + 1 < tries:
            print(f"  [retry {attempt+1}/{tries}] {cid}: {last}", file=sys.stderr)
        if attempt + 1 < tries:
            time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(last)


def fetch_one(item, verbose=False):
    name, cid = item
    try:
        url = get_manifest(cid, verbose=verbose)
        return (name, cid, url, None)
    except Exception as e:
        return (name, cid, None, str(e))


def get_channels_embedcanais(html):
    """Extrai (nome, slug) dos canais do embedcanais.online a partir do JS CHANNELS."""
    m = CHANNELS_JS_RE.search(html)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except Exception:
        return []
    out = []
    seen = set()
    for c in data:
        if not isinstance(c, dict):
            continue
        slug = (c.get("slug") or c.get("id") or "").strip()
        name = (c.get("name") or slug).strip()
        if not slug or slug in seen:
            continue
        seen.add(slug)
        out.append((name, slug))
    return out


def get_manifest_embedcanais(slug, tries=2, verbose=False):
    """Busca URL .m3u8 na página do player embedcanaisdetv.xyz."""
    last = None
    url_player = EMBEDCANAIS_PLAYER.format(cid=slug)
    for attempt in range(tries):
        try:
            html = http_get(url_player, referer=EMBEDCANAIS_HOME)
            m = M3U8_RE.search(html)
            if m:
                manifest = m.group(0)
                return manifest
            m2 = re.search(r'"([^"]+\.m3u8[^"]*)"', html)
            if m2:
                return m2.group(1)
            m3 = re.search(r"src:\s*['\"]([^'\"]+\.m3u8[^'\"]*)['\"]", html)
            if m3:
                return m3.group(1)
            last = "pagina sem URL .m3u8"
        except Exception as e:
            last = f"erro HTTP: {e}"
        if verbose and attempt + 1 < tries:
            print(f"  [retry {attempt+1}/{tries}] {slug}: {last}", file=sys.stderr)
        if attempt + 1 < tries:
            time.sleep(0.6 * (attempt + 1))
    raise RuntimeError(last)


def fetch_one_embedcanais(item, verbose=False):
    name, slug = item
    try:
        url = get_manifest_embedcanais(slug, verbose=verbose)
        return (name, slug, url, None)
    except Exception as e:
        return (name, slug, None, str(e))


def main():
    ap = argparse.ArgumentParser(description="Atualiza playlist M3U do embedtv.lat e embedcanais.online")
    ap.add_argument("-o", "--output", help="arquivo M3U de saida (padrao: embedtv.m3u ao lado do script)")
    ap.add_argument("-j", "--jobs", type=int, default=6, help="concorrencia (padrao: 6)")
    ap.add_argument("-n", "--limit", type=int, default=0, help="processa so os primeiros N canais (teste)")
    ap.add_argument("-v", "--verbose", action="store_true", help="mostra erros e tentativas")
    ap.add_argument("--only-embedcanais", action="store_true", help="processa apenas embedcanais.online")
    ap.add_argument("--only-embedtv", action="store_true", help="processa apenas embedtv.lat")
    args = ap.parse_args()

    out_path = args.output or os.path.join(os.path.dirname(os.path.abspath(__file__)), "embedtv.m3u")

    all_channels_embedtv = []
    all_channels_embedcanais = []

    if not args.only_embedcanais:
        print(f"[1/3] Buscando lista de canais em {HOME_URL}")
        try:
            html = http_get(HOME_URL, referer=HOME_URL)
            channels = get_channels(html)
            if channels:
                print(f"      {len(channels)} canais encontrados (embedtv.lat)")
                all_channels_embedtv = channels
            else:
                print("      Nenhum canal encontrado (embedtv.lat)", file=sys.stderr)
        except Exception as e:
            print(f"ERRO: nao consegui acessar {HOME_URL}: {e}", file=sys.stderr)

    if not args.only_embedtv:
        print(f"[2/3] Buscando lista de canais em {EMBEDCANAIS_HOME}")
        try:
            html = http_get(EMBEDCANAIS_HOME, referer=EMBEDCANAIS_HOME)
            channels = get_channels_embedcanais(html)
            if channels:
                print(f"      {len(channels)} canais encontrados (embedcanais.online)")
                all_channels_embedcanais = channels
            else:
                print("      Nenhum canal encontrado (embedcanais.online)", file=sys.stderr)
        except Exception as e:
            print(f"ERRO: nao consegui acessar {EMBEDCANAIS_HOME}: {e}", file=sys.stderr)

    # Montar lista com origem correta
    channels = []
    seen = set()
    for name, cid in all_channels_embedtv:
        key = ('embedtv', cid)
        if key in seen:
            continue
        seen.add(key)
        channels.append(('embedtv', name, cid))
    for name, cid in all_channels_embedcanais:
        key = ('embedc', cid)
        if key in seen:
            continue
        seen.add(key)
        channels.append(('embedc', name, cid))

    if not channels:
        print("ERRO: nenhum canal encontrado em nenhuma fonte.", file=sys.stderr)
        sys.exit(1)

    if args.limit:
        channels = channels[:args.limit]
    print(f"      Total unico: {len(channels)} canais")

    print(f"[3/3] Buscando URLs de manifesto ({args.jobs} conexoes simultaneas)...")
    ok, fails = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futures = {}
        for c in channels:
            src, name, cid = c
            if src == 'embedtv':
                futures[ex.submit(fetch_one, (name, cid), args.verbose)] = ('embedtv', name, cid)
            else:
                futures[ex.submit(fetch_one_embedcanais, (name, cid), args.verbose)] = ('embedc', name, cid)
        done = 0
        for fut in concurrent.futures.as_completed(futures):
            done += 1
            src, name, cid = futures[fut]
            res = fut.result()
            rname, rcid, url, err = res
            if url:
                ok.append((rname, rcid, url))
            else:
                fails.append((src, rname, rcid, err))
            print(f"      {done}/{len(channels)}", end="\r")

    if fails:
        print(f"\n      {len(fails)} falharam na 1a tentativa, tentando de novo...")
        second = []
        for src, name, cid, err in fails:
            time.sleep(0.5)
            try:
                if src == 'embedtv':
                    url = get_manifest(cid, tries=1, verbose=args.verbose)
                else:
                    url = get_manifest_embedcanais(cid, tries=1, verbose=args.verbose)
                ok.append((name, cid, url))
                second.append((src, name, cid, None))
            except Exception as e:
                second.append((src, name, cid, str(e)))
        fails = [(s, n, c, e) for s, n, c, e in second if e]
        for s, n, c, e in second:
            if e and args.verbose:
                print(f"  FALHA {c} ({n}) [{s}]: {e}", file=sys.stderr)

    if not ok:
        print("ERRO: nenhum canal foi extraido.", file=sys.stderr)
        sys.exit(1)

    print(f"\n      Gravando {len(ok)} canais em {out_path}")
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("#EXTM3U\n")
        for name, cid, url in ok:
            f.write(f"#EXTINF:-1,{name}\n{url}\n")
    os.replace(tmp, out_path)

    print(f"Pronto: {len(ok)}/{len(channels)} canais em {out_path}")
    if fails:
        print("Nao atualizados:")
        for src, name, cid, err in fails:
            print(f"  - {name} ({cid}) [{src}]: {err}")
        sys.exit(2)


if __name__ == "__main__":
    main()
