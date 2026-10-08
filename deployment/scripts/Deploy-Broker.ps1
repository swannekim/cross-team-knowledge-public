param(
    [switch]$Apply,
    [Parameter(Mandatory)][string]$Image,
    [switch]$InitialDeployment,
    [ValidateRange(90,1800)][int]$DrainTimeoutSeconds=600,
    [ValidateRange(1,1800)][int]$ReadyTimeoutSeconds=600
)
$ErrorActionPreference='Stop'
Import-Module (Join-Path $PSScriptRoot 'Demo.psm1') -Force
if(-not $Apply){throw 'Explicit -Apply required.'}
$imagePattern='^exampleknowledgeacr\.azurecr\.io/kx-broker@sha256:[0-9a-f]{64}$'
if($Image -cnotmatch $imagePattern){throw 'Use a built immutable demo image digest from the approved -2 registry.'}
$s=Get-DemoState
$appName='example-broker'
$groupName='rg-example-knowledge'
$rg="/subscriptions/$($s.subscriptionId)/resourceGroups/rg-example-knowledge"
$resource="$rg/providers/Microsoft.App/containerApps/$appName"
$resourceUrl="https://management.azure.com${resource}?api-version=2024-03-01"
$identity=$s.brokerIdentity.id
$url='https://broker.example.invalid'
if($identity -ne "$rg/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-example-broker"){
    throw 'Unexpected broker managed identity.'
}
if($s.broker -and ($s.broker.resourceId -ne $resource -or $s.broker.url -ne $url)){
    throw 'Unexpected broker deployment target in state.'
}
$vars=@{
    KX_TENANT_ID=$s.tenantId
    KX_BROKER_CLIENT_ID=$s.apps.Broker.clientId
    KX_AUDIENCE_GROUP_ID=$s.groups.Readers.id
    KX_GRAPH_CLIENT_ID=$s.brokerIdentity.clientId
    KX_CONTRACT_PATH='/app/runtime/contract.json'
    KX_SOURCE_SNAPSHOT_PATH='/app/runtime/snapshot.json'
    KX_PUBLIC_ORIGIN=$url
    KX_STORAGE_MODE='azure-blob'
    KX_STORAGE_ROOT='/app/state'
    KX_STATE_PATH='/app/state/broker.sqlite'
    KX_BLOB_ACCOUNT_URL='https://storage.example.invalid'
    KX_BLOB_CONTAINER='broker-state'
    KX_BLOB_NAME='broker-state.sqlite'
    KX_REPLICA_COUNT='1'
    KX_PURVIEW_MODE='disabled-demo'
}
$healthFailures=@{}

function Invoke-BrokerAz([string[]]$Arguments) {
    $raw=& az @Arguments --subscription $s.subscriptionId --only-show-errors --output json
    if($LASTEXITCODE -ne 0){throw "Azure CLI failed: az $($Arguments -join ' ') (exit $LASTEXITCODE)."}
    if($raw){$raw|ConvertFrom-Json -AsHashtable}
}

function Get-BrokerApp {
    Invoke-BrokerAz @('rest','--method','get','--url',$resourceUrl)
}

function Get-BrokerRevisions {
    Invoke-BrokerAz @('containerapp','revision','list','--name',$appName,'--resource-group',$groupName,'--all')
}

function Get-BrokerReplicas([string]$Revision) {
    Invoke-BrokerAz @('containerapp','replica','list','--name',$appName,'--resource-group',$groupName,'--revision',$Revision)
}

function Assert-BrokerAbsent {
    $existing=@(Invoke-BrokerAz @('containerapp','list','--resource-group',$groupName)|
        Where-Object {$_.id -eq $resource -or $_.name -eq $appName})
    if($existing.Count){throw 'InitialDeployment requires an absent app; omit that switch for a replacement.'}
}

