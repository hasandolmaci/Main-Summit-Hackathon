SELECT lower(if(position(repo_name, '/') > 0, substring(repo_name, 1, position(repo_name, '/') - 1), '')) AS owner, toDate(created_at, 'UTC') AS day,
  countIf(event_type = 'PushEvent') AS push,
  countIf(event_type = 'PullRequestEvent') AS pr,
  countIf(event_type = 'IssuesEvent') AS issues,
  countIf(event_type = 'ReleaseEvent') AS release
FROM github_events
WHERE event_type IN ('PushEvent', 'PullRequestEvent', 'IssuesEvent', 'ReleaseEvent')
  AND owner IN ('airbytehq', 'hashicorp', 'posthog', 'supabase', 'vercel') AND NOT match(lower(actor_login), '(\\[bot\\]$|-bot$|^dependabot|^renovate|^github-actions|^greenkeeper)')
GROUP BY owner, day
ORDER BY owner, day
LIMIT 500001
FORMAT TSVWithNames
