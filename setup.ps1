#Requires -Version 5.1
<#
.SYNOPSIS
    FaceLock — interactive installer / uninstaller.
.DESCRIPTION
    Menu-driven setup for the FaceLock facial authentication application.
    Run from the project root directory.
    Requires an internet connection for the first install (Python + pip packages).
.NOTES
    Auto-elevates to Administrator when required for scheduled-task registration.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────
$APP          = "FaceLock"
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
$SOURCE_DIR   = $PSScriptRoot   # directory containing setup.ps1 = project root

# Directories and files excluded from the install copy.
$EXCL_DIRS    = "facelock_env __pycache__ .git .claude logs tests docs"
$EXCL_FILES   = "facelock.db facelock.key settings.json pipe.key pids.json *.pyc"

# ──────────────────────────────────────────────────────────────────────────────
# Output helpers
# ──────────────────────────────────────────────────────────────────────────────
function OK($m)     { Write-Host "  [OK] $m" -ForegroundColor Green  }
function Warn($m)   { Write-Host "  [!!] $m" -ForegroundColor Yellow }
function Err($m)    { Write-Host "  [XX] $m" -ForegroundColor Red    }
function Info($m)   { Write-Host "   ->  $m" -ForegroundColor Cyan   }
function Hdr($m)    { Write-Host "`n━━  $m  ━━" -ForegroundColor White }
function Blank      { Write-Host "" }

function Ask-YN {
    <# Returns $true for Yes, $false for No. Default controls what Enter means. #>
    param([string]$Prompt, [bool]$Default = $true)
    $hint = if ($Default) { "[Y/n]" } else { "[y/N]" }
    $ans  = Read-Host "$Prompt $hint"
    if ([string]::IsNullOrWhiteSpace($ans)) { return $Default }
    return $ans.Trim().ToLower() -in @("y", "yes")
}

# ──────────────────────────────────────────────────────────────────────────────
# Elevation helpers
# ──────────────────────────────────────────────────────────────────────────────
function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    ([Security.Principal.WindowsPrincipal]$id).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Elevate-IfNeeded {
    if (-not (Test-Admin)) {
        Warn "Administrator rights are required to register scheduled tasks."
        Info "Re-launching as Administrator — choose the same menu option again."
        Blank
        Start-Process powershell.exe `
            -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" `
            -Verb RunAs
        exit
    }
}

