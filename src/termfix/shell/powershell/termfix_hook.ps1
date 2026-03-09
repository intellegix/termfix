# termfix PowerShell hook — spell correction, frecency tracking, command suggestions
# This file is sourced from $PROFILE by `termfix init powershell`

$script:TermfixPipeName = '\\.\pipe\TermfixPipe'
$script:TermfixConnectTimeoutMs = 100
$script:TermfixLastCwd = $null
$script:TermfixOriginalPrompt = $null
$script:TermfixLastHistoryId = 0

# ---------------------------------------------------------------------------
# IPC: Send request to daemon via Named Pipe
# ---------------------------------------------------------------------------

function Send-TermfixRequest {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Type,
        [hashtable]$Payload = @{}
    )

    try {
        $pipe = New-Object System.IO.Pipes.NamedPipeClientStream(
            '.',
            'TermfixPipe',
            [System.IO.Pipes.PipeDirection]::InOut
        )

        $pipe.Connect($script:TermfixConnectTimeoutMs)

        $request = @{ type = $Type; payload = $Payload } | ConvertTo-Json -Compress -Depth 5
        $requestBytes = [System.Text.Encoding]::UTF8.GetBytes($request)

        # Length-prefixed framing: 4-byte uint32 LE header
        $header = [BitConverter]::GetBytes([uint32]$requestBytes.Length)
        $pipe.Write($header, 0, 4)
        $pipe.Write($requestBytes, 0, $requestBytes.Length)
        $pipe.Flush()

        # Read response header
        $respHeader = New-Object byte[] 4
        $bytesRead = $pipe.Read($respHeader, 0, 4)
        if ($bytesRead -lt 4) {
            $pipe.Close()
            return $null
        }
        $respLen = [BitConverter]::ToUInt32($respHeader, 0)

        # Read response payload
        $respBytes = New-Object byte[] $respLen
        $totalRead = 0
        while ($totalRead -lt $respLen) {
            $chunk = $pipe.Read($respBytes, $totalRead, ($respLen - $totalRead))
            if ($chunk -eq 0) { break }
            $totalRead += $chunk
        }

        $pipe.Close()

        $responseJson = [System.Text.Encoding]::UTF8.GetString($respBytes, 0, $totalRead)
        return ($responseJson | ConvertFrom-Json)
    }
    catch {
        # Daemon not running — silently return null
        return $null
    }
}

# ---------------------------------------------------------------------------
# Spell correction via CommandNotFoundAction
# ---------------------------------------------------------------------------

if ($PSVersionTable.PSVersion.Major -ge 7) {
    # PowerShell 7+: Use $ExecutionContext.InvokeCommand.CommandNotFoundAction
    $ExecutionContext.InvokeCommand.CommandNotFoundAction = {
        param($CommandName, $CommandLookupEventArgs)

        $response = Send-TermfixRequest -Type 'spell_check' -Payload @{ command = $CommandName }

        if ($response -and $response.status -eq 'ok' -and $response.data.suggestions.Count -gt 0) {
            $suggestions = $response.data.suggestions
            $best = $suggestions[0]

            Write-Host ""
            Write-Host "  termfix: " -NoNewline -ForegroundColor DarkCyan
            Write-Host "Did you mean " -NoNewline -ForegroundColor Gray
            Write-Host "$($best.name)" -NoNewline -ForegroundColor Green
            Write-Host "?" -ForegroundColor Gray

            if ($suggestions.Count -gt 1) {
                Write-Host "           Also: " -NoNewline -ForegroundColor DarkGray
                $others = ($suggestions | Select-Object -Skip 1 | ForEach-Object { $_.name }) -join ', '
                Write-Host $others -ForegroundColor DarkGray
            }
            Write-Host ""
        }
    }
}
else {
    # PowerShell 5.1: Use $ExecutionContext.SessionState.InvokeCommand.CommandNotFoundAction
    # (same API, slightly different path in some PS versions — try/catch both)
    try {
        $ExecutionContext.InvokeCommand.CommandNotFoundAction = {
            param($CommandName, $CommandLookupEventArgs)

            $response = Send-TermfixRequest -Type 'spell_check' -Payload @{ command = $CommandName }

            if ($response -and $response.status -eq 'ok' -and $response.data.suggestions.Count -gt 0) {
                $best = $response.data.suggestions[0]
                Write-Host ""
                Write-Host "  termfix: " -NoNewline -ForegroundColor DarkCyan
                Write-Host "Did you mean " -NoNewline -ForegroundColor Gray
                Write-Host "$($best.name)" -NoNewline -ForegroundColor Green
                Write-Host "?" -ForegroundColor Gray
                Write-Host ""
            }
        }
    }
    catch {
        # CommandNotFoundAction not available on this PS version
    }
}

# ---------------------------------------------------------------------------
# Prompt wrapper — directory tracking & command recording
# ---------------------------------------------------------------------------

# Detect if Oh-My-Posh or Starship is active
$script:HasOhMyPosh = [bool]$env:POSH_THEMES_PATH
$script:HasStarship = [bool]$env:STARSHIP_SHELL

