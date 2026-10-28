@echo off
chcp 65001 >nul
title Atualizar tudo (EmbedTV + Embed Canais)
cd /d "%~dp0"

echo ==================================================
echo   1/2  Atualizando as duas fontes e montando a lista
echo ==================================================
echo.

set PY=
where python >nul 2>nul && set PY=python
if not defined PY (
    where py >nul 2>nul && set PY=py
)
if not defined PY (
    echo [ERRO] Python nao encontrado.
    echo Instale o Python em https://www.python.org/downloads/
    echo e marque a opcao "Add Python to PATH".
    echo.
    pause
    exit /b 1
)

%PY% playlist_unica.py %*
if errorlevel 1 (
    echo.
    echo [ERRO] A lista nao foi gerada. O arquivo antigo no GitHub continua igual.
    echo.
    pause
    exit /b 1
)

echo.
echo ==================================================
echo   2/2  Publicando no GitHub (raw)
echo ==================================================
echo.
%PY% publicar.py
if errorlevel 1 (
    echo.
    echo [ERRO] Falha ao publicar. Verifique o login com: gh auth login
    echo A lista foi gerada localmente em canais.m3u.
    echo.
    pause
    exit /b 1
)

echo.
echo ==================================================
echo   Concluido! Copie o link raw acima e use no player.
echo ==================================================
echo.
pause
