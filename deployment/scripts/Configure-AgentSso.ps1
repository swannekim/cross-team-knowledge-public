param(
    [switch]$Apply,
    [Parameter(Mandatory)][string]$RegistrationId,
    [Parameter(Mandatory)][string]$ApplicationIdUri
)
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'Demo.psm1') -Force
if (-not $Apply) {throw 'Explicit -Apply after agent/SSO preview approval is required.'}
$state = Get-DemoState
$binding = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($RegistrationId)).Split('##')
if ($binding.Count -ne 2 -or $binding[0] -ne $state.tenantId) {throw 'SSO registration belongs to another tenant or has an unsupported format.'}
$registrationGuid = [guid]::Parse($binding[1]).ToString()
$expectedUri = "api://auth-$registrationGuid/$($state.apps.Broker.clientId)"
if ($ApplicationIdUri -ne $expectedUri) {throw 'Generated SSO Application ID URI does not match registration and broker.'}
$admin = Get-DemoAdminToken
try {
    $app = Invoke-DemoGraph $admin "/applications/$($state.apps.Broker.objectId)?`$select=id,appId,identifierUris,api,web"
    if ($app.appId -ne $state.apps.Broker.clientId -or $app.api.requestedAccessTokenVersion -ne 2) {
        throw 'Unexpected broker application or token version.'
    }
    $scope = @($app.api.oauth2PermissionScopes | Where-Object {$_.value -eq 'Knowledge.Ask' -and $_.isEnabled})
    if ($scope.Count -ne 1) {throw 'Exactly one enabled Knowledge.Ask scope is required.'}
    $callback = 'https://teams.microsoft.com/api/platform/v1.0/oAuthConsentRedirect'
    $client = 'ab3be6b7-f5df-413d-ac2d-abf1e3fd9c0b'
    $uris = @(@($app.identifierUris) + $ApplicationIdUri | Select-Object -Unique)
    $redirects = @(@($app.web.redirectUris) + $callback | Select-Object -Unique)
    $preauthorized = @($app.api.preAuthorizedApplications)
    $existing = @($preauthorized | Where-Object {$_.appId -eq $client})
    if ($existing.Count -gt 1) {throw 'Duplicate token-store preauthorization records require explicit reconciliation.'}
    if ($existing.Count -eq 1) {
        $existing[0].delegatedPermissionIds = @(@($existing[0].delegatedPermissionIds) + $scope[0].id | Select-Object -Unique)
    } else {
        $preauthorized += @{appId=$client;delegatedPermissionIds=@($scope[0].id)}
    }
    Invoke-DemoGraph $admin "/applications/$($app.id)" PATCH @{
        identifierUris=$uris
        web=@{redirectUris=$redirects}
        api=@{preAuthorizedApplications=$preauthorized}
    } | Out-Null
    $actual = Invoke-DemoGraph $admin "/applications/$($app.id)?`$select=id,appId,identifierUris,api,web"
    if ($actual.api.requestedAccessTokenVersion -ne 2 -or
        $actual.identifierUris -notcontains $ApplicationIdUri -or
        $actual.identifierUris -notcontains "api://$($app.appId)" -or
        $actual.web.redirectUris -notcontains $callback -or
        @($actual.api.preAuthorizedApplications | Where-Object {$_.appId -eq $client -and $_.delegatedPermissionIds -contains $scope[0].id}).Count -ne 1) {
        throw 'SSO configuration readback failed.'
    }
    foreach ($uri in $app.identifierUris) {
        if ($actual.identifierUris -notcontains $uri) {throw 'An existing identifier URI was lost.'}
    }
    foreach ($uri in $app.web.redirectUris) {
        if ($actual.web.redirectUris -notcontains $uri) {throw 'An existing redirect URI was lost.'}
    }
    $state.agentSso = @{
        registrationId=$RegistrationId
        applicationIdUri=$ApplicationIdUri
        brokerClientId=$app.appId
        scopeId=$scope[0].id
        preauthorizedClientId=$client
        callback=$callback
        configuredAt=[DateTimeOffset]::UtcNow.ToString('o')
        tokenVersion=2
        apiAudienceUnchanged=$app.appId
    }
    Save-DemoState $state
    $state.agentSso | ConvertTo-Json -Depth 5
} finally {$admin=$null}
