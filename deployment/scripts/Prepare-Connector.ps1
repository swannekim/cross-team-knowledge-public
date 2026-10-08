param([switch]$Apply)
$ErrorActionPreference='Stop'
Import-Module (Join-Path $PSScriptRoot 'Demo.psm1') -Force
if(-not $Apply){throw 'Explicit -Apply required.'}
$s=Get-DemoState
$token=Get-DemoAppToken $s.apps.Pipeline
$id='ExampleDerived'
$connection=Invoke-DemoGraph $token "/external/connections/$id" -AllowNotFound
if(-not $connection){
    $connection=Invoke-DemoGraph $token '/external/connections' POST @{id=$id;name='KX synthetic derived knowledge';description='Approved synthetic summaries for the KX demo audience. Original SharePoint files are not shared.'}
}
$s.connectionId=$id
Save-DemoState $s
$schema=Invoke-DemoGraph $token "/external/connections/$id/schema" -AllowNotFound
if($schema.properties.Count -gt 0 -and $connection.state -eq 'ready'){
    Write-Output 'Connector schema already ready.'
    return
}
$repo=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$schemaText=Get-Content -Raw -LiteralPath (Join-Path $repo 'examples\artifacts\arch_a\schema.json')
$response=Invoke-WebRequest -Method Patch -Uri "https://graph.microsoft.com/v1.0/external/connections/$id/schema" -Headers @{Authorization="Bearer $token"} -Body $schemaText -ContentType 'application/json' -SkipHttpErrorCheck
if([int]$response.StatusCode -ne 202){throw "Schema registration returned HTTP $($response.StatusCode): $($response.Content)"}
$location=[string]($response.Headers.Location|Select-Object -First 1)
if(-not $location.StartsWith('https://graph.microsoft.com/')){throw 'Schema operation lacks a valid Graph location.'}
$s.schemaOperation=$location
$s.schemaSubmittedAt=[DateTimeOffset]::UtcNow.ToString('o')
Save-DemoState $s
Write-Output 'Schema registration accepted; polling operation (maximum 20 minutes).'
$end=[DateTimeOffset]::UtcNow.AddMinutes(20)
do{
    Start-Sleep -Seconds 15
    $op=Invoke-DemoGraph $token $location
    if($op.status -eq 'failed'){throw 'Graph schema registration failed.'}
    if($op.status -eq 'completed'){
        $s.schemaReadyAt=[DateTimeOffset]::UtcNow.ToString('o')
        Save-DemoState $s
        Write-Output "Connector schema ready: $id"
        return
    }
}while([DateTimeOffset]::UtcNow -lt $end)
throw 'Schema registration pending after 20 minutes; inspect saved operation before retrying.'
