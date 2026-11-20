-- owner_sample
SELECT login, hash_bucket, first_year, first_event_ts, last_event_ts, n_events, n_actor_events, n_personal_actor_events
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
  WHERE cityHash64(lower(if(position(repo_name, '/') > 0, substring(repo_name, 1, position(repo_name, '/') - 1), ''))) % 10000 < 200 OR cityHash64(lower(actor_login)) % 10000 < 200)
  WHERE tupleElement(p, 1) != '' AND cityHash64(tupleElement(p, 1)) % 10000 < 200
  GROUP BY login
  HAVING n_events > 0)
WHERE n_personal_actor_events <= 0.01 * n_events AND ((first_year = 2010 AND hash_bucket < 200) OR (first_year = 2011 AND hash_bucket < 200) OR (first_year = 2012 AND hash_bucket < 200) OR (first_year = 2013 AND hash_bucket < 200) OR (first_year = 2014 AND hash_bucket < 200) OR (first_year = 2015 AND hash_bucket < 200) OR (first_year = 2016 AND hash_bucket < 200) OR (first_year = 2017 AND hash_bucket < 200) OR (first_year = 2018 AND hash_bucket < 200) OR (first_year = 2019 AND hash_bucket < 200) OR (first_year = 2020 AND hash_bucket < 200) OR (first_year = 2021 AND hash_bucket < 200) OR (first_year = 2022 AND hash_bucket < 200) OR (first_year = 2023 AND hash_bucket < 200))
ORDER BY first_year, hash_bucket, login
LIMIT 500001
FORMAT TSVWithNames
