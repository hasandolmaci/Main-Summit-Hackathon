$codex = Join-Path (Get-AppxPackage OpenAI.Codex).InstallLocation 'app\resources\codex.exe'
$d = 'C:\Users\hasan\OneDrive\Desktop\main hackathon\backtest-data'
$p = Get-Content -Raw -Path "$d\prompts\RESEARCH2.txt"
'' | & $codex exec --skip-git-repo-check --sandbox read-only -C $d -m gpt-6-astra -c model_reasoning_effort=high -c tools.web_search=true -o "$d\codex_RESEARCH2.md" $p *> "$d\codex_RESEARCH2.log"
