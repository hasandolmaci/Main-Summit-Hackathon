SELECT toString(event_type) AS event_type, count() AS n
FROM github_events
WHERE file_time = toDateTime('2021-05-12 15:00:00', 'UTC')
GROUP BY event_type
ORDER BY event_type
FORMAT TSVWithNames
