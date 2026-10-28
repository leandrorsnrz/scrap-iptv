@echo off
chcp 65001 >nul
title Atualizar Canais Embed Canais
cd /d "%~dp0"

echo ==========================================
echo   Atualizando canais Embed Canais...
echo ==========================================
echo.

where python >nul 2>nul
if %errorlevel%==0 (
    python embedcanais_update.py %*
) else (
    where py >nul 2>nul
    if %errorlevel%==0 (
        py embedcanais_update.py %*
    ) else (
        echo [ERRO] Python nao encontrado.
        echo Instale o Python em https://www.python.org/downloads/
        echo e marque a opcao "Add Python to PATH".
        echo.
        pause
        exit /b 1
    )
)

echo.
echo ==========================================
echo   Concluido! Playlist: %~dp0embedcanais.m3u
echo ==========================================
echo.
pause