function Get-InitialBrokerDefinition {
    Assert-BrokerAbsent
    $group=Invoke-BrokerAz @('group','show','--name',$groupName)
    if($group.id -ne $rg -or $group.location -ne 'koreacentral' -or
       $group.tags.purpose -ne 'cross-team-knowledge-demo' -or $group.tags.data -ne 'synthetic'){
        throw 'Initial deployment requires the existing, correctly tagged demo resource group.'
    }
    $environment=Invoke-BrokerAz @('containerapp','env','show','--name','cae-example-knowledge','--resource-group',$groupName)
    if($environment.id -ne "$rg/providers/Microsoft.App/managedEnvironments/cae-example-knowledge" -or
       $environment.properties.defaultDomain -ne ([uri]$url).Host.Substring($appName.Length+1)){
        throw 'Initial deployment requires the approved existing Container Apps environment.'
    }
    # Read-only bootstrap checks using the provisioning operator's existing account-key permissions.
    # Never initialize, overwrite, acquire, break or release the state Blob here.
    $storageArgs=@('--account-name','exampleknowledgestorage','--auth-mode','key')
    $container=Invoke-BrokerAz (@('storage','container','show','--name','broker-state')+$storageArgs)
    $blob=Invoke-BrokerAz (@('storage','blob','show','--container-name','broker-state','--name','broker-state.sqlite')+$storageArgs)
    if($container.properties.publicAccess -notin @($null,'off') -or
       $blob.properties.blobType -ne 'BlockBlob' -or $blob.properties.contentLength -lt 1 -or
       $blob.properties.contentLength -gt 16777216 -or $blob.properties.lease.status -ne 'unlocked' -or
       $blob.properties.lease.state -notin @('available','expired')){
        throw 'Initial deployment requires an existing private, nonempty, bounded and unleased bootstrap/state Blob.'
    }
    # The broker validates the marker/SQLite binding under its lease before health can pass.
    $template=@{
        containers=@(@{
            name='broker';image=$Image
            env=@($vars.GetEnumerator()|ForEach-Object {@{name=$_.Key;value=$_.Value}})
            resources=@{cpu=0.25;memory='0.5Gi'}
            probes=@(
                @{type='Startup';httpGet=@{path='/healthz';port=8080};initialDelaySeconds=5;periodSeconds=5;failureThreshold=30},
                @{type='Readiness';httpGet=@{path='/healthz';port=8080};periodSeconds=10;failureThreshold=3}
            )
        })
        scale=@{minReplicas=0;maxReplicas=1;rules=@(@{name='http';http=@{metadata=@{concurrentRequests='10'}}})}
    }
    Assert-BrokerTemplate $template
    return @{
        location='koreacentral'
        tags=@{purpose='cross-team-knowledge-demo';data='synthetic'}
        identity=@{type='UserAssigned';userAssignedIdentities=@{$identity=@{}}}
        properties=@{
            managedEnvironmentId=$environment.id
            configuration=@{
                activeRevisionsMode='Multiple'
                ingress=@{external=$true;targetPort=8080;transport='auto';allowInsecure=$false}
                registries=@(@{server='exampleknowledgeacr.azurecr.io';identity=$identity})
            }
            template=$template
        }
    }
}

function Assert-BrokerTemplate($Template) {
    $containers=@($Template.containers)
    if($containers.Count -ne 1 -or $containers[0].name -ne 'broker' -or
       $containers[0].image -cnotmatch $imagePattern -or @($Template.initContainers|Where-Object {$null -ne $_}).Count -gt 0){
        throw 'Unexpected broker container/image; refusing to replace this template.'
    }
    if($Template.scale.minReplicas -ne 0 -or $Template.scale.maxReplicas -ne 1){
        throw 'Expected minReplicas=0 and maxReplicas=1; review scale changes explicitly.'
    }
    foreach($key in $vars.Keys){
        $entry=@($containers[0].env|Where-Object name -eq $key)
        if(-not $vars[$key] -or $entry.Count -ne 1 -or $entry[0].value -cne $vars[$key] -or $entry[0].secretRef){
            throw "Unexpected or missing broker environment setting $key."
        }
    }
    foreach($type in @('Startup','Readiness')){
        $probe=@($containers[0].probes|Where-Object type -eq $type)
        if($probe.Count -ne 1 -or $probe[0].httpGet.path -ne '/healthz' -or $probe[0].httpGet.port -ne 8080){
            throw "Expected $type health probe on /healthz:8080."
        }
    }
}

