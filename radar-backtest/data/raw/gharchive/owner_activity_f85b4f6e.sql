-- owner_activity
WITH cands AS (SELECT arrayJoin(['getsentry', 'google', 'posthog', 'sentry', 'stripe', 'torvalds', 'zzz-no-such-owner-zz']) AS login)
SELECT tupleElement(p, 1) AS login,
  countIf(tupleElement(p, 2) = 1) AS n_events,
  if(n_events > 0, formatDateTime(minIf(created_at, tupleElement(p, 2) = 1), '%Y-%m-%d %H:%i:%S', 'UTC'), '') AS first_event_ts,
  if(n_events > 0, formatDateTime(maxIf(created_at, tupleElement(p, 2) = 1), '%Y-%m-%d %H:%i:%S', 'UTC'), '') AS last_event_ts,
  countIf(tupleElement(p, 2) = 2) AS n_actor_events,
  countIf(tupleElement(p, 2) = 2 AND event_type NOT IN ('ForkEvent', 'PublicEvent', 'MemberEvent', 'TeamAddEvent', 'Event', 'SponsorshipEvent')) AS n_personal_actor_events
FROM (SELECT created_at, event_type,
         arrayJoin(if(match(lower(actor_login), '(\\[bot\\]$|-bot$|^dependabot|^renovate|^github-actions|^greenkeeper)'),
                      [(lower(if(position(repo_name, '/') > 0, substring(repo_name, 1, position(repo_name, '/') - 1), '')), 3), (lower(actor_login), 2)],
                      [(lower(if(position(repo_name, '/') > 0, substring(repo_name, 1, position(repo_name, '/') - 1), '')), 1), (lower(actor_login), 2)])) AS p
  FROM github_events
  WHERE lower(if(position(repo_name, '/') > 0, substring(repo_name, 1, position(repo_name, '/') - 1), '')) IN (SELECT login FROM cands) OR lower(actor_login) IN (SELECT login FROM cands))
WHERE tupleElement(p, 1) IN (SELECT login FROM cands)
GROUP BY login
ORDER BY login
LIMIT 500001
FORMAT TSVWithNames
