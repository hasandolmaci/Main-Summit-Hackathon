param([string]$Name, [string]$Model = 'gpt-6.1-sol', [string]$Effort = 'medium')
$codex = Join-Path (Get-AppxPackage OpenAI.Codex).InstallLocation 'app\resources\codex.exe'
$d = 'C:\Users\hasan\OneDrive\Desktop\main hackathon\backtest-data'
$p = (Get-Content -Raw -Path "$d\prompts\V2_COMMON.txt") + "`n`nYOUR TASK:`n" + (Get-Content -Raw -Path "$d\prompts\$Name.txt")
$p = $p.Replace('"', "'")
'' | & $codex exec --skip-git-repo-check --sandbox workspace-write -C $d -m $Model -c model_reasoning_effort=$Effort -c tools.web_search=true -c sandbox_workspace_write.network_access=true -o "$d\codex_$Name.md" $p *> "$d\codex_$Name.log"
