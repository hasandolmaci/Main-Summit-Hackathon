SELECT toYear(created_at, 'UTC') AS year, toString(event_type) AS event_type, count() AS n_events,
  countIf(action != 'none') AS n_action_set,
  countIf(action = 'opened') AS n_action_opened,
  countIf(author_association != 'NONE') AS n_author_association_set,
  countIf(position(repo_name, '/') > 0) AS n_repo_slash,
  countIf(actor_login = '') AS n_actor_empty,
  countIf(NOT match(lower(actor_login), '^[a-z0-9-]{1,39}$') AND NOT is_bot) AS n_actor_nonstandard,
  countIf(is_bot) AS n_bot
FROM (SELECT created_at, event_type, action, author_association, repo_name, actor_login,
             match(lower(actor_login), '(\\[bot\\]$|-bot$|^dependabot|^renovate|^github-actions|^greenkeeper)') AS is_bot
      FROM github_events WHERE event_type = 'SponsorshipEvent')
GROUP BY year, event_type
ORDER BY year
FORMAT TSVWithNames
