@echo off
REM ==================================================================
REM  Dashboard Openbravo - Lanceur robuste et portable (Streamlit)
REM ------------------------------------------------------------------
REM  Le venv n'est JAMAIS reutilise sur base d'une empreinte machine.
REM  On teste sa SANTE REELLE : python.exe fonctionne + streamlit est
REM  installe. Si oui -> on lance directement (rapide). Sinon -> on
REM  (re)cree le venv et on installe les dependances.
REM ==================================================================
setlocal enabledelayedexpansion

cd /d "%~dp0"

echo.
echo ==================================================================
echo   Dashboard Openbravo
echo ==================================================================
echo.

REM ------------------------------------------------------------------
REM  Nettoyage des residus de test eventuels (best-effort, silencieux)
REM ------------------------------------------------------------------
if exist "_test_cache.db"     del /f /q "_test_cache.db"     >nul 2>&1
if exist "_test_cache.db-wal" del /f /q "_test_cache.db-wal" >nul 2>&1
if exist "_test_cache.db-shm" del /f /q "_test_cache.db-shm" >nul 2>&1

REM ------------------------------------------------------------------
REM  ETAPE 1 : le venv est-il DEJA sain ? (cas le plus frequent)
REM ------------------------------------------------------------------
REM  Test de sante : python.exe repond ET streamlit est importable.
REM  Si c'est bon, on saute toute la preparation et on lance direct.
REM ------------------------------------------------------------------
if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" -c "import streamlit" >nul 2>&1
    if not errorlevel 1 (
        echo [OK] Environnement pret. Lancement immediat.
        echo.
        goto :launch
    )
    echo [i] venv present mais incomplet ^(streamlit manquant ou python KO^).
) else (
    echo [i] Aucun venv detecte.
)

REM ------------------------------------------------------------------
REM  ETAPE 2 : preparer le venv (il faut un Python systeme)
REM ------------------------------------------------------------------
echo.
echo Preparation de l'environnement...
echo.

REM -- Trouver un Python systeme utilisable --
set "PYCMD="
py -3 --version >nul 2>&1
if not errorlevel 1 (
    set "PYCMD=py -3"
    goto :py_ok
)
python --version >nul 2>&1
if not errorlevel 1 (
    for /f "delims=" %%v in ('python --version 2^>^&1') do set "PYVER=%%v"
    echo !PYVER! | find "Python" >nul
    if not errorlevel 1 (
        set "PYCMD=python"
        goto :py_ok
    )
)
for %%D in (
    "%LocalAppData%\Programs\Python\Python313\python.exe"
    "%LocalAppData%\Programs\Python\Python312\python.exe"
    "%LocalAppData%\Programs\Python\Python311\python.exe"
    "%LocalAppData%\Local\Python\pythoncore-3.14-64\python.exe"
    "%ProgramFiles%\Python313\python.exe"
    "%ProgramFiles%\Python312\python.exe"
    "C:\Python313\python.exe"
    "C:\Python312\python.exe"
) do (
    if exist %%D (
        set "PYCMD=%%~D"
        goto :py_ok
    )
)

echo ==================================================================
echo  ERREUR : aucune installation Python trouvee sur ce PC.
echo ==================================================================
echo   1. Installez Python : https://www.python.org/downloads/
echo   2. Cochez "Add python.exe to PATH" pendant l'installation.
echo   3. Relancez ce fichier.
echo.
pause
exit /b 1

:py_ok
echo   Python detecte : !PYCMD!
for /f "delims=" %%v in ('!PYCMD! --version 2^>^&1') do echo   Version : %%v

REM -- (Re)creer le venv si absent ou casse --
if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" --version >nul 2>&1
    if errorlevel 1 (
        echo   venv casse : suppression et recreation...
        rmdir /s /q "venv"
        !PYCMD! -m venv venv
    )
) else (
    if exist "venv" rmdir /s /q "venv"
    echo   Creation du venv...
    !PYCMD! -m venv venv
)
if not exist "venv\Scripts\python.exe" (
    echo ERREUR : echec de la creation du venv.
    pause
    exit /b 1
)

REM -- Installer / mettre a jour les dependances --
echo   Mise a jour de pip...
"venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>&1
echo   Installation des dependances ^(requirements.txt^)...
"venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo ERREUR : echec de l'installation des dependances.
    pause
    exit /b 1
)

REM ------------------------------------------------------------------
REM  ETAPE 3 : lancement
REM ------------------------------------------------------------------
:launch
echo Lancement de l'application Streamlit...
echo   Ouverture dans le navigateur : http://localhost:8501
echo   Pour arreter : fermez cette fenetre ou Ctrl+C
echo.
"venv\Scripts\streamlit.exe" run ISPT_dashboard.py

endlocal
pause
