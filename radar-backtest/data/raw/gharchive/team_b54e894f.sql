SELECT lower(if(position(repo_name, '/') > 0, substring(repo_name, 1, position(repo_name, '/') - 1), '')) AS owner, lower(actor_login) AS actor,
  formatDateTime(min(created_at), '%Y-%m-%d %H:%i:%S', 'UTC') AS first_push_ts
FROM github_events
WHERE event_type = 'PushEvent' AND owner IN ('airbytehq', 'hashicorp', 'posthog', 'supabase', 'vercel') AND actor_login != '' AND NOT match(lower(actor_login), '(\\[bot\\]$|-bot$|^dependabot|^renovate|^github-actions|^greenkeeper)')
GROUP BY owner, actor
ORDER BY owner, actor
LIMIT 500001
FORMAT TSVWithNames
