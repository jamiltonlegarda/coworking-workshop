# Prueba del Lab 10: registro, login, 401, 403 y rutas protegidas.
# Uso (PowerShell, desde la raiz del repo):  .\test-auth.ps1
$base = "http://localhost:8002"
$userMail = "user_$(Get-Random)@test.com"
$userPass = "ClaveUser123"
$adminMail = "admin_test@test.com"
$adminPass = "ClaveAdmin123"

function Status($Method, $Uri, $Body = $null, $Headers = @{}, $ContentType = "application/json") {
    try {
        $p = @{ Method = $Method; Uri = $Uri; Headers = $Headers; UseBasicParsing = $true }
        if ($Body) { $p.Body = $Body; $p.ContentType = $ContentType }
        return [int](Invoke-WebRequest @p).StatusCode
    } catch { return [int]$_.Exception.Response.StatusCode }
}
function Check($name, $got, $expected) {
    $ok = if ($got -eq $expected) { "OK  " } else { "FALLA" }
    Write-Host ("[{0}] {1}: {2} (esperado {3})" -f $ok, $name, $got, $expected)
}

$regBody = (@{ email = $userMail; password = $userPass } | ConvertTo-Json)
Check "Registro nuevo"            (Status POST "$base/auth/register" $regBody) 201
Check "Registro duplicado"        (Status POST "$base/auth/register" $regBody) 409
Check "Login clave incorrecta"    (Status POST "$base/auth/login" "username=$userMail&password=mala" @{} "application/x-www-form-urlencoded") 401

$token = (Invoke-RestMethod -Method Post -Uri "$base/auth/login" -ContentType "application/x-www-form-urlencoded" -Body "username=$userMail&password=$userPass").access_token
$h = @{ Authorization = "Bearer $token" }

Check "GET /orders sin token"     (Status GET "$base/orders") 401
Check "Token invalido"            (Status GET "$base/auth/me" $null @{ Authorization = "Bearer abc.def.ghi" }) 401
Check "GET /auth/me con token"    (Status GET "$base/auth/me" $null $h) 200
Check "user en /admin/users"      (Status GET "$base/admin/users" $null $h) 403
Check "POST /orders con token"    (Status POST "$base/orders?space_id=1&hours=2" $null $h) 200

docker-compose exec -T orders-service python create_user.py $adminMail $adminPass admin | Out-Null
$adminToken = (Invoke-RestMethod -Method Post -Uri "$base/auth/login" -ContentType "application/x-www-form-urlencoded" -Body "username=$adminMail&password=$adminPass").access_token
$ha = @{ Authorization = "Bearer $adminToken" }
Check "admin en /admin/users"     (Status GET "$base/admin/users" $null $ha) 200
Check "admin cambia roles"        (Status PUT "$base/admin/users/$userMail/role?role=admin" $null $ha) 403

Write-Host "`nExpiracion: token con exp en el pasado debe dar 401"
$expired = docker-compose exec -T orders-service python -c "from datetime import timedelta; from auth import create_access_token; print(create_access_token({'sub':'$userMail','role':'user'}, timedelta(seconds=-5)))"
Check "Token expirado"            (Status GET "$base/auth/me" $null @{ Authorization = "Bearer $($expired.Trim())" }) 401