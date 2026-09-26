param(
    [string]$Python = (Join-Path (Get-Location) '.venv\Scripts\python.exe'),
    [string]$Report = (Join-Path (Get-Location) 'var\reports\verify-m4.json')
)

$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Report) | Out-Null
$checks = @()
function Invoke-Check { param([string]$Name,[scriptblock]$Command); try { $output = & $Command 2>&1; $code = $LASTEXITCODE; if ($null -eq $code) { $code = 0 }; [pscustomobject]@{name=$Name;exit_code=$code;output=($output -join "`n")} } catch { [pscustomobject]@{name=$Name;exit_code=1;output=$_.Exception.Message} } }
$checks += Invoke-Check 'm4_tests' { & $Python -m pytest --basetemp 'var\pytest-tmp-m4' backend/tests/test_agent_limits.py backend/tests/test_agent_tool_allowlist.py backend/tests/test_agent_trace_sse.py backend/tests/test_langchain_agent.py backend/tests/test_conversation_scope.py -q }
$checks += Invoke-Check 'agent_import' { & $Python -c "from langchain.agents import create_agent; from backend.app.application.langchain_agent import LangChainAgentAdapter; print('langchain=create_agent; tools=closed-read-only')" }
$checks += Invoke-Check 'agent_api_smoke' { & $Python scripts\smoke_m4.py }
$status = if (($checks | Where-Object {$_.exit_code -ne 0}).Count -eq 0) {'PASS'} else {'FAIL'}
$payload = [pscustomobject]@{gate='M4';status=$status;checks=$checks;provider_mode='LangChain create_agent with bounded local tests'}
$payload | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 -LiteralPath $Report
$payload | ConvertTo-Json -Depth 8
if ($status -ne 'PASS') { exit 1 }
