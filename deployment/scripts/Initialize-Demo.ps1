param([switch]$Apply)
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'Demo.psm1') -Force
if (-not $Apply) { throw 'Use -Apply only after approving the isolated synthetic demo setup.' }
$stateDir = Join-Path $env:LOCALAPPDATA 'CrossTeamKnowledgePublicExample\operator-configured'
$stateFile = Join-Path $stateDir 'state.json'
$state = if (Test-Path $stateFile) {Get-DemoState} else {@{tenantId='f0000000-0000-4000-8000-000000000019';subscriptionId='f0000000-0000-4000-8000-00000000003f';apps=@{};users=@{};groups=@{};sites=@{}}}
Save-DemoState $state
$admin = Get-DemoAdminToken
$graph = (Invoke-DemoGraph $admin "/servicePrincipals?`$filter=appId eq '00000003-0000-0000-c000-000000000000'&`$select=id,appRoles,oauth2PermissionScopes").value[0]

function New-DemoApplication([string]$Key,[string[]]$Roles,[switch]$PublicClient) {
    if ($state.apps.ContainsKey($Key)) { return }
    $display = "EXAMPLE-$Key"
    $existing = (Invoke-DemoGraph $admin "/applications?`$filter=displayName eq '$display'&`$select=id,appId").value
    if ($existing.Count) { throw "Untracked app $display already exists; reconcile state rather than duplicate." }
    $body = @{displayName=$display;signInAudience='AzureADMyOrg'}
    $cert = $null
    if ($PublicClient) {
        $body.isFallbackPublicClient = $true
        $body.publicClient = @{redirectUris=@('http://localhost')}
    } else {
        $cert = New-SelfSignedCertificate -Subject "CN=$display" -Type Custom -KeyAlgorithm RSA -KeyLength 2048 -KeyUsage DigitalSignature -KeyExportPolicy NonExportable -CertStoreLocation 'Cert:\CurrentUser\My' -NotAfter (Get-Date).AddDays(14)
        $body.keyCredentials = @(@{type='AsymmetricX509Cert';usage='Verify';key=[Convert]::ToBase64String($cert.RawData);displayName='Local non-exportable demo certificate';endDateTime=$cert.NotAfter.ToUniversalTime().ToString('o');startDateTime=$cert.NotBefore.ToUniversalTime().ToString('o')})
    }
    $app = Invoke-DemoGraph $admin '/applications' POST $body
    $state.apps[$Key] = @{clientId=$app.appId;objectId=$app.id;thumbprint=$(if($cert){$cert.Thumbprint}else{''})}
    Save-DemoState $state
    $sp = Invoke-DemoGraph $admin '/servicePrincipals' POST @{appId=$app.appId}
    $state.apps[$Key].servicePrincipalId = $sp.id
    Save-DemoState $state
    foreach ($role in $Roles) {
        $rid = @($graph.appRoles | Where-Object {$_.value -eq $role -and $_.allowedMemberTypes -contains 'Application'})[0].id
        if (-not $rid) { throw "Graph application role missing: $role" }
        $grant = Invoke-DemoGraph $admin "/servicePrincipals/$($sp.id)/appRoleAssignments" POST @{principalId=$sp.id;resourceId=$graph.id;appRoleId=$rid}
        $state.apps[$Key]["grant:$role"] = $grant.id
        Save-DemoState $state
    }
    Write-Output "Created application $display"
}

New-DemoApplication 'Provision' @('Sites.FullControl.All')
New-DemoApplication 'Pipeline' @('Sites.Selected','ExternalConnection.ReadWrite.OwnedBy','ExternalItem.ReadWrite.OwnedBy')
New-DemoApplication 'Reader' @() -PublicClient
$reader = $state.apps.Reader
if (-not $reader.graphConsent) {
    $consent = Invoke-DemoGraph $admin '/oauth2PermissionGrants' POST @{clientId=$reader.servicePrincipalId;consentType='AllPrincipals';resourceId=$graph.id;scope='User.Read Files.Read.All Sites.Read.All ExternalItem.Read.All'}
    $reader.graphConsent = $consent.id
    Save-DemoState $state
}

foreach ($alias in @('source-owner','reader','outsider')) {
    if ($state.users.ContainsKey($alias)) { continue }
    $upn = "example-$alias@example.invalid"
    $existing = (Invoke-DemoGraph $admin "/users?`$filter=userPrincipalName eq '$upn'&`$select=id").value
    if ($existing.Count) { throw "Untracked test identity already exists: $upn" }
    $password = 'Kx!'+[Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(30))+'9a'
    $body = @{accountEnabled=$true;displayName="Example $alias";mailNickname="example-$alias";userPrincipalName=$upn;usageLocation='KR';passwordProfile=@{forceChangePasswordNextSignIn=$false;password=$password}}
    $user = Invoke-DemoGraph $admin '/users' POST $body
    $state.users[$alias] = @{id=$user.id;upn=$upn}
    Save-DemoState $state
    ConvertTo-SecureString $password -AsPlainText -Force | ConvertFrom-SecureString | Set-Content -LiteralPath (Join-Path $stateDir "$alias.dpapi") -Encoding utf8
    $password = $null
    Write-Output "Created unlicensed synthetic test identity $alias"
}

foreach ($key in @('Source','Exchange','Readers')) {
    if ($state.groups.ContainsKey($key)) {continue}
    $name = if ($key -eq 'Readers') {'Example-Readers'} else {"EXAMPLE-$Key"}
    $existing = (Invoke-DemoGraph $admin "/groups?`$filter=displayName eq '$name'&`$select=id").value
    if ($existing.Count) {throw "Untracked group $name already exists."}
    $owner = if ($key -eq 'Source') {$state.users['source-owner'].id} else {'f0000000-0000-4000-8000-000000000031'}
    $unified = $key -ne 'Readers'
    $body = @{displayName=$name;description='Isolated synthetic cross-team knowledge demo. No customer data.';mailNickname=$name;mailEnabled=$unified;securityEnabled=(-not $unified);groupTypes=@();'owners@odata.bind'=@("https://graph.microsoft.com/v1.0/users/$owner")}
    if ($unified) {$body.visibility='Private';$body.groupTypes=@('Unified')}
    $group = Invoke-DemoGraph $admin '/groups' POST $body
    $state.groups[$key] = @{id=$group.id;name=$name}
    Save-DemoState $state
    Write-Output "Created private demo group $name"
}

foreach ($key in @('Readers','Exchange')) {
    $gid = $state.groups[$key].id
    $members = (Invoke-DemoGraph $admin "/groups/$gid/members?`$select=id").value.id
    foreach ($uid in @($state.users.reader.id,'f0000000-0000-4000-8000-000000000031')) {
        if ($members -notcontains $uid) { Invoke-DemoGraph $admin "/groups/$gid/members/`$ref" POST @{'@odata.id'="https://graph.microsoft.com/v1.0/users/$uid"} | Out-Null }
    }
}
$sourceGroup = $state.groups.Source.id
$ownerId = $state.users['source-owner'].id
$sourceMembers = (Invoke-DemoGraph $admin "/groups/$sourceGroup/members?`$select=id").value.id
if ($sourceMembers -notcontains $ownerId) {Invoke-DemoGraph $admin "/groups/$sourceGroup/members/`$ref" POST @{'@odata.id'="https://graph.microsoft.com/v1.0/users/$ownerId"} | Out-Null}
Write-Output 'Initialization complete. New users are unlicensed; no existing accounts or licenses changed.'
