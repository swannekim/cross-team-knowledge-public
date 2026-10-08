param([switch]$Apply)
$ErrorActionPreference='Stop'
Import-Module (Join-Path $PSScriptRoot 'Demo.psm1') -Force
if(-not $Apply){throw 'Explicit -Apply required.'}
$s=Get-DemoState
$app=$s.apps.Provision
$admin=Get-DemoAdminToken
$graph=(Invoke-DemoGraph $admin "/servicePrincipals?`$filter=appId eq '00000003-0000-0000-c000-000000000000'&`$select=id,appRoles").value[0]
$role=@($graph.appRoles|Where-Object {$_.value -eq 'Sites.FullControl.All'})[0].id
$grants=(Invoke-DemoGraph $admin "/servicePrincipals/$($app.servicePrincipalId)/appRoleAssignments").value
foreach($grant in @($grants|Where-Object {$_.resourceId -eq $graph.id -and $_.appRoleId -eq $role})){
    Invoke-DemoGraph $admin "/servicePrincipals/$($app.servicePrincipalId)/appRoleAssignments/$($grant.id)" DELETE|Out-Null
}
Invoke-DemoGraph $admin "/applications/$($app.objectId)" PATCH @{keyCredentials=@()}|Out-Null
$s.apps.Provision.provisioningDisabledAt=[DateTimeOffset]::UtcNow.ToString('o')
Save-DemoState $s
Write-Output 'Temporary provisioning permission and app certificate credential removed. Existing tokens may remain valid until expiry.'
