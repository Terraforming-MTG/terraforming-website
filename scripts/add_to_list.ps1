$orcl_id = $args[0]
$list = $args[1]

$body = @{
    oracle_id = $orcl_id
    reason = ""
} | ConvertTo-Json

Invoke-RestMethod `
    -Uri "https://terraforming-mtg.terraforming.workers.dev/api/$list/" `
    -Method Post `
    -ContentType "application/json" `
    -Body $body