if (-not $script:HasOhMyPosh -and -not $script:HasStarship) {
    # Safe to wrap the prompt function directly
    $script:TermfixOriginalPrompt = $function:prompt

    function prompt {
        # Track directory changes
        $currentCwd = (Get-Location).Path
        if ($currentCwd -ne $script:TermfixLastCwd) {
            $script:TermfixLastCwd = $currentCwd
            $null = Send-TermfixRequest -Type 'record_cd' -Payload @{ path = $currentCwd }
        }

        # Record last command
        $lastCmd = Get-History -Count 1 -ErrorAction SilentlyContinue
        if ($lastCmd -and $lastCmd.Id -ne $script:TermfixLastHistoryId) {
            $script:TermfixLastHistoryId = $lastCmd.Id
            $null = Send-TermfixRequest -Type 'record_command' -Payload @{
                command   = $lastCmd.CommandLine
                cwd       = $currentCwd
                exit_code = if ($?) { 0 } else { 1 }
            }
        }

        # Call original prompt
        if ($script:TermfixOriginalPrompt) {
            & $script:TermfixOriginalPrompt
        }
        else {
            "PS $($executionContext.SessionState.Path.CurrentLocation)$('>' * ($nestedPromptLevel + 1)) "
        }
    }
}
else {
    # Oh-My-Posh or Starship detected — use OnIdle event to avoid breaking their prompt
    Register-EngineEvent -SourceIdentifier PowerShell.OnIdle -Action {
        $currentCwd = (Get-Location).Path
        if ($currentCwd -ne $script:TermfixLastCwd) {
            $script:TermfixLastCwd = $currentCwd
            $null = Send-TermfixRequest -Type 'record_cd' -Payload @{ path = $currentCwd }
        }

        $lastCmd = Get-History -Count 1 -ErrorAction SilentlyContinue
        if ($lastCmd -and $lastCmd.Id -ne $script:TermfixLastHistoryId) {
            $script:TermfixLastHistoryId = $lastCmd.Id
            $null = Send-TermfixRequest -Type 'record_command' -Payload @{
                command   = $lastCmd.CommandLine
                cwd       = $currentCwd
                exit_code = if ($?) { 0 } else { 1 }
            }
        }
    } | Out-Null
}

# ---------------------------------------------------------------------------
# j — frecency directory jump
# ---------------------------------------------------------------------------

function j {
    [CmdletBinding()]
    param(
        [Parameter(Position = 0, ValueFromRemainingArguments)]
        [string[]]$Query
    )

    $queryStr = if ($Query) { $Query -join ' ' } else { $null }

    if ($queryStr) {
        $response = Send-TermfixRequest -Type 'get_frecent_dirs' -Payload @{
            query = $queryStr
            limit = 1
        }

        if ($response -and $response.status -eq 'ok' -and $response.data.directories.Count -gt 0) {
            $target = $response.data.directories[0].path
            Set-Location $target
        }
        else {
            Write-Host "  termfix: no match for '$queryStr'" -ForegroundColor Yellow
        }
    }
    else {
        # List top frecent directories
        $response = Send-TermfixRequest -Type 'get_frecent_dirs' -Payload @{ limit = 10 }

        if ($response -and $response.status -eq 'ok' -and $response.data.directories.Count -gt 0) {
            foreach ($dir in $response.data.directories) {
                $score = [math]::Round($dir.score, 0).ToString().PadLeft(6)
                Write-Host "  $score  " -NoNewline -ForegroundColor Cyan
                Write-Host $dir.path
            }
        }
        else {
            Write-Host "  termfix: no directory history yet" -ForegroundColor DarkGray
        }
    }
}

# Tab completion for j
if (Get-Command Register-ArgumentCompleter -ErrorAction SilentlyContinue) {
    Register-ArgumentCompleter -CommandName j -ParameterName Query -ScriptBlock {
        param($commandName, $parameterName, $wordToComplete, $commandAst, $fakeBoundParameters)

        $response = Send-TermfixRequest -Type 'get_frecent_dirs' -Payload @{
            query = $wordToComplete
            limit = 10
        }

        if ($response -and $response.status -eq 'ok') {
            foreach ($dir in $response.data.directories) {
                $path = $dir.path
                [System.Management.Automation.CompletionResult]::new(
                    $path, $path, 'ParameterValue', "Score: $([math]::Round($dir.score, 0))"
                )
            }
        }
    }
}

# ---------------------------------------------------------------------------
# Ctrl+F — fuzzy command suggestion
# ---------------------------------------------------------------------------

if (Get-Module PSReadLine -ErrorAction SilentlyContinue) {
    Set-PSReadLineKeyHandler -Key 'Ctrl+f' -ScriptBlock {
        $line = $null
        $cursor = $null
        [Microsoft.PowerShell.PSConsoleReadLine]::GetBufferState([ref]$line, [ref]$cursor)

        if (-not $line) { return }

        $response = Send-TermfixRequest -Type 'suggest_command' -Payload @{
            partial = $line
            limit   = 1
        }

        if ($response -and $response.status -eq 'ok' -and $response.data.suggestions.Count -gt 0) {
            $suggestion = $response.data.suggestions[0].command
            [Microsoft.PowerShell.PSConsoleReadLine]::Replace(0, $line.Length, $suggestion)
        }
    }
}

# Initialize: record starting directory
$script:TermfixLastCwd = (Get-Location).Path
$null = Send-TermfixRequest -Type 'record_cd' -Payload @{ path = $script:TermfixLastCwd }