function Assert-BrokerApp($App) {
    $p=$App.properties
    $ingress=$p.configuration.ingress
    $environment="$rg/providers/Microsoft.App/managedEnvironments/cae-example-knowledge"
    if($App.id -ne $resource -or $App.location -ne 'koreacentral' -or
       $App.tags.purpose -ne 'cross-team-knowledge-demo' -or $App.tags.data -ne 'synthetic' -or
       ($p.managedEnvironmentId -ne $environment -and $p.environmentId -ne $environment) -or
       -not $App.identity.userAssignedIdentities.Contains($identity) -or
       $ingress.fqdn -ne ([uri]$url).Host -or $ingress.external -ne $true -or
       $ingress.targetPort -ne 8080 -or $ingress.allowInsecure -ne $false){
        throw 'Unexpected app target, tags, identity, environment or ingress; refusing mutation.'
    }
    $registry=@($p.configuration.registries|Where-Object server -eq 'exampleknowledgeacr.azurecr.io')
    if($registry.Count -ne 1 -or $registry[0].identity -ne $identity){
        throw 'Unexpected broker registry identity.'
    }
    if(@($ingress.traffic|Where-Object label).Count -gt 0){
        throw 'Labeled traffic requires explicit operator review before this single-writer handoff.'
    }
    Assert-BrokerTemplate $p.template
}

function Set-BrokerPatch([hashtable]$Properties) {
    # JSON Merge Patch preserves unrelated configuration, identities, tags and secrets.
    @{location='koreacentral';properties=$Properties}|ConvertTo-Json -Depth 50|
        Set-Content -LiteralPath $file -Encoding utf8NoBOM
    Invoke-BrokerAz @('rest','--method','patch','--url',$resourceUrl,'--body',"@$file")|Out-Null
}

function Wait-BrokerControlPlane([switch]$RecoveryPreflight) {
    $deadline=(Get-Date).AddSeconds($ReadyTimeoutSeconds)
    do {
        $app=Get-BrokerApp
        Assert-BrokerApp $app
        if($app.properties.provisioningState -in @('Failed','Canceled')){
            # A prior failed revision must be recoverable, but only before submitting its replacement.
            if($RecoveryPreflight -and $app.properties.configuration.activeRevisionsMode -eq 'Multiple'){return $app}
            throw "Container App provisioning failed: $($app.properties.provisioningState)."
        }
        if($app.properties.provisioningState -eq 'Succeeded' -and
           $app.properties.configuration.activeRevisionsMode -eq 'Multiple'){return $app}
        Start-Sleep -Seconds 5
    } while((Get-Date) -lt $deadline)
    throw 'Timed out waiting for Multiple revision mode / control-plane completion.'
}

function Assert-KnownRevisions($Revisions,[string]$NewRevision='') {
    foreach($revision in $Revisions){
        if($revision.name -notin $oldNames -and $revision.name -ne $NewRevision){
            throw "Unexpected concurrent revision $($revision.name); stop other deployments before retrying."
        }
        if($revision.name -in $oldNames -and
           ($revision.properties.active -ne $false -or $revision.properties.replicas -gt 0)){
            throw "Old revision $($revision.name) is still active or running; no concurrent rollback is safe."
        }
    }
}

function Test-BrokerTraffic($App,[string]$Revision) {
    $traffic=@($App.properties.configuration.ingress.traffic)
    return ($traffic.Count -eq 1 -and $traffic[0].revisionName -eq $Revision -and
        $traffic[0].weight -eq 100 -and $traffic[0].latestRevision -ne $true)
}

function Write-BrokerHealthFailure([string]$BaseUrl,[string]$Detail) {
    $detail=($Detail -replace '[\r\n\t]',' ')
    $detail=$detail.Substring(0,[Math]::Min(256,$detail.Length))
    if(-not $healthFailures.ContainsKey($BaseUrl)){$healthFailures[$BaseUrl]=@{count=0;detail=''}}
    $entry=$healthFailures[$BaseUrl]
    $entry.detail=$detail
    $entry.count++
    if($entry.count -le 5){Write-Warning "Health check $BaseUrl/healthz failed: $detail"}
    if($entry.count -eq 5){Write-Warning "Further health warnings for $BaseUrl are suppressed; the last failure is retained for timeout reporting."}
    return $false
}

