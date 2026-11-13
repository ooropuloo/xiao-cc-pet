# Stop what start-xiao.bat started: the player and the ccpet server (only those processes).
# Each target is found by the port it listens on, and its command line is checked before stopping.
param([switch]$KeepCcpet)

function Stop-PortOwner([int]$port, [string]$mustMatch, [string]$label) {
  $owner = (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess
  if (-not $owner) { "$label not running"; return }
  $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$owner"
  if ($proc.CommandLine -notmatch $mustMatch) { "port $port is held by something else, left alone: $($proc.CommandLine)"; return }
  Stop-Process -Id $owner -Force
  "stopped $label (pid $owner)"
  # the uv launcher that started it exits on its own once the child is gone; stop it if it lingers
  $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($proc.ParentProcessId)" -ErrorAction SilentlyContinue
  if ($parent -and $parent.CommandLine -match $mustMatch) { Stop-Process -Id $parent.ProcessId -Force -ErrorAction SilentlyContinue }
}

# stop the restart loop first, or it brings the player straight back
$loops = Get-CimInstance Win32_Process -Filter "Name='cmd.exe'" | Where-Object { $_.CommandLine -match 'run-player\.bat' }
foreach ($l in $loops) { Stop-Process -Id $l.ProcessId -Force -ErrorAction SilentlyContinue; "stopped restart loop (pid $($l.ProcessId))" }
Stop-PortOwner 5810 'player\.py' 'player'
if (-not $KeepCcpet) { Stop-PortOwner 3378 'server\.js' 'ccpet server' }
