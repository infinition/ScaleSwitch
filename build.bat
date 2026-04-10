@echo off
echo.
echo  ====================================
echo   ScaleSwitch — Build .exe
echo  ====================================
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERREUR] Python non trouvé. Installe Python 3.8+ et ajoute-le au PATH.
    pause
    exit /b 1
)

REM Install dependencies
echo [1/3] Installation des dépendances...
pip install pystray Pillow pyinstaller --quiet

REM Build
echo [2/3] Compilation en .exe...
pyinstaller scale_switch.spec --clean --noconfirm

REM Done
echo [3/3] Terminé !
echo.
if exist "dist\ScaleSwitch.exe" (
    echo  ✅ ScaleSwitch.exe créé dans : dist\ScaleSwitch.exe
    echo  Taille :
    for %%A in ("dist\ScaleSwitch.exe") do echo    %%~zA octets
) else (
    echo  ❌ Erreur de compilation. Vérifie les logs ci-dessus.
)
echo.
pause