function Test-BrokerHealth([string]$BaseUrl) {
    try {
        $response=Invoke-WebRequest -Uri "$BaseUrl/healthz" -TimeoutSec 15 -MaximumRedirection 0 -SkipHttpErrorCheck
    } catch [System.Net.Http.HttpRequestException], [System.Net.WebException], [System.Threading.Tasks.TaskCanceledException], [System.TimeoutException] {
        return (Write-BrokerHealthFailure $BaseUrl "$($_.Exception.GetType().Name): $($_.Exception.Message)")
    }
    if($response.StatusCode -ne 200){return (Write-BrokerHealthFailure $BaseUrl "HTTP $($response.StatusCode)")}
    try {$health=$response.Content|ConvertFrom-Json -AsHashtable}
    catch [System.ArgumentException] {return (Write-BrokerHealthFailure $BaseUrl 'Invalid JSON health response.')}
    if($health -isnot [System.Collections.IDictionary] -or $health.status -ne 'ok' -or
       $health.checks -isnot [System.Collections.IDictionary]){
        return (Write-BrokerHealthFailure $BaseUrl 'Missing successful health status/checks object.')
    }
    foreach($check in @('persistenceReady','contractActive','snapshotFresh','configurationUnchanged')){
        if($health.checks[$check] -ne $true){return (Write-BrokerHealthFailure $BaseUrl "Check $check is not true.")}
    }
    return $true
}

