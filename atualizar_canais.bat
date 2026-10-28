@echo off
chcp 65001 >nul
title Atualizar Canais EmbedTV
cd /d "%~dp0"

echo ==========================================
echo   Atualizando canais EmbedTV...
echo ==========================================
echo.

where python >nul 2>nul
if %errorlevel%==0 (
    python embedtv_update.py %*
) else (
    where py >nul 2>nul
    if %errorlevel%==0 (
        py embedtv_update.py %*
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
echo   Concluido! Playlist: %~dp0embedtv.m3u
echo ==========================================
echo.
pause
