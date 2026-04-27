@echo off
:: ─────────────────────────────────────────────────────────────────────────────
:: FaceLock Setup — double-click to install or uninstall.
:: Batch bootstraps PowerShell; all setup logic is below the sentinel marker.
:: ─────────────────────────────────────────────────────────────────────────────
set "SETUP_SRC=%~f0"
set "SETUP_DIR=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "& { $src=[IO.File]::ReadAllText($env:SETUP_SRC,[Text.Encoding]::UTF8);" ^
  "$m='##::POWERSHELL_START::##';" ^
  "$idx=$src.IndexOf($m);" ^
  "if($idx -lt 0){Write-Host 'Marker not found' -f Red;pause;exit 1};" ^
  "$ps=$src.Substring($idx+$m.Length+1);" ^
  "$t=[IO.Path]::GetTempFileName()+'.ps1';" ^
  "[IO.File]::WriteAllText($t,$ps,[Text.Encoding]::UTF8);" ^
  "try{& $t}finally{Remove-Item $t -EA 0} }"
exit /b
##::POWERSHELL_START::##
#Requires -Version 5.1
<#
.SYNOPSIS  FaceLock — Install / Uninstall
.NOTES     Single-file setup. Source directory is passed via $env:SETUP_DIR.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────
$APP          = "FaceLock"
$SOURCE_DIR   = $env:SETUP_DIR.TrimEnd('\')       # project root (set by batch)
$INSTALL_DIR  = "$env:LOCALAPPDATA\$APP"
$VENV_DIR     = "$INSTALL_DIR\facelock_env"
$PYTHON_EXE   = "$VENV_DIR\Scripts\python.exe"
$PYTHONW_EXE  = "$VENV_DIR\Scripts\pythonw.exe"
$MAIN_PY      = "$INSTALL_DIR\main.py"
$CORE_PY      = "$INSTALL_DIR\core_service.py"
$REQ_TXT      = "$INSTALL_DIR\requirements.txt"
$SHORTCUT     = [IO.Path]::Combine($env:USERPROFILE, "Desktop", "$APP.lnk")
$TASK_CORE    = "FaceLock-CoreService"
$TASK_MODEA   = "FaceLock-ModeA"

# Robocopy exclusions — keeps data\face_detector.tflite, excludes user data.
$EXCL_DIRS    = @("facelock_env","__pycache__",".git",".claude","logs","tests","docs")
$EXCL_FILES   = @("facelock.db","facelock.key","settings.json","pipe.key","pids.json","*.pyc")

# ─────────────────────────────────────────────────────────────────────────────
# Output helpers
# ─────────────────────────────────────────────────────────────────────────────
function OK($m)   { Write-Host "  [OK] $m" -ForegroundColor Green  }
function Warn($m) { Write-Host "  [!!] $m" -ForegroundColor Yellow }
function Err($m)  { Write-Host "  [XX] $m" -ForegroundColor Red    }
function Info($m) { Write-Host "   ->  $m" -ForegroundColor Cyan   }
function Hdr($m)  { Write-Host "`n━━  $m  ━━" -ForegroundColor White }
function Blank    { Write-Host "" }

function Ask-YN {
    param([string]$Prompt, [bool]$Default = $true)
    $hint = if ($Default) { "[Y/n]" } else { "[y/N]" }
    $ans  = Read-Host "$Prompt $hint"
    if ([string]::IsNullOrWhiteSpace($ans)) { return $Default }
    return $ans.Trim().ToLower() -in @("y","yes")
}

# ─────────────────────────────────────────────────────────────────────────────
# Admin elevation
# ─────────────────────────────────────────────────────────────────────────────
function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    ([Security.Principal.WindowsPrincipal]$id).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Elevate-IfNeeded {
    if (-not (Test-Admin)) {
        Warn "Administrator rights required for scheduled-task registration."
        Info "Re-launching as Administrator — choose the same option again."
        Blank
        # Re-launch the original .bat file as admin so SETUP_DIR stays correct.
        Start-Process cmd.exe -ArgumentList "/c `"$env:SETUP_SRC`"" -Verb RunAs
        exit
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Python detection
# ─────────────────────────────────────────────────────────────────────────────
function Find-Python312 {
    # Refresh PATH so a freshly-installed Python is immediately visible.
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") +
                ";" + [System.Environment]::GetEnvironmentVariable("Path","User")

    foreach ($cmd in @("py","python3","python")) {
        try {
            $v = & $cmd --version 2>&1
            if ("$v" -match "Python (\d+)\.(\d+)" -and
                [int]$Matches[1] -ge 3 -and [int]$Matches[2] -ge 12) {
                $src = (Get-Command $cmd -EA SilentlyContinue).Source
                if ($src) { return $src }
            }
        } catch {}
    }

    foreach ($base in @("$env:LOCALAPPDATA\Programs\Python","$env:ProgramFiles\Python",
                         "C:\Python312","C:\Python313","C:\Python314")) {
        foreach ($dir in (Get-ChildItem $base -EA SilentlyContinue)) {
            $py = Join-Path $dir.FullName "python.exe"
            if (Test-Path $py) {
                $v = & $py --version 2>&1
                if ("$v" -match "Python 3\.(\d+)" -and [int]$Matches[1] -ge 12) {
                    return $py
                }
            }
        }
    }
    return $null
}

function Install-Python312 {
    Info "Installing Python 3.12 via winget..."
    try {
        winget install --id Python.Python.3.12 --source winget --silent `
              --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) { throw "winget exit $LASTEXITCODE" }
        OK "Python 3.12 installed."
        return $true
    } catch {
        Err "winget failed: $_"
        Err "Install Python 3.12+ manually from https://www.python.org/downloads/"
        Err "Tick 'Add Python to PATH' during setup, then re-run this script."
        return $false
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Install
# ─────────────────────────────────────────────────────────────────────────────
function Invoke-Install {
    Elevate-IfNeeded
    Hdr "FaceLock — Installation"

    # 1. Verify source directory contains the project.
    if (-not (Test-Path "$SOURCE_DIR\main.py")) {
        Err "Cannot find main.py in '$SOURCE_DIR'."
        Err "Place FaceLock-Setup.bat in the FaceLock project root and try again."
        return
    }

    # 2. Python 3.12+
    Blank
    Info "Checking for Python 3.12+..."
    $pythonSys = Find-Python312
    if (-not $pythonSys) {
        Warn "Python 3.12+ not found."
        if (Ask-YN "Install Python 3.12 via winget now?") {
            if (-not (Install-Python312)) { return }
            $pythonSys = Find-Python312
            if (-not $pythonSys) {
                Err "Still not found. Restart the script after installing manually."
                return
            }
        } else { Err "Python 3.12+ is required. Aborting."; return }
    }
    OK "Python: $pythonSys"

    # 3. Copy files.
    Blank
    Info "Copying project files to $INSTALL_DIR ..."
    if (-not (Test-Path $INSTALL_DIR)) {
        New-Item -ItemType Directory -Path $INSTALL_DIR | Out-Null
    }

    $roboArgs = @($SOURCE_DIR, $INSTALL_DIR, "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/XD") +
                $EXCL_DIRS + @("/XF") + $EXCL_FILES
    robocopy @roboArgs | Out-Null
    if ($LASTEXITCODE -ge 8) {
        Err "File copy failed (robocopy exit $LASTEXITCODE). Check permissions."
        return
    }
    OK "Files copied."

    # 4. Virtual environment.
    Blank
    if (Test-Path $VENV_DIR) {
        Warn "Virtual environment already exists at $VENV_DIR"
        $recreate = Ask-YN "Recreate it? (No = keep existing, skip pip install)" $false
        if ($recreate) {
            Info "Removing existing venv..."
            Remove-Item -Recurse -Force $VENV_DIR
        }
    }

    if (-not (Test-Path $VENV_DIR)) {
        Info "Creating virtual environment..."
        & $pythonSys -m venv $VENV_DIR
        if ($LASTEXITCODE -ne 0) { Err "Failed to create virtual environment."; return }
        OK "Virtual environment created."

        # 5. pip install.
        Blank
        Info "Installing Python packages — this may take several minutes..."
        Warn "dlib and face_recognition require a C++ compiler."
        & $PYTHON_EXE -m pip install --upgrade pip --quiet
        & $PYTHON_EXE -m pip install -r $REQ_TXT

        if ($LASTEXITCODE -ne 0) {
            Err "pip install failed."
            Blank
            Warn "Most likely cause: Microsoft C++ Build Tools not installed."
            Warn "Download: https://visualstudio.microsoft.com/visual-cpp-build-tools/"
            Warn "Select workload: 'Desktop development with C++', then re-run."
            Blank
            if (-not (Ask-YN "Continue anyway (register tasks + shortcut only)?" $false)) {
                return
            }
        } else {
            OK "Python packages installed."
        }
    } else {
        Warn "Keeping existing venv — skipping pip install."
    }

    # 6. Scheduled tasks.
    Blank
    Info "Registering scheduled tasks..."
    foreach ($t in @($TASK_CORE, $TASK_MODEA)) {
        schtasks /query /tn $t 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { schtasks /delete /f /tn $t | Out-Null }
    }

    schtasks /create /f /tn $TASK_CORE `
             /tr "`"$PYTHONW_EXE`" `"$CORE_PY`"" `
             /sc ONLOGON /ru $env:USERNAME | Out-Null
    if ($LASTEXITCODE -ne 0) { Err "Failed to register $TASK_CORE."; return }
    OK "Registered: $TASK_CORE  (starts on logon)"

    schtasks /create /f /tn $TASK_MODEA `
             /tr "`"$PYTHONW_EXE`" `"$MAIN_PY`" mode-a" `
             /sc ONLOGON /ru $env:USERNAME /delay 0:01 | Out-Null
    if ($LASTEXITCODE -ne 0) { Err "Failed to register $TASK_MODEA."; return }
    OK "Registered: $TASK_MODEA  (starts 1 min after logon)"

    # 7. Desktop shortcut.
    Blank
    Info "Creating Desktop shortcut..."
    try {
        $ws = New-Object -ComObject WScript.Shell
        $sc = $ws.CreateShortcut($SHORTCUT)
        $sc.TargetPath       = $PYTHONW_EXE
        $sc.Arguments        = "`"$MAIN_PY`""
        $sc.WorkingDirectory = $INSTALL_DIR
        $sc.Description      = "FaceLock — Facial Authentication"
        $sc.WindowStyle      = 1
        $sc.Save()
        OK "Shortcut: $SHORTCUT"
    } catch {
        Warn "Could not create Desktop shortcut: $_"
    }

    # 8. Summary.
    Blank
    Write-Host "  ┌───────────────────────────────────────────────────────┐" -ForegroundColor Green
    Write-Host "  │  FaceLock installed successfully.                     │" -ForegroundColor Green
    Write-Host "  │  Installed to: $INSTALL_DIR" -ForegroundColor Green
    Write-Host "  │  Tasks activate automatically on next login.          │" -ForegroundColor Green
    Write-Host "  └───────────────────────────────────────────────────────┘" -ForegroundColor Green
    Blank

    if (Ask-YN "Launch FaceLock now?") {
        Start-Process -FilePath $PYTHONW_EXE `
                      -ArgumentList "`"$MAIN_PY`"" `
                      -WorkingDirectory $INSTALL_DIR
        OK "FaceLock launched."
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Uninstall
# ─────────────────────────────────────────────────────────────────────────────
function Invoke-Uninstall {
    Elevate-IfNeeded
    Hdr "FaceLock — Uninstallation"
    Blank
    Warn "This will remove scheduled tasks and the Desktop shortcut."

    if (-not (Ask-YN "Continue?")) { Info "Cancelled."; return }

    # 1. Scheduled tasks.
    Blank
    Info "Removing scheduled tasks..."
    foreach ($t in @($TASK_CORE, $TASK_MODEA)) {
        schtasks /query /tn $t 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            schtasks /end    /tn $t 2>&1 | Out-Null
            schtasks /delete /f /tn $t   | Out-Null
            OK "Removed: $t"
        } else {
            Info "Not found (already removed): $t"
        }
    }

    # 2. Desktop shortcut.
    Blank
    if (Test-Path $SHORTCUT) {
        Remove-Item -Force $SHORTCUT
        OK "Shortcut removed."
    } else {
        Info "Shortcut not found."
    }

    # 3. Optional: delete installed files.
    Blank
    if (Test-Path $INSTALL_DIR) {
        Warn "Installation folder still exists: $INSTALL_DIR"
        Warn "It contains your enrolled face data and all app files."
        Blank
        $del = Ask-YN "Delete ALL FaceLock files and face data?" $false

        if ($del) {
            Blank
            Write-Host "  ┌───────────────────────────────────────────────────┐" -ForegroundColor Red
            Write-Host "  │  FINAL CONFIRMATION                               │" -ForegroundColor Red
            Write-Host "  │  $INSTALL_DIR" -ForegroundColor Red
            Write-Host "  │  will be permanently deleted.                     │" -ForegroundColor Red
            Write-Host "  │  Your face data CANNOT be recovered.              │" -ForegroundColor Red
            Write-Host "  └───────────────────────────────────────────────────┘" -ForegroundColor Red
            Blank

            if (Ask-YN "Confirm permanent deletion?" $false) {
                Info "Deleting $INSTALL_DIR ..."
                Remove-Item -Recurse -Force $INSTALL_DIR -EA SilentlyContinue
                if (Test-Path $INSTALL_DIR) {
                    Warn "Some files could not be deleted (may be in use)."
                    Warn "Close all FaceLock windows and delete manually."
                } else {
                    OK "Deleted."
                }
            } else {
                Info "Deletion cancelled — files kept."
            }
        } else {
            Info "Files kept at $INSTALL_DIR"
        }
    } else {
        Info "Installation folder not found — nothing to delete."
    }

    Blank
    OK "Uninstall complete."
}

# ─────────────────────────────────────────────────────────────────────────────
# Main menu
# ─────────────────────────────────────────────────────────────────────────────
Clear-Host
Write-Host @"

  ███████╗ █████╗  ██████╗███████╗██╗      ██████╗  ██████╗██╗  ██╗
  ██╔════╝██╔══██╗██╔════╝██╔════╝██║     ██╔═══██╗██╔════╝██║ ██╔╝
  █████╗  ███████║██║     █████╗  ██║     ██║   ██║██║     █████╔╝
  ██╔══╝  ██╔══██║██║     ██╔══╝  ██║     ██║   ██║██║     ██╔═██╗
  ██║     ██║  ██║╚██████╗███████╗███████╗╚██████╔╝╚██████╗██║  ██╗
  ╚═╝     ╚═╝  ╚═╝ ╚═════╝╚══════╝╚══════╝ ╚═════╝  ╚═════╝╚═╝  ╚═╝

  Facial Authentication for Windows — Setup
"@ -ForegroundColor Cyan

while ($true) {
    Blank
    Write-Host "  Select an option:" -ForegroundColor White
    Write-Host "    1)  Install"   -ForegroundColor Green
    Write-Host "    2)  Uninstall" -ForegroundColor Yellow
    Write-Host "    3)  Exit"      -ForegroundColor Gray
    Blank

    $choice = Read-Host "  Option [1/2/3]"
    switch ($choice.Trim()) {
        "1" { try { Invoke-Install   } catch { Err "Install error: $_"   } }
        "2" { try { Invoke-Uninstall } catch { Err "Uninstall error: $_" } }
        "3" { Blank; Info "Goodbye."; Blank; exit 0 }
        default { Warn "Enter 1, 2, or 3." }
    }

    Blank
    Read-Host "  Press Enter to return to the menu"
    Clear-Host
}