$mutex=[Threading.Mutex]::new($false,'Local\PUBLIC-EXAMPLE-Broker-Deploy')
$locked=$false
$file=$null
$deployment=$null
$rollbackImage=$null
try {
    try {$locked=$mutex.WaitOne(0)} catch [Threading.AbandonedMutexException] {$locked=$true}
    if(-not $locked){throw 'Another broker deployment is running on this machine.'}
    Get-DemoAdminToken|Out-Null
    $app=$null
    $oldNames=@()
    $anchor=$null
    if($InitialDeployment){
        $initialBody=Get-InitialBrokerDefinition
    } else {
    $app=Get-BrokerApp
    Assert-BrokerApp $app
    if($app.properties.provisioningState -notin @('Succeeded','Failed','Canceled')){
        throw 'Another Container App control-plane operation is in progress.'
    }
    if($app.properties.configuration.activeRevisionsMode -notin @('Single','Multiple')){
        throw 'Unexpected revision mode.'
    }
    $oldRevisions=@(Get-BrokerRevisions)
    $oldNames=@($oldRevisions.name)
    if($oldNames.Count -eq 0 -or $app.properties.latestRevisionName -notin $oldNames){
        throw 'Could not identify all existing broker revisions.'
    }
    foreach($revision in $oldRevisions){
        if($revision.id -ne "$resource/revisions/$($revision.name)" -or
           @($revision.properties.template.containers).Count -ne 1 -or
           $revision.properties.template.containers[0].name -ne 'broker' -or
           $revision.properties.template.containers[0].image -cnotmatch $imagePattern){
            throw "Unexpected existing revision/image: $($revision.name)."
        }
    }
    $anchor=$app.properties.latestRevisionName
    $previousReady=@($oldRevisions|Where-Object name -eq $app.properties.latestReadyRevisionName)
    if($previousReady.Count -eq 1){$rollbackImage=$previousReady[0].properties.template.containers[0].image}
    }
    $suffix="deploy-$((Get-Date).ToUniversalTime().ToString('yyyyMMddHHmmss'))-$([guid]::NewGuid().ToString('N').Substring(0,8))"
    $newRevision="$appName--$suffix"
    $repoRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
    $scratch=New-Item -ItemType Directory -Path (Join-Path $repoRoot '.scratch') -Force
    $file=Join-Path $scratch.FullName "$suffix.json"
    if(-not $s.broker){$s.broker=@{resourceId=$resource;url=$url}}
    $deployment=@{
        status=$(if($InitialDeployment){'submitting'}else{'draining'});image=$Image;revision=$newRevision
        startedAt=[DateTimeOffset]::UtcNow.ToString('o')
        previousRevisions=$oldNames;previousReadyRevision=$app.properties.latestReadyRevisionName
    }
    $s.broker.deployment=$deployment
    Save-DemoState $s

    if($InitialDeployment){
        Assert-BrokerAbsent
        $initialBody.properties.template.revisionSuffix=$suffix
        $initialBody|ConvertTo-Json -Depth 50|Set-Content -LiteralPath $file -Encoding utf8NoBOM
        Invoke-BrokerAz @('rest','--method','put','--url',$resourceUrl,'--body',"@$file")|Out-Null
    } else {
    # Single mode can reactivate the previous lease holder while its replacement is Activating.
    # Keep Multiple mode permanently, with exactly one active revision after verification.
    # Never use revision restart: its rolling replacement can overlap replicas even with maxReplicas=1.
    # Recovery also requires deactivate, drain, lease expiry, then activate (or create a new suffix).
    if($app.properties.configuration.activeRevisionsMode -ne 'Multiple'){
        Invoke-BrokerAz @('containerapp','revision','set-mode','--name',$appName,'--resource-group',$groupName,'--mode','multiple')|Out-Null
    }
    $app=Wait-BrokerControlPlane -RecoveryPreflight
    # Never leave a "latestRevision" rule that could send public traffic to an unverified image.
    Set-BrokerPatch @{configuration=@{ingress=@{traffic=@(@{revisionName=$anchor;weight=100})}}}
    $app=Wait-BrokerControlPlane -RecoveryPreflight
    if(-not (Test-BrokerTraffic $app $anchor)){throw 'Could not pin traffic to the old revision before drain.'}
    foreach($revision in @(Get-BrokerRevisions)){
        if($revision.name -notin $oldNames){throw "Unexpected concurrent revision $($revision.name)."}
        if($revision.properties.active -eq $true){
            Invoke-BrokerAz @('containerapp','revision','deactivate','--name',$appName,'--resource-group',$groupName,'--revision',$revision.name)|Out-Null
        }
    }
    Write-Output 'Old revisions deactivating. Planned downtime; existing Blob state is never reset or unlocked.'
    $deadline=(Get-Date).AddSeconds($DrainTimeoutSeconds)
    $quietSince=$null
    $drained=$false
    do {
        $revisions=@(Get-BrokerRevisions)
        $quiet=$true
        foreach($revision in $revisions){
            if($revision.name -notin $oldNames){throw "Unexpected concurrent revision $($revision.name)."}
            if($revision.properties.active -ne $false -or $revision.properties.replicas -gt 0 -or
               @(Get-BrokerReplicas $revision.name).Count -ne 0){$quiet=$false}
        }
        if(@($oldNames|Where-Object {$_ -notin $revisions.name}).Count -gt 0){
            throw 'A tracked revision disappeared during drain; cannot verify its replicas.'
        }
        if($quiet){
            if($null -eq $quietSince){$quietSince=Get-Date}
            # 60-second runtime lease, renewed every 15 seconds: allow expiry after confirmed zero replicas.
            if(((Get-Date)-$quietSince).TotalSeconds -ge 75){$drained=$true;break}
        } else {$quietSince=$null}
        Start-Sleep -Seconds 5
    } while((Get-Date) -lt $deadline)
    if(-not $drained){throw 'Timed out draining all old replicas and waiting 75 seconds for natural lease expiry.'}

    $app=Wait-BrokerControlPlane -RecoveryPreflight
    Assert-KnownRevisions @(Get-BrokerRevisions)
    if($app.properties.latestRevisionName -ne $anchor){throw 'Latest revision changed during handoff.'}
    if(-not (Test-BrokerTraffic $app $anchor)){throw 'Traffic is no longer pinned to the old revision; refusing new creation.'}
    $template=$app.properties.template|ConvertTo-Json -Depth 50|ConvertFrom-Json -AsHashtable
    $template.containers[0].image=$Image
    $template.revisionSuffix=$suffix
    $deployment.status='submitting'
    $deployment.drainCompletedAt=[DateTimeOffset]::UtcNow.ToString('o')
    Save-DemoState $s
    Set-BrokerPatch @{template=$template}
    }
    $deployment.status='submitted'
    $deployment.submittedAt=[DateTimeOffset]::UtcNow.ToString('o')
    Save-DemoState $s
    Write-Output "Submitted $Image as $newRevision; health is NOT yet verified."

    $deadline=(Get-Date).AddSeconds($ReadyTimeoutSeconds)
    $ready=$false
    do {
        $revisions=@(Get-BrokerRevisions)
        Assert-KnownRevisions $revisions $newRevision
        $new=@($revisions|Where-Object name -eq $newRevision)
        if($new.Count -eq 1){
            $revision=$new[0]
            Assert-BrokerTemplate $revision.properties.template
            if($revision.properties.template.containers[0].image -cne $Image){
                throw 'Submitted revision does not contain the requested immutable image.'
            }
            if($revision.properties.provisioningState -eq 'Failed' -or
               $revision.properties.runningState -in @('Failed','ActivationFailed')){
                throw "Revision $newRevision failed to start: $($revision.properties.runningStateDetails)."
            }
            $fqdn=$revision.properties.fqdn
            $expectedFqdn="$newRevision.$(([uri]$url).Host.Substring($appName.Length+1))"
            if($fqdn -and $fqdn -ne $expectedFqdn){throw 'Unexpected revision health endpoint.'}
            # Direct revision requests also wake minReplicas=0 without changing scale or public traffic.
            $healthy=$fqdn -and (Test-BrokerHealth "https://$fqdn")
            if($healthy -and $revision.properties.active -eq $true -and
               $revision.properties.provisioningState -eq 'Provisioned' -and
               $revision.properties.healthState -eq 'Healthy' -and @(Get-BrokerReplicas $newRevision).Count -eq 1){
                $ready=$true;break
            }
        }
        Start-Sleep -Seconds 5
    } while((Get-Date) -lt $deadline)
    if(-not $ready){throw "Timed out verifying health of requested revision $newRevision ($Image). Last health failure: $($healthFailures["https://$fqdn"].detail)"}
    $deployment.status='revisionVerified'
    $deployment.revisionVerifiedAt=[DateTimeOffset]::UtcNow.ToString('o')
    Save-DemoState $s
    $app=Wait-BrokerControlPlane
    Assert-KnownRevisions @(Get-BrokerRevisions) $newRevision
    if(-not $InitialDeployment -and -not (Test-BrokerTraffic $app $anchor)){throw 'Traffic changed before revision verification completed.'}
    Set-BrokerPatch @{configuration=@{ingress=@{traffic=@(@{revisionName=$newRevision;weight=100})}}}

    $deadline=(Get-Date).AddSeconds($ReadyTimeoutSeconds)
    $verified=$false
    do {
        $app=Get-BrokerApp
        Assert-BrokerApp $app
        $revisions=@(Get-BrokerRevisions)
        Assert-KnownRevisions $revisions $newRevision
        $active=@($revisions|Where-Object {$_.properties.active -eq $true})
        if($app.properties.configuration.activeRevisionsMode -ne 'Multiple'){throw 'Revision mode changed during verification.'}
        if($app.properties.provisioningState -eq 'Succeeded' -and
           $app.properties.latestRevisionName -eq $newRevision -and $app.properties.latestReadyRevisionName -eq $newRevision -and
           $active.Count -eq 1 -and $active[0].name -eq $newRevision -and $active[0].properties.healthState -eq 'Healthy' -and
           (Test-BrokerTraffic $app $newRevision) -and
           (Test-BrokerHealth "https://$fqdn") -and (Test-BrokerHealth $url)){
            foreach($name in $oldNames){
                if(@(Get-BrokerReplicas $name).Count -ne 0){throw "Old revision $name has replicas again."}
            }
            $verified=$true;break
        }
        Start-Sleep -Seconds 5
    } while((Get-Date) -lt $deadline)
    if(-not $verified){throw "Timed out verifying public traffic and health for $newRevision ($Image). Last public health failure: $($healthFailures[$url].detail)"}
    $deployment.status='verified'
    $deployment.verifiedAt=[DateTimeOffset]::UtcNow.ToString('o')
    $s.broker.image=$Image
    $s.broker.verified=@{image=$Image;revision=$newRevision;verifiedAt=$deployment.verifiedAt;url=$url}
    Save-DemoState $s
    Write-Output "Verified $Image on ${newRevision}: public /healthz healthy, explicit 100% traffic, Multiple mode, one active revision, min 0 / max 1."
} catch {
    $failure=$_
    if($deployment){
        $deployment.status='failed'
        $deployment.failedAt=[DateTimeOffset]::UtcNow.ToString('o')
        $deployment.error=$failure.Exception.Message
        try {Save-DemoState $s} catch {Write-Warning 'Could not persist deployment failure; do not infer health from prior state.'}
        Write-Warning "Deployment failed; no automatic rollback or Blob lease break was attempted. Inspect revision $($deployment.revision)."
        if($rollbackImage){
            Write-Warning "Operator-only rollback, after stopping other deployments: .\deployment\scripts\Deploy-Broker.ps1 -Apply -Image '$rollbackImage'"
        }
    }
    throw $failure
} finally {
    if($file -and (Test-Path -LiteralPath $file)){Remove-Item -LiteralPath $file -Force}
    if($locked){$mutex.ReleaseMutex()}
    $mutex.Dispose()
}
