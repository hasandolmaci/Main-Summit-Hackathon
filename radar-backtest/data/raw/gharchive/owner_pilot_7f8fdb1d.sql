-- owner_pilot
SELECT first_year, hash_bucket, count() AS n_owners, countIf(n_actor_events = 0) AS n_never_actor,
  countIf(n_personal_actor_events <= 0.01 * n_events) AS n_org_like
FROM (SELECT tupleElement(p, 1) AS login, cityHash64(login) % 10000 AS hash_bucket,
    countIf(tupleElement(p, 2) = 1) AS n_events,
  toYear(minIf(created_at, tupleElement(p, 2) = 1), 'UTC') AS first_year,
  if(n_events > 0, formatDateTime(minIf(created_at, tupleElement(p, 2) = 1), '%Y-%m-%d %H:%i:%S', 'UTC'), '') AS first_event_ts,
  if(n_events > 0, formatDateTime(maxIf(created_at, tupleElement(p, 2) = 1), '%Y-%m-%d %H:%i:%S', 'UTC'), '') AS last_event_ts,
  countIf(tupleElement(p, 2) = 2) AS n_actor_events,
  countIf(tupleElement(p, 2) = 2 AND event_type NOT IN ('ForkEvent', 'PublicEvent', 'MemberEvent', 'TeamAddEvent', 'Event', 'SponsorshipEvent')) AS n_personal_actor_events
  FROM (SELECT created_at, event_type,
         arrayJoin(if(match(lower(actor_login), '(\\[bot\\]$|-bot$|^dependabot|^renovate|^github-actions|^greenkeeper)'),
                      [(lower(if(position(repo_name, '/') > 0, substring(repo_name, 1, position(repo_name, '/') - 1), '')), 3), (lower(actor_login), 2)],
                      [(lower(if(position(repo_name, '/') > 0, substring(repo_name, 1, position(repo_name, '/') - 1), '')), 1), (lower(actor_login), 2)])) AS p
  FROM github_events
  WHERE cityHash64(lower(if(position(repo_name, '/') > 0, substring(repo_name, 1, position(repo_name, '/') - 1), ''))) % 10000 < 400 OR cityHash64(lower(actor_login)) % 10000 < 400)
  WHERE tupleElement(p, 1) != '' AND cityHash64(tupleElement(p, 1)) % 10000 < 400
  GROUP BY login
  HAVING n_events > 0)
GROUP BY first_year, hash_bucket
ORDER BY first_year, hash_bucket
FORMAT TSVWithNames
