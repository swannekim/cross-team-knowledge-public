[CmdletBinding(SupportsShouldProcess,ConfirmImpact='High')]
param([switch]$Apply)
$ErrorActionPreference='Stop'
Import-Module (Join-Path $PSScriptRoot 'Demo.psm1') -Force
$s=Get-DemoState
if($s.tenantId -ne 'f0000000-0000-4000-8000-000000000019' -or $s.subscriptionId -ne 'f0000000-0000-4000-8000-00000000003f'){throw 'Unexpected demo tenant or subscription.'}
if(-not $Apply){throw 'Cleanup deletes ONLY this demo. Review state.json, then use -Apply and confirm.'}
$admin=Get-DemoAdminToken
foreach($g in $s.groups.Values){
    $actual=Invoke-DemoGraph $admin "/groups/$($g.id)?`$select=id,displayName" -AllowNotFound
    if($actual -and $actual.displayName -ne $g.name){throw 'Group identity mismatch; cleanup stopped.'}
}
foreach($u in $s.users.Values){
    $actual=Invoke-DemoGraph $admin "/users/$($u.id)?`$select=id,userPrincipalName" -AllowNotFound
    if($actual -and ($actual.userPrincipalName -ne $u.upn -or $u.upn -notmatch '^example-(source-owner|reader|outsider)@example\.invalid$')){throw 'User identity mismatch; cleanup stopped.'}
}
foreach($entry in $s.apps.GetEnumerator()){
    $actual=Invoke-DemoGraph $admin "/applications/$($entry.Value.objectId)?`$select=id,displayName" -AllowNotFound
    if($actual -and $actual.displayName -ne "KX-$($entry.Key)-20261007"){throw 'App identity mismatch; cleanup stopped.'}
}
$rg=az group show --subscription $s.subscriptionId --name rg-example-knowledge -o json|ConvertFrom-Json
if($LASTEXITCODE -ne 0 -or $rg.tags.purpose -ne 'cross-team-knowledge-demo' -or $rg.tags.data -ne 'synthetic'){throw 'Azure resource-group ownership verification failed.'}
if(-not $PSCmdlet.ShouldProcess('KX 20261007 demo resources, private sites, test users and apps','Delete isolated demo; retain local code and GitHub repo')){return}
$pipeline=Get-DemoAppToken $s.apps.Pipeline
$connection=Invoke-DemoGraph $pipeline '/external/connections/ExampleDerived' -AllowNotFound
if($connection){Invoke-DemoGraph $pipeline '/external/connections/ExampleDerived' DELETE|Out-Null}
az group delete --subscription $s.subscriptionId --name rg-example-knowledge --yes --only-show-errors
if($LASTEXITCODE -ne 0){throw 'Azure deletion incomplete; inspect before removing identities.'}
foreach($g in $s.groups.Values){if(Invoke-DemoGraph $admin "/groups/$($g.id)" -AllowNotFound){Invoke-DemoGraph $admin "/groups/$($g.id)" DELETE|Out-Null}}
foreach($u in $s.users.Values){if(Invoke-DemoGraph $admin "/users/$($u.id)" -AllowNotFound){Invoke-DemoGraph $admin "/users/$($u.id)" DELETE|Out-Null}}
foreach($app in $s.apps.Values){if(Invoke-DemoGraph $admin "/applications/$($app.objectId)" -AllowNotFound){Invoke-DemoGraph $admin "/applications/$($app.objectId)" DELETE|Out-Null}}
foreach($app in $s.apps.Values){if($app.thumbprint -and (Test-Path "Cert:\CurrentUser\My\$($app.thumbprint)")){Remove-Item -LiteralPath "Cert:\CurrentUser\My\$($app.thumbprint)"}}
$s.removedAt=[DateTimeOffset]::UtcNow.ToString('o')
Save-DemoState $s
Write-Output 'Demo cleanup requested and completed where reported. SharePoint/Entra retention may preserve soft-deleted content. Local code and private GitHub repository retained.'
