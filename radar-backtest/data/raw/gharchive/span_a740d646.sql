SELECT lower(if(position(repo_name, '/') > 0, substring(repo_name, 1, position(repo_name, '/') - 1), '')) AS owner,
  formatDateTime(min(created_at), '%Y-%m-%d %H:%i:%S', 'UTC') AS first_event_ts,
  formatDateTime(max(created_at), '%Y-%m-%d %H:%i:%S', 'UTC') AS last_event_ts,
  count() AS n_events
FROM github_events
WHERE owner IN ('airbytehq', 'hashicorp', 'posthog', 'supabase', 'vercel') AND NOT match(lower(actor_login), '(\\[bot\\]$|-bot$|^dependabot|^renovate|^github-actions|^greenkeeper)')
GROUP BY owner
ORDER BY owner
LIMIT 500001
FORMAT TSVWithNames
