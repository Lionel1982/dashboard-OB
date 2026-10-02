@echo off
REM ============================================================
REM  push-git.bat - Pousse les modifs du dashboard sur GitHub
REM  Usage :
REM    push-git.bat "mon message de commit"
REM    push-git.bat              (message horodate par defaut)
REM
REM  Ordre : add -> commit -> pull --rebase -> push
REM  (on committe AVANT de puller : git pull --rebase refuse de
REM   tourner s'il reste des modifs non committees)
REM ============================================================
setlocal

cd /d "%~dp0"

REM --- Message de commit : argument, sinon horodatage ---
set "MSG=%~1"
if "%MSG%"=="" (
    set "MSG=maj dashboard %date% %time%"
)

echo.
echo === 1/4 : Ajout des fichiers (git add) ===
git add -A

echo.
echo === 2/4 : Commit : "%MSG%" ===
git commit -m "%MSG%"
if errorlevel 1 (
    echo.
    echo [INFO] Rien a committer ^(aucune modif^). On continue vers pull/push.
)

echo.
echo === 3/4 : Recuperation des modifs distantes (git pull --rebase) ===
git pull --rebase origin main
if errorlevel 1 (
    echo.
    echo [ERREUR] git pull --rebase a echoue ^(conflit ?^).
    echo Resolvez les conflits ^(git status^), puis : git rebase --continue
    echo Ou pour annuler le rebase : git rebase --abort
    goto :fin
)

echo.
echo === 4/4 : Envoi vers GitHub (git push) ===
git push origin main
if errorlevel 1 (
    echo.
    echo [ERREUR] git push a echoue ^(authentification ? conflit ?^).
    goto :fin
)

echo.
echo === TERMINE : modifs poussees sur GitHub. Streamlit Cloud va redeployer. ===

:fin
echo.
pause
endlocal
