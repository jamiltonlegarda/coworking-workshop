# Prueba del Lab 11: circuit breaker, recuperacion y rate limiter.
# Uso (PowerShell, desde la raiz del repo):  .\test-resilience.ps1
# Dura ~1,5 minutos (hay que esperar 60 s a que el circuito pueda recuperarse).
$base = "http://localhost:8002"
$mail = "chaos@test.com"; $pass = "ClaveChaos123"

function Status($Method, $Uri, $Headers = @{}) {
    try { return [int](Invoke-WebRequest -Method $Method -Uri $Uri -Headers $Headers -UseBasicParsing).StatusCode }
    catch { return [int]$_.Exception.Response.StatusCode }
}
function Circuit { (Invoke-RestMethod "$base/resilience/status").catalog_circuit }
function Check($name, $ok, $detail) {
    $tag = if ($ok) { "OK  " } else { "FALLA" }
    Write-Host ("[{0}] {1} {2}" -f $tag, $name, $detail)
}

# --- Preparacion: usuario de prueba y token ---
try { Invoke-RestMethod -Method Post -Uri "$base/auth/register" -ContentType "application/json" `
      -Body (@{ email = $mail; password = $pass } | ConvertTo-Json) | Out-Null } catch {}
$token = (Invoke-RestMethod -Method Post -Uri "$base/auth/login" -ContentType "application/x-www-form-urlencoded" `
          -Body "username=$mail&password=$pass").access_token
$h = @{ Authorization = "Bearer $token" }
$order = "$base/orders?space_id=1&hours=1"

Write-Host "`n=== A. Circuit breaker ==="
Check "Circuito arranca cerrado" ((Circuit).state -eq "closed") "($((Circuit).state))"
docker-compose stop catalog-service | Out-Null
Write-Host "Catalogo apagado. Enviando ordenes (cada una reintenta 3 veces: 1 s y 2 s de espera)..."

$t1 = Measure-Command { $c1 = Status POST $order $h }
Check "Orden 1 con catalogo caido -> 503" ($c1 -eq 503) "($c1, $([int]$t1.TotalSeconds) s por los reintentos)"
$t2 = Measure-Command { $c2 = Status POST $order $h }
Check "Orden 2 -> 503 y el circuito se abre (5 fallos)" (($c2 -eq 503) -and ((Circuit).state -eq "open")) "($c2, estado: $((Circuit).state))"
$t3 = Measure-Command { $c3 = Status POST $order $h }
Check "Orden 3 -> 503 inmediato (circuito abierto)" (($c3 -eq 503) -and ($t3.TotalSeconds -lt 1)) "($c3, $([math]::Round($t3.TotalSeconds,2)) s)"

Write-Host "`n=== B. Recuperacion ==="
docker-compose start catalog-service | Out-Null
Write-Host "Catalogo encendido. Esperando 62 s a que el circuito pase a prueba (HALF_OPEN)..."
Start-Sleep -Seconds 62
$c4 = Status POST $order $h
Check "Primera orden tras la espera -> 200 y circuito cerrado" (($c4 -eq 200) -and ((Circuit).state -eq "closed")) "($c4, estado: $((Circuit).state))"

Write-Host "`n=== C. Rate limiter (100 solicitudes por IP cada 60 s) ==="
$ok = 0; $blocked = 0
1..105 | ForEach-Object {
    $code = Status GET "$base/resilience/status"
    if ($code -eq 200) { $ok++ } elseif ($code -eq 429) { $blocked++ }
}
Check "Pasan como maximo 100 y el resto recibe 429" (($blocked -ge 1) -and ($ok -le 100)) "(200: $ok, 429: $blocked)"
Write-Host "Nota: espera 60 s antes de volver a usar la API."