SELECT toString(event_type) AS event_type, count() AS n
FROM github_events
WHERE created_at >= toDateTime('2013-05-08 22:00:00', 'UTC') AND created_at < toDateTime('2013-05-08 23:00:00', 'UTC')
GROUP BY event_type
ORDER BY event_type
FORMAT TSVWithNames
