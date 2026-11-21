$codex = Join-Path (Get-AppxPackage OpenAI.Codex).InstallLocation 'app\resources\codex.exe'
$d = 'C:\Users\hasan\OneDrive\Desktop\main hackathon\backtest-data'
$p = Get-Content -Raw -Path "$d\prompts\R2.txt"
'' | & $codex exec --skip-git-repo-check --sandbox workspace-write -C $d -m gpt-6.1-sol -c model_reasoning_effort=medium -c sandbox_workspace_write.network_access=true -o "$d\codex_R2.md" $p *> "$d\codex_R2.log"