# ──────────────────────────────────────────────────────────────────────────────
# Python detection
# ──────────────────────────────────────────────────────────────────────────────
function Find-Python312 {
    <# Returns the path to a Python 3.12+ executable, or $null. #>

    # Refresh PATH so a freshly-installed Python is visible.
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path", "User")

    foreach ($cmd in @("py", "python3", "python")) {
        try {
            $raw = & $cmd --version 2>&1
            if ("$raw" -match "Python (\d+)\.(\d+)") {
                if ([int]$Matches[1] -ge 3 -and [int]$Matches[2] -ge 12) {
                    $exe = (Get-Command $cmd -ErrorAction SilentlyContinue).Source
                    if ($exe) { return $exe }
                }
            }
        } catch {}
    }

    # Fallback: scan common install locations.
    $bases = @(
        "$env:LOCALAPPDATA\Programs\Python",
        "$env:ProgramFiles\Python",
        "C:\Python312", "C:\Python313", "C:\Python314"
    )
    foreach ($base in $bases) {
        foreach ($dir in (Get-ChildItem $base -ErrorAction SilentlyContinue)) {
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
    Info "Attempting to install Python 3.12 via winget..."
    try {
        winget install --id Python.Python.3.12 --source winget --silent --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) { throw "winget exited with code $LASTEXITCODE" }
        OK "Python 3.12 installed."
    } catch {
        Err "winget installation failed: $_"
        Err "Please install Python 3.12+ manually from https://www.python.org/downloads/"
        Err "Make sure to tick 'Add Python to PATH' during setup."
        return $false
    }
    # Re-scan after install.
    $py = Find-Python312
    if (-not $py) {
        Err "Python 3.12+ not found after installation. You may need to restart your terminal."
        return $false
    }
    return $true
}

# ──────────────────────────────────────────────────────────────────────────────
# Install
# ──────────────────────────────────────────────────────────────────────────────
function Invoke-Install {

    Elevate-IfNeeded
    Hdr "FaceLock — Installation"

    # ── 1. Verify source ──────────────────────────────────────────────────────
    if (-not (Test-Path "$SOURCE_DIR\main.py")) {
        Err "Cannot find main.py in '$SOURCE_DIR'."
        Err "Please run setup.ps1 from the FaceLock project root directory."
        return
    }

    # ── 2. Python 3.12+ ───────────────────────────────────────────────────────
    Blank
    Info "Checking for Python 3.12+..."
    $pythonSys = Find-Python312
    if (-not $pythonSys) {
        Warn "Python 3.12+ not found."
        if (Ask-YN "Install Python 3.12 via winget now?") {
            $ok = Install-Python312
            if (-not $ok) { return }
            $pythonSys = Find-Python312
        } else {
            Err "Python 3.12+ is required. Aborting."
            return
        }
    }
    OK "Python found: $pythonSys"

    # ── 3. Copy project files ────────────────────────────────────────────────
    Blank
    Info "Copying project files to $INSTALL_DIR ..."

    if (-not (Test-Path $INSTALL_DIR)) {
        New-Item -ItemType Directory -Path $INSTALL_DIR | Out-Null
    }

    # Build exclusion arguments for robocopy.
    $exclDirArgs  = ($EXCL_DIRS  -split " " | ForEach-Object { $_ })
    $exclFileArgs = ($EXCL_FILES -split " " | ForEach-Object { $_ })

    $roboArgs = @(
        $SOURCE_DIR, $INSTALL_DIR,
        "/E",          # copy subdirectories including empty ones
        "/NFL",        # no file list in output
        "/NDL",        # no directory list
        "/NJH",        # no job header
        "/NJS",        # no job summary
        "/XD"
    ) + $exclDirArgs + @("/XF") + $exclFileArgs

    $result = robocopy @roboArgs
    # robocopy exit codes 0-7 are success; 8+ are errors.
    if ($LASTEXITCODE -ge 8) {
        Err "robocopy failed (exit $LASTEXITCODE). Check permissions on $INSTALL_DIR."
        return
    }
    OK "Files copied."

    # ── 4. Virtual environment ───────────────────────────────────────────────
    Blank
    if (Test-Path $VENV_DIR) {
        Warn "A virtual environment already exists at $VENV_DIR."
        $recreate = Ask-YN "Recreate it? (No = keep existing and skip pip install)" $false
        if ($recreate) {
            Info "Removing existing venv..."
            Remove-Item -Recurse -Force $VENV_DIR
        }
    }

    if (-not (Test-Path $VENV_DIR)) {
        Info "Creating virtual environment..."
        & $pythonSys -m venv $VENV_DIR
        if ($LASTEXITCODE -ne 0) {
            Err "Failed to create virtual environment."
            return
        }
        OK "Virtual environment created."

        # ── 5. pip install ───────────────────────────────────────────────────
        Blank
        Info "Installing Python packages (this may take several minutes)..."
        Info "Note: dlib and face_recognition require a C++ compiler."

        & $PYTHON_EXE -m pip install --upgrade pip --quiet
        & $PYTHON_EXE -m pip install -r $REQ_TXT

        if ($LASTEXITCODE -ne 0) {
            Err "pip install failed."
            Blank
            Warn "dlib and face_recognition require Microsoft C++ Build Tools."
            Warn "Download from: https://visualstudio.microsoft.com/visual-cpp-build-tools/"
            Warn "Select: 'Desktop development with C++' workload, then re-run setup."
            Blank
            Warn "If you already have Build Tools, ensure they are up to date."
            if (-not (Ask-YN "Continue with setup anyway (tasks + shortcut only)?" $false)) {
                return
            }
        } else {
            OK "Python packages installed."
        }
    } else {
        Warn "Keeping existing virtual environment — skipping pip install."
    }

    # ── 6. Scheduled tasks ───────────────────────────────────────────────────
    Blank
    Info "Registering scheduled tasks..."

    # Remove old versions of the tasks if they exist.
    foreach ($task in @($TASK_CORE, $TASK_MODEA)) {
        $q = schtasks /query /tn $task 2>&1
        if ($LASTEXITCODE -eq 0) {
            schtasks /delete /f /tn $task | Out-Null
        }
    }

    # Core service — starts on logon, no delay.
    schtasks /create /f `
        /tn $TASK_CORE `
        /tr "`"$PYTHONW_EXE`" `"$CORE_PY`"" `
        /sc ONLOGON `
        /ru $env:USERNAME | Out-Null

    if ($LASTEXITCODE -ne 0) {
        Err "Failed to register $TASK_CORE."
        return
    }
    OK "Registered: $TASK_CORE"

    # Mode A session locker — starts 1 minute after logon (gives core service time to init).
    schtasks /create /f `
        /tn $TASK_MODEA `
        /tr "`"$PYTHONW_EXE`" `"$MAIN_PY`" mode-a" `
        /sc ONLOGON `
        /ru $env:USERNAME `
        /delay 0:01 | Out-Null

    if ($LASTEXITCODE -ne 0) {
        Err "Failed to register $TASK_MODEA."
        return
    }
    OK "Registered: $TASK_MODEA (1 min delay)"

    # ── 7. Desktop shortcut ──────────────────────────────────────────────────
    Blank
    Info "Creating Desktop shortcut..."
    try {
        $ws  = New-Object -ComObject WScript.Shell
        $sc  = $ws.CreateShortcut($SHORTCUT)
        $sc.TargetPath       = $PYTHONW_EXE
        $sc.Arguments        = "`"$MAIN_PY`""
        $sc.WorkingDirectory = $INSTALL_DIR
        $sc.Description      = "FaceLock — Facial Authentication"
        $sc.WindowStyle      = 1   # normal window
        $sc.Save()
        OK "Shortcut created: $SHORTCUT"
    } catch {
        Warn "Could not create Desktop shortcut: $_"
    }

    # ── 8. Done ──────────────────────────────────────────────────────────────
    Blank
    OK "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    OK " FaceLock installed to $INSTALL_DIR"
    OK " Scheduled tasks will activate on next login."
    OK " Use the Desktop shortcut to start manually now."
    OK "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    Blank

    if (Ask-YN "Launch FaceLock now?") {
        Info "Starting FaceLock..."
        Start-Process -FilePath $PYTHONW_EXE -ArgumentList "`"$MAIN_PY`"" -WorkingDirectory $INSTALL_DIR
        OK "FaceLock launched."
    }
}

# ──────────────────────────────────────────────────────────────────────────────
# Uninstall
# ──────────────────────────────────────────────────────────────────────────────
function Invoke-Uninstall {

    Elevate-IfNeeded
    Hdr "FaceLock — Uninstallation"
    Blank
    Warn "This will remove FaceLock's scheduled tasks and Desktop shortcut."

    if (-not (Ask-YN "Continue with uninstall?")) {
        Info "Uninstall cancelled."
        return
    }

    # ── 1. Stop and remove scheduled tasks ───────────────────────────────────
    Blank
    Info "Removing scheduled tasks..."
    $removedAny = $false
    foreach ($task in @($TASK_CORE, $TASK_MODEA)) {
        $q = schtasks /query /tn $task 2>&1
        if ($LASTEXITCODE -eq 0) {
            schtasks /end /tn $task 2>&1 | Out-Null    # stop if running
            schtasks /delete /f /tn $task | Out-Null
            OK "Removed task: $task"
            $removedAny = $true
        } else {
            Info "Task not found (already removed): $task"
        }
    }

    # ── 2. Remove Desktop shortcut ───────────────────────────────────────────
    Blank
    Info "Removing Desktop shortcut..."
    if (Test-Path $SHORTCUT) {
        Remove-Item -Force $SHORTCUT
        OK "Shortcut removed."
    } else {
        Info "Shortcut not found (already removed)."
    }

    # ── 3. Optionally delete installed files ─────────────────────────────────
    Blank
    if (Test-Path $INSTALL_DIR) {
        Warn "Installation directory: $INSTALL_DIR"
        Warn "This contains all FaceLock files including your enrolled face data."
        Blank

        $deleteFiles = Ask-YN "Delete ALL FaceLock files (including face data)?" $false

        if ($deleteFiles) {
            Blank
            Warn "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            Warn " FINAL CONFIRMATION"
            Warn " $INSTALL_DIR will be permanently deleted."
            Warn " Your enrolled face data CANNOT be recovered."
            Warn "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            Blank

            $confirmed = Ask-YN "Type Y to confirm permanent deletion" $false

            if ($confirmed) {
                Info "Deleting $INSTALL_DIR ..."
                Remove-Item -Recurse -Force $INSTALL_DIR -ErrorAction SilentlyContinue
                if (Test-Path $INSTALL_DIR) {
                    Warn "Some files could not be deleted (they may be in use)."
                    Warn "Close all FaceLock windows and retry, or delete manually."
                } else {
                    OK "Installation directory deleted."
                }
            } else {
                Info "Deletion cancelled — files kept."
            }
        } else {
            Info "Installation directory kept at $INSTALL_DIR"
            Info "Delete it manually when ready."
        }
    } else {
        Info "Installation directory not found — nothing to delete."
    }

    # ── 4. Done ──────────────────────────────────────────────────────────────
    Blank
    OK "Uninstall complete."
}

# ──────────────────────────────────────────────────────────────────────────────
# Main menu
# ──────────────────────────────────────────────────────────────────────────────
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
    Write-Host "    1)  Install" -ForegroundColor Green
    Write-Host "    2)  Uninstall" -ForegroundColor Yellow
    Write-Host "    3)  Exit" -ForegroundColor Gray
    Blank

    $choice = Read-Host "  Option [1/2/3]"

    switch ($choice.Trim()) {
        "1" {
            try   { Invoke-Install }
            catch { Err "Installation error: $_" }
        }
        "2" {
            try   { Invoke-Uninstall }
            catch { Err "Uninstallation error: $_" }
        }
        "3" {
            Blank
            Info "Goodbye."
            Blank
            exit 0
        }
        default {
            Warn "Invalid option. Enter 1, 2, or 3."
        }
    }

    Blank
    Read-Host "  Press Enter to return to the menu"
    Clear-Host
}
