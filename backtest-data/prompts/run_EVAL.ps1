$codex = Join-Path (Get-AppxPackage OpenAI.Codex).InstallLocation 'app\resources\codex.exe'
$d = 'C:\Users\hasan\OneDrive\Desktop\main hackathon\backtest-data'
$p = Get-Content -Raw -Path "$d\prompts\EVAL.txt"
'' | & $codex exec --skip-git-repo-check --sandbox read-only -C $d -m gpt-6-astra -c model_reasoning_effort=high -o "$d\codex_EVAL.md" $p *> "$d\codex_EVAL.log"
