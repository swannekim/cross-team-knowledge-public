$ErrorActionPreference = 'Stop'
$script:TenantId = 'f0000000-0000-4000-8000-000000000019'
$script:SubscriptionId = 'f0000000-0000-4000-8000-00000000003f'
$script:StateDir = Join-Path $env:LOCALAPPDATA 'CrossTeamKnowledgePublicExample\operator-configured'
$script:StateFile = Join-Path $script:StateDir 'state.json'

if ($script:TenantId -like 'f0000000-*' -or $script:SubscriptionId -like 'f0000000-*') {
    throw 'Sanitized public example: configure and authorize your own tenant, subscription, identities and resources before running live scripts. Historical evidence is not deployment approval.'
}

function Get-DemoState {
    if (-not (Test-Path $script:StateFile)) { throw "Run Initialize-Demo.ps1 first." }
    $state = Get-Content -Raw -LiteralPath $script:StateFile | ConvertFrom-Json -AsHashtable
    if ($state.tenantId -ne $script:TenantId -or $state.subscriptionId -ne $script:SubscriptionId) {
        throw 'State must target the user-approved approved example subscription. Migrate state explicitly before continuing.'
    }
    $state
}

function Save-DemoState([hashtable]$State) {
    New-Item -ItemType Directory -Path $script:StateDir -Force | Out-Null
    $tmp = "$script:StateFile.tmp"
    $State | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $tmp -Encoding utf8
    Move-Item -LiteralPath $tmp -Destination $script:StateFile -Force
}

function Get-DemoAdminToken {
    $raw = az account get-access-token --subscription $script:SubscriptionId --resource-type ms-graph --output json
    if ($LASTEXITCODE -ne 0) { throw 'Demo admin token acquisition failed.' }
    $token = ($raw | ConvertFrom-Json).accessToken
    $me = Invoke-RestMethod -Uri 'https://graph.microsoft.com/v1.0/me?$select=id,userPrincipalName' -Headers @{Authorization="Bearer $token"}
    if ($me.userPrincipalName -ne 'admin@example.invalid') { throw 'Unexpected admin identity; refusing mutation.' }
    $token
}

function ConvertTo-Base64Url([byte[]]$Bytes) {
    [Convert]::ToBase64String($Bytes).TrimEnd('=').Replace('+','-').Replace('/','_')
}

function Get-DemoAppToken([hashtable]$App, [string]$Scope = 'https://graph.microsoft.com/.default') {
    $cert = Get-Item -LiteralPath "Cert:\CurrentUser\My\$($App.thumbprint)"
    $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $endpoint = "https://login.microsoftonline.com/$script:TenantId/oauth2/v2.0/token"
    $header = @{alg='RS256';typ='JWT';x5t=(ConvertTo-Base64Url $cert.GetCertHash())} | ConvertTo-Json -Compress
    $claims = @{aud=$endpoint;iss=$App.clientId;sub=$App.clientId;jti=[guid]::NewGuid().ToString();nbf=$now-30;exp=$now+300} | ConvertTo-Json -Compress
    $unsigned = (ConvertTo-Base64Url ([Text.Encoding]::UTF8.GetBytes($header))) + '.' + (ConvertTo-Base64Url ([Text.Encoding]::UTF8.GetBytes($claims)))
    $rsa = [Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPrivateKey($cert)
    try { $sig = $rsa.SignData([Text.Encoding]::ASCII.GetBytes($unsigned),[Security.Cryptography.HashAlgorithmName]::SHA256,[Security.Cryptography.RSASignaturePadding]::Pkcs1) }
    finally { $rsa.Dispose() }
    $form = @{client_id=$App.clientId;grant_type='client_credentials';scope=$Scope;client_assertion_type='urn:ietf:params:oauth:client-assertion-type:jwt-bearer';client_assertion=$unsigned+'.'+(ConvertTo-Base64Url $sig)}
    for ($attempt=0; $attempt -lt 12; $attempt++) {
        try { return (Invoke-RestMethod -Method Post -Uri $endpoint -Body $form).access_token }
        catch {
            $detail = if ($_.ErrorDetails.Message) { $_.ErrorDetails.Message | ConvertFrom-Json -ErrorAction SilentlyContinue } else { $null }
            if ($detail.error_codes -contains 700027 -and $attempt -lt 11) {
                Start-Sleep -Seconds 10
                continue
            }
            throw
        }
    }
}

function Invoke-DemoGraph {
    param([string]$Token,[string]$Path,[string]$Method='GET',$Body,[string]$ContentType='application/json',[switch]$AllowNotFound)
    $uri = if ($Path.StartsWith('https://graph.microsoft.com/')) {$Path} elseif ($Path.StartsWith('/')) {"https://graph.microsoft.com/v1.0$Path"} else {throw 'Graph path must be absolute or root-relative.'}
    $headers = @{Authorization="Bearer $Token";'client-request-id'=[guid]::NewGuid().ToString()}
    for ($attempt=0; $attempt -lt 5; $attempt++) {
        $args = @{Uri=$uri;Method=$Method;Headers=$headers;ContentType=$ContentType;SkipHttpErrorCheck=$true}
        if ($null -ne $Body) {
            if ($ContentType -eq 'application/json') { $args.Body = $Body|ConvertTo-Json -Depth 30 -Compress }
            else { $args.Body = $Body }
        }
        $response = Invoke-WebRequest @args
        $status = [int]$response.StatusCode
        if ($status -in @(429,503,504) -and $attempt -lt 4) {
            $wait = [Math]::Pow(2,$attempt)
            $seconds = 0
            if ([int]::TryParse([string]($response.Headers['Retry-After'] | Select-Object -First 1),[ref]$seconds)) {$wait=[Math]::Min(120,$seconds)}
            Start-Sleep -Seconds $wait
            continue
        }
        $parsed = if ($response.Content) {if ($ContentType -eq 'application/json') {$response.Content|ConvertFrom-Json -AsHashtable} else {$response.Content}} else {$null}
        if ($status -eq 404 -and $AllowNotFound) { return $null }
        if ($status -lt 200 -or $status -ge 300) {
            $code = if ($parsed -is [hashtable]) {$parsed.error.code} else {'NonJsonError'}
            $message = if ($parsed -is [hashtable]) {$parsed.error.message} else {'Non-JSON response'}
            throw "Graph $Method $Path failed HTTP $status ($code): $message; request-id $($response.Headers['request-id'])."
        }
        return $parsed
    }
}

function Get-DemoTestToken([string]$Alias,[string]$Scope='https://graph.microsoft.com/.default') {
    if ($Alias -notin @('source-owner','reader','outsider')) {throw 'Only newly created synthetic identities may use this test-only password grant.'}
    $state = Get-DemoState
    $secret = (Get-Content -Raw -LiteralPath (Join-Path $script:StateDir "$Alias.dpapi")).Trim() | ConvertTo-SecureString
    $credential = [pscredential]::new($state.users[$Alias].upn,$secret)
    $form = @{client_id=$state.apps.Reader.clientId;grant_type='password';username=$credential.UserName;password=$credential.GetNetworkCredential().Password;scope=$Scope}
    try {
        (Invoke-RestMethod -Method Post -Uri "https://login.microsoftonline.com/$script:TenantId/oauth2/v2.0/token" -Body $form).access_token
    } finally {$form.password=$null;$credential=$null;$secret.Dispose()}
}

Export-ModuleMember -Function Get-DemoState,Save-DemoState,Get-DemoAdminToken,Get-DemoAppToken,Get-DemoTestToken,Invoke-DemoGraph
