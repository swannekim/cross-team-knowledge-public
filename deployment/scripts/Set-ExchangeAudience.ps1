param([switch]$Apply)
$ErrorActionPreference='Stop'
Import-Module (Join-Path $PSScriptRoot 'Demo.psm1') -Force
if(-not $Apply){throw 'Explicit -Apply required for approved audience configuration.'}
$s=Get-DemoState
$pipeline=Get-DemoAppToken $s.apps.Pipeline
$admin=Get-DemoAdminToken
$result=Invoke-DemoGraph $pipeline "/drives/$($s.sites.Exchange.driveId)/root/invite" POST @{
    recipients=@(@{objectId=$s.groups.Readers.id});roles=@('read');requireSignIn=$true;sendInvitation=$false
}
if(-not @($result.value|Where-Object {$_.roles -contains 'read'}).Count){throw 'Exchange read grant not confirmed; existing membership unchanged.'}
$group=$s.groups.Exchange.id
$reader=$s.users.reader.id
$members=(Invoke-DemoGraph $admin "/groups/$group/members?`$select=id").value.id
if($members -contains $reader){
    Invoke-DemoGraph $admin "/groups/$group/members/$reader/`$ref" DELETE|Out-Null
}
$s.exchangeAudience=@{mode='Readers security group read on library root';permissionIds=@($result.value.id);readerRemovedFromEditGroup=$true;configuredAt=[DateTimeOffset]::UtcNow.ToString('o')}
Save-DemoState $s
$permissions=(Invoke-DemoGraph $pipeline "/drives/$($s.sites.Exchange.driveId)/root/permissions").value
$target=@($permissions|Where-Object {$_.id -in $s.exchangeAudience.permissionIds})
if(-not $target.Count -or @($target|Where-Object {$_.roles -notcontains 'read' -or $_.roles -contains 'write'}).Count){throw 'Exchange read-only ACL readback mismatch.'}
Write-Output 'Readers group has read on Exchange library; synthetic reader removed from group-granted edit. User-context effective rights still require sign-in validation.'
