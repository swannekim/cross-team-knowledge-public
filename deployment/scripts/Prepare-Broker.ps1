param([switch]$Apply)
$ErrorActionPreference='Stop'
Import-Module (Join-Path $PSScriptRoot 'Demo.psm1') -Force
if(-not $Apply){throw 'Explicit -Apply required.'}
$s=Get-DemoState
$admin=Get-DemoAdminToken
$graph=(Invoke-DemoGraph $admin "/servicePrincipals?`$filter=appId eq '00000003-0000-0000-c000-000000000000'&`$select=id,appRoles").value[0]
if(-not $s.apps.ContainsKey('Broker')){
    $scopeId=[guid]::NewGuid().ToString()
    $scope=@{id=$scopeId;value='Knowledge.Ask';type='Admin';isEnabled=$true;adminConsentDisplayName='Ask approved synthetic demo knowledge';adminConsentDescription='Read approved synthetic knowledge through the isolated KX broker.'}
    $app=Invoke-DemoGraph $admin '/applications' POST @{displayName='example-broker';signInAudience='AzureADMyOrg';api=@{requestedAccessTokenVersion=2;oauth2PermissionScopes=@($scope)}}
    $s.apps.Broker=@{clientId=$app.appId;objectId=$app.id;scopeId=$scopeId}
    Save-DemoState $s
    Invoke-DemoGraph $admin "/applications/$($app.id)" PATCH @{identifierUris=@("api://$($app.appId)")} | Out-Null
    $sp=Invoke-DemoGraph $admin '/servicePrincipals' POST @{appId=$app.appId}
    $s.apps.Broker.servicePrincipalId=$sp.id
    Save-DemoState $s
}
$broker=$s.apps.Broker
if(-not $broker.readerConsent){
    $c=Invoke-DemoGraph $admin '/oauth2PermissionGrants' POST @{clientId=$s.apps.Reader.servicePrincipalId;consentType='AllPrincipals';resourceId=$broker.servicePrincipalId;scope='Knowledge.Ask'}
    $broker.readerConsent=$c.id
    Save-DemoState $s
}
$mi=az identity show --subscription $s.subscriptionId --resource-group rg-example-knowledge --name id-example-broker --output json | ConvertFrom-Json -AsHashtable
if($LASTEXITCODE -ne 0){throw 'Managed identity lookup failed.'}
$s.brokerIdentity=@{id=$mi.id;clientId=$mi.clientId;principalId=$mi.principalId}
Save-DemoState $s
$existing=(Invoke-DemoGraph $admin "/servicePrincipals/$($mi.principalId)/appRoleAssignments").value.appRoleId
foreach($name in @('User.Read.All','GroupMember.Read.All')){
    $role=@($graph.appRoles|Where-Object {$_.value -eq $name})[0]
    if($existing -notcontains $role.id){Invoke-DemoGraph $admin "/servicePrincipals/$($mi.principalId)/appRoleAssignments" POST @{principalId=$mi.principalId;resourceId=$graph.id;appRoleId=$role.id}|Out-Null}
}
$key=az storage account keys list --subscription $s.subscriptionId --resource-group rg-example-knowledge --account-name exampleknowledgestorage --query '[0].value' -o tsv
if($LASTEXITCODE -ne 0){throw 'Storage key retrieval failed.'}
$env:AZURE_STORAGE_KEY=$key
try {
    az storage container create --account-name exampleknowledgestorage --name broker-state --public-access off --only-show-errors -o none
    if($LASTEXITCODE -ne 0){throw 'Private Blob state container creation failed.'}
} finally {Remove-Item Env:AZURE_STORAGE_KEY -ErrorAction SilentlyContinue;$key=$null}
$blob="/subscriptions/$($s.subscriptionId)/resourceGroups/rg-example-knowledge/providers/Microsoft.Storage/storageAccounts/exampleknowledgestorage/blobServices/default/containers/broker-state"
$blobGrant=az role assignment list --subscription $s.subscriptionId --assignee-object-id $mi.principalId --scope $blob --query "[?roleDefinitionName=='Storage Blob Data Contributor'].id" -o tsv
if(-not $blobGrant){az role assignment create --subscription $s.subscriptionId --assignee-object-id $mi.principalId --assignee-principal-type ServicePrincipal --role 'Storage Blob Data Contributor' --scope $blob --only-show-errors -o none;if($LASTEXITCODE -ne 0){throw 'Blob state grant failed.'}}
$registry="/subscriptions/$($s.subscriptionId)/resourceGroups/rg-example-knowledge/providers/Microsoft.ContainerRegistry/registries/exampleknowledgeacr"
$assign=az role assignment list --subscription $s.subscriptionId --assignee-object-id $mi.principalId --scope $registry --query "[?roleDefinitionName=='AcrPull'].id" -o tsv
if(-not $assign){az role assignment create --subscription $s.subscriptionId --assignee-object-id $mi.principalId --assignee-principal-type ServicePrincipal --role AcrPull --scope $registry --only-show-errors -o none;if($LASTEXITCODE -ne 0){throw 'AcrPull grant failed.'}}
Write-Output 'Broker API, test-client consent, managed identity Graph roles, registry pull and private Blob state configured in approved example.'
