-- owner_activity
WITH cands AS (SELECT arrayJoin(['airbyte-co', 'airbyte-eng', 'airbyte-lab', 'airbyte-org', 'airbyte-oss', 'airbyte-software', 'airbyte-team', 'airbyte-tech', 'airbyteco', 'airbyteeng', 'airbytelab', 'airbyteorg', 'airbyteoss', 'airbytesoftware', 'airbyteteam', 'airbytetech', 'anthropic-co', 'anthropic-eng', 'anthropic-lab', 'anthropic-org', 'anthropic-oss', 'anthropic-software', 'anthropic-team', 'anthropic-tech', 'anthropicco', 'anthropiceng', 'anthropiclab', 'anthropicorg', 'anthropicoss', 'anthropicsoftware', 'anthropicteam', 'anthropictech', 'get-airbyte', 'get-anthropic', 'get-gitpod', 'get-shopify', 'gitpod-co', 'gitpod-eng', 'gitpod-lab', 'gitpod-org', 'gitpod-oss', 'gitpod-software', 'gitpod-team', 'gitpod-tech', 'gitpodco', 'gitpodeng', 'gitpodlab', 'gitpodorg', 'gitpodoss', 'gitpodsoftware', 'gitpodteam', 'gitpodtech', 'go-airbyte', 'go-anthropic', 'go-gitpod', 'go-shopify', 'goairbyte', 'goanthropic', 'gogitpod', 'goshopify', 'hello-airbyte', 'hello-anthropic', 'hello-gitpod', 'hello-shopify', 'helloairbyte', 'helloanthropic', 'hellogitpod', 'helloshopify', 'join-airbyte', 'join-anthropic', 'join-gitpod', 'join-shopify', 'joinairbyte', 'joinanthropic', 'joingitpod', 'joinshopify', 'shopify-co', 'shopify-eng', 'shopify-lab', 'shopify-org', 'shopify-oss', 'shopify-software', 'shopify-team', 'shopify-tech', 'shopifyco', 'shopifyeng', 'shopifylab', 'shopifyorg', 'shopifyoss', 'shopifysoftware', 'shopifyteam', 'shopifytech', 'try-airbyte', 'try-anthropic', 'try-gitpod', 'try-shopify', 'use-airbyte', 'use-anthropic', 'use-gitpod', 'use-shopify']) AS login)
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
