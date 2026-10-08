param([switch]$Apply)
$ErrorActionPreference='Stop'
Import-Module (Join-Path $PSScriptRoot 'Demo.psm1') -Force
if(-not $Apply){throw 'Explicit -Apply required; replaces only the recorded synthetic seed versions.'}
$s=Get-DemoState
$admin=Get-DemoAdminToken
$app=$s.apps.Provision
$cert=Get-Item -LiteralPath "Cert:\CurrentUser\My\$($app.thumbprint)"
$graph=(Invoke-DemoGraph $admin "/servicePrincipals?`$filter=appId eq '00000003-0000-0000-c000-000000000000'&`$select=id,appRoles").value[0]
$role=@($graph.appRoles|Where-Object {$_.value -eq 'Sites.FullControl.All'})[0].id
try {
    Invoke-DemoGraph $admin "/applications/$($app.objectId)" PATCH @{keyCredentials=@(@{type='AsymmetricX509Cert';usage='Verify';key=[Convert]::ToBase64String($cert.RawData);displayName='Temporary seed repair';startDateTime=$cert.NotBefore.ToUniversalTime().ToString('o');endDateTime=$cert.NotAfter.ToUniversalTime().ToString('o')})}|Out-Null
    $existing=(Invoke-DemoGraph $admin "/servicePrincipals/$($app.servicePrincipalId)/appRoleAssignments").value.appRoleId
    if($existing -notcontains $role){Invoke-DemoGraph $admin "/servicePrincipals/$($app.servicePrincipalId)/appRoleAssignments" POST @{principalId=$app.servicePrincipalId;resourceId=$graph.id;appRoleId=$role}|Out-Null}
    $token=Get-DemoAppToken $app
    $repo=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
    $manifest=Get-Content -Raw -LiteralPath (Join-Path $repo 'examples\data\team_a_library\manifest.json')|ConvertFrom-Json -AsHashtable
    foreach($fixture in $manifest.items){
        $record=$s.sources[$fixture.name]
        if(-not $record){throw 'Missing synthetic registry record.'}
        $bytes=[IO.File]::ReadAllBytes((Join-Path $repo ('examples\data\team_a_library\'+$fixture.contentFile.Replace('/','\'))))
        $hash=[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant()
        if($hash -ne $record.sha256){throw 'Local fixture differs from the approved uploaded fixture registry.'}
        $uri="https://graph.microsoft.com/v1.0/drives/$($s.sites.Source.driveId)/items/$($record.id)/content"
        for($attempt=0;$attempt -lt 5;$attempt++){
            $response=Invoke-WebRequest -Method Put -Uri $uri -Headers @{Authorization="Bearer $token";'If-Match'=$record.etag} -Body $bytes -ContentType 'application/octet-stream' -SkipHttpErrorCheck
            if([int]$response.StatusCode -in @(500,502,503,504) -and $attempt -lt 4){Start-Sleep -Seconds ([Math]::Pow(2,$attempt+1));continue}
            break
        }
        if([int]$response.StatusCode -notin @(200,201)){throw "Seed repair failed HTTP $($response.StatusCode); request-id $($response.Headers['request-id']). No source versions will be overwritten without the recorded ETag."}
        $item=$response.Content|ConvertFrom-Json
        $record.etag=$item.eTag
        $record.seedRepair='byte-array-serialization'
        Save-DemoState $s
        Write-Output "Replaced exact synthetic bytes: $($fixture.name)"
    }
} finally {
    & (Join-Path $PSScriptRoot 'Remove-ProvisioningGrant.ps1') -Apply
}
