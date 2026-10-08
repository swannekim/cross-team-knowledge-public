param([switch]$Apply)
$ErrorActionPreference='Stop'
Import-Module (Join-Path $PSScriptRoot 'Demo.psm1') -Force
if(-not $Apply){throw 'Explicit -Apply required.'}
$s=Get-DemoState
$token=Get-DemoAppToken $s.apps.Provision
$admin=Get-DemoAdminToken
$repo=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
foreach($key in @('Source','Exchange')){
    if(-not $s.sites.ContainsKey($key)){
        $resolved=Invoke-DemoGraph $token "/groups/$($s.groups[$key].id)/sites/root"
        $drive=Invoke-DemoGraph $token "/sites/$($resolved.id)/drive"
        $s.sites[$key]=@{id=$resolved.id;url=$resolved.webUrl;driveId=$drive.id}
        Save-DemoState $s
    }
    $site=$s.sites[$key]
    $permissions=(Invoke-DemoGraph $token "/sites/$($site.id)/permissions").value
    $existing=@($permissions|Where-Object {($_.grantedToIdentities.application.id -contains $s.apps.Pipeline.clientId) -or ($_.grantedToIdentitiesV2.application.id -contains $s.apps.Pipeline.clientId)})
    if(-not $existing.Count){
        $role=if($key -eq 'Source'){'read'}else{'write'}
        $grant=Invoke-DemoGraph $token "/sites/$($site.id)/permissions" POST @{roles=@($role);grantedToIdentities=@(@{application=@{id=$s.apps.Pipeline.clientId;displayName='Example-Pipeline'}})}
        $site.pipelineGrant=$grant.id
        Save-DemoState $s
    }
}
$source=$s.sites.Source
$root=Invoke-DemoGraph $token "/drives/$($source.driveId)/root"
$manifest=Get-Content -Raw -LiteralPath (Join-Path $repo 'examples\data\team_a_library\manifest.json')|ConvertFrom-Json -AsHashtable
if(-not $s.ContainsKey('sources')){$s.sources=@{}}
foreach($item in $manifest.items){
    if($s.sources.ContainsKey($item.name)){continue}
    $path=$item.contentFile.Substring(6)
    $parent=$root.id
    $parts=$path.Split('/')
    foreach($folder in $parts[0..($parts.Length-2)]){
        $children=(Invoke-DemoGraph $token "/drives/$($source.driveId)/items/$parent/children?`$select=id,name,folder").value
        $found=@($children|Where-Object {$_.name -eq $folder -and $_.ContainsKey('folder')})
        if($found.Count){$parent=$found[0].id}else{
            $created=Invoke-DemoGraph $token "/drives/$($source.driveId)/items/$parent/children" POST @{name=$folder;folder=@{};'@microsoft.graph.conflictBehavior'='fail'}
            $parent=$created.id
        }
    }
    $bytes=[IO.File]::ReadAllBytes((Join-Path $repo ('examples\data\team_a_library\'+$item.contentFile.Replace('/','\'))))
    $url="/drives/$($source.driveId)/items/${parent}:/$([uri]::EscapeDataString($item.name)):/content"
    $raw=Invoke-DemoGraph $token $url PUT $bytes 'application/octet-stream'
    $uploaded=$raw|ConvertFrom-Json -AsHashtable
    $s.sources[$item.name]=@{id=$uploaded.id;etag=$uploaded.eTag;path=$item.folderPath+'/'+$item.name;fixtureLabel=$item.sensitivityLabel;sha256=[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant();optIn=($item.name -notlike '*FDC_event_log*');classificationAuthority='synthetic-fixture-registry-not-Purview'}
    Save-DemoState $s
    Write-Output "Uploaded synthetic fixture $($item.name)"
}
$site=$s.sites.Exchange
$drive=Invoke-DemoGraph $token "/drives/$($site.driveId)?`$select=id,list&`$expand=list"
$site.listId=$drive.list.id
if(-not $site.listId){$list=Invoke-DemoGraph $token "/drives/$($site.driveId)/list";$site.listId=$list.id}
Save-DemoState $s
$columns=(Invoke-DemoGraph $token "/sites/$($site.id)/lists/$($site.listId)/columns").value.name
foreach($name in @('KXContractId','KXSourceFingerprint','KXApprovalId','KXApprovedOutputHash','KXExpiresAt','KXClassificationAuthority')){
    if($columns -notcontains $name){Invoke-DemoGraph $token "/sites/$($site.id)/lists/$($site.listId)/columns" POST @{name=$name;text=@{}}|Out-Null}
}
$gid=$s.groups.Source.id
$adminId='f0000000-0000-4000-8000-000000000031'
foreach($relation in @('owners','members')){
    $ids=(Invoke-DemoGraph $admin "/groups/$gid/$relation`?`$select=id").value.id
    if($ids -contains $adminId){Invoke-DemoGraph $admin "/groups/$gid/$relation/$adminId/`$ref" DELETE|Out-Null}
}
$s.sourceAclSealedAt=[DateTimeOffset]::UtcNow.ToString('o')
Save-DemoState $s
Write-Output 'Sites prepared; pipeline read on source / write on Exchange; admin removed from source group.'
& (Join-Path $PSScriptRoot 'Set-ExchangeAudience.ps1') -Apply
