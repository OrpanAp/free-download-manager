# Zephyr - guided extension setup for EVERY Chromium browser found on this PC.
# Per browser you do 2 clicks (Developer mode, Load unpacked); this script types the folder path for you.
$ext = Join-Path $PSScriptRoot 'browser-extension'
if (-not (Test-Path (Join-Path $ext 'manifest.json'))) {
    Write-Host "Could not find the extension folder: $ext"
    Read-Host "Press Enter to close"
    exit 1
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName Microsoft.VisualBasic

$pf   = $env:ProgramFiles
$pf86 = ${env:ProgramFiles(x86)}
$lad  = $env:LOCALAPPDATA

$candidates = @(
    @{ Name = 'Google Chrome';  Page = 'chrome://extensions';  Paths = @("$pf\Google\Chrome\Application\chrome.exe", "$pf86\Google\Chrome\Application\chrome.exe", "$lad\Google\Chrome\Application\chrome.exe") },
    @{ Name = 'Microsoft Edge'; Page = 'edge://extensions';    Paths = @("$pf86\Microsoft\Edge\Application\msedge.exe", "$pf\Microsoft\Edge\Application\msedge.exe") },
    @{ Name = 'Brave';          Page = 'brave://extensions';   Paths = @("$pf\BraveSoftware\Brave-Browser\Application\brave.exe", "$pf86\BraveSoftware\Brave-Browser\Application\brave.exe", "$lad\BraveSoftware\Brave-Browser\Application\brave.exe") },
    @{ Name = 'Vivaldi';        Page = 'vivaldi://extensions'; Paths = @("$lad\Vivaldi\Application\vivaldi.exe", "$pf\Vivaldi\Application\vivaldi.exe") }
)

$found = @()
foreach ($b in $candidates) {
    foreach ($p in $b.Paths) {
        if (Test-Path $p) {
            $found += [pscustomobject]@{ Name = $b.Name; Page = $b.Page; Exe = $p }
            break
        }
    }
}

Write-Host "=================================================="
Write-Host "  Add the Zephyr extension to your browsers"
Write-Host "=================================================="
if ($found.Count -eq 0) {
    Write-Host "No Chrome, Edge, Brave or Vivaldi found on this PC."
    Read-Host "Press Enter to close"
    exit 0
}
Write-Host "Browsers found:"
foreach ($f in $found) { Write-Host "  - $($f.Name)" }
Write-Host ""
Write-Host "For each one: switch ON 'Developer mode' (top right), then click 'Load unpacked'."
Write-Host "The folder path is typed in for you. Repeat for every browser profile you use."

function Wait-AndPaste {
    param([string]$Seconds = 240)
    # Titles of the folder-picker window that 'Load unpacked' opens.
    $titles = @('Select the extension directory', 'Select extension directory')
    $deadline = (Get-Date).AddSeconds([int]$Seconds)
    while ((Get-Date) -lt $deadline) {
        foreach ($t in $titles) {
            $ok = $false
            try { [Microsoft.VisualBasic.Interaction]::AppActivate($t); $ok = $true } catch { $ok = $false }
            if ($ok) {
                Start-Sleep -Milliseconds 800
                [System.Windows.Forms.SendKeys]::SendWait('^v')
                Start-Sleep -Milliseconds 500
                [System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
                return $true
            }
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

foreach ($f in $found) {
    Write-Host ""
    $answer = Read-Host "Press Enter to set up $($f.Name) (or type S to skip)"
    if ($answer -match '^[sS]') { continue }
    Set-Clipboard -Value $ext
    Start-Process -FilePath $f.Exe -ArgumentList $f.Page
    Write-Host "  Waiting for you to click 'Load unpacked' in $($f.Name) ..."
    $done = Wait-AndPaste
    if ($done) {
        Write-Host "  Done. 'Zephyr Integration' should now be listed in $($f.Name)."
    } else {
        Write-Host "  Timed out. If the folder window is open, press Ctrl+V then Enter (the path is on your clipboard)."
        Write-Host "  Still stuck? Open the Installation guide from the Start menu (Zephyr Download Manager folder)."
    }
    Start-Sleep -Seconds 2
}

Write-Host ""
Write-Host "Finished. If anything did not work, follow the Installation guide:"
Write-Host "  Start menu > Zephyr Download Manager > Installation guide  (it also covers Firefox)."
Read-Host "Press Enter to close"
