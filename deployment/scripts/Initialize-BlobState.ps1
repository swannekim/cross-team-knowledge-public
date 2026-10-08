param([switch]$Apply)
$ErrorActionPreference='Stop'
Import-Module (Join-Path $PSScriptRoot 'Demo.psm1') -Force
if(-not $Apply){throw 'Explicit -Apply required. Existing state is never overwritten.'}
$s=Get-DemoState
$repo=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$contract=Get-Content -Raw -LiteralPath (Join-Path $repo 'deployment\runtime\contract.json')|ConvertFrom-Json
$marker="KX_BROKER_STATE_V1`n$($s.tenantId):$($s.apps.Broker.clientId):$($contract.contractId)"
$file=Join-Path $env:LOCALAPPDATA 'CrossTeamKnowledgePublicExample\operator-configured\blob-initial-marker.bin'
[IO.File]::WriteAllBytes($file,[Text.Encoding]::UTF8.GetBytes($marker))
$key=az storage account keys list --subscription $s.subscriptionId --resource-group rg-example-knowledge --account-name exampleknowledgestorage --query '[0].value' -o tsv
if($LASTEXITCODE -ne 0){throw 'Storage key unavailable.'}
$env:AZURE_STORAGE_KEY=$key
try {
    $exists=az storage blob exists --account-name exampleknowledgestorage --container-name broker-state --name broker-state.sqlite --query exists -o tsv --only-show-errors
    if($LASTEXITCODE -ne 0){throw 'Blob existence check failed.'}
    if($exists -eq 'true'){Write-Output 'State blob already exists; unchanged.';return}
    az storage blob upload --account-name exampleknowledgestorage --container-name broker-state --name broker-state.sqlite --file $file --if-none-match '*' --only-show-errors --query '{etag:etag,lastModified:lastModified}' -o json
    if($LASTEXITCODE -ne 0){throw 'State marker creation failed; never overwrite an existing state blob.'}
}finally{Remove-Item Env:AZURE_STORAGE_KEY -ErrorAction SilentlyContinue;$key=$null}
