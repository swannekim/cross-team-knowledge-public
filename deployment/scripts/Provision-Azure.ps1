param([switch]$Apply)
$ErrorActionPreference='Stop'
Import-Module (Join-Path $PSScriptRoot 'Demo.psm1') -Force
if(-not $Apply){throw 'Explicit -Apply required for the isolated Azure demo.'}
$s=Get-DemoState
Get-DemoAdminToken|Out-Null
$sub='f0000000-0000-4000-8000-00000000003f'
if($s.subscriptionId -ne $sub){throw 'Only EXAMPLE-SUBSCRIPTION-CURRENT is approved.'}
$name='rg-example-knowledge'
$exists=az group exists --subscription $sub --name $name -o tsv
if($LASTEXITCODE -ne 0){throw 'Resource group lookup failed.'}
if($exists -eq 'true'){
    $group=az group show --subscription $sub --name $name -o json|ConvertFrom-Json
    if($group.tags.purpose -ne 'cross-team-knowledge-demo' -or $group.tags.data -ne 'synthetic'){throw 'Existing group is not owned by this demo.'}
} else {
    az group create --subscription $sub --name $name --location koreacentral --tags purpose=cross-team-knowledge-demo data=synthetic owner=example-operator -o none
    if($LASTEXITCODE -ne 0){throw 'Resource group creation failed.'}
}
az identity create --subscription $sub --resource-group $name --name id-example-broker -o none
if($LASTEXITCODE -ne 0){throw 'Managed identity creation failed.'}
az containerapp env create --subscription $sub --resource-group $name --name cae-example-knowledge --location koreacentral --logs-destination none -o none
if($LASTEXITCODE -ne 0){throw 'Container Apps environment creation failed.'}
az storage account create --subscription $sub --resource-group $name --name exampleknowledgestorage --location koreacentral --sku Standard_LRS --kind StorageV2 --allow-blob-public-access false --min-tls-version TLS1_2 -o none
if($LASTEXITCODE -ne 0){throw 'Storage account creation failed.'}
az acr create --subscription $sub --resource-group $name --name exampleknowledgeacr --sku Basic --admin-enabled false -o none
if($LASTEXITCODE -ne 0){throw 'Registry creation failed.'}
& (Join-Path $PSScriptRoot 'Prepare-Broker.ps1') -Apply
& (Join-Path $PSScriptRoot 'Initialize-BlobState.ps1') -Apply
Write-Output 'Isolated Azure infrastructure configured in the approved -2 subscription. Build and deploy separately.'
