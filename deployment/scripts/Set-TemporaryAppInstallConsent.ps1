# Historical consent workflow, not reusable approval. Public defaults and state are isolated examples.
param(
    [switch]$Apply,
    [switch]$Restore,
    [DateTimeOffset]$ApprovedAt = [DateTimeOffset]::MinValue,
    [DateTimeOffset]$RestoreAt = [DateTimeOffset]::MinValue,
    [string]$PreviewPath = ''
)
$ErrorActionPreference = 'Stop'

function Get-InstallConsentSettings {
    @{
        tenantId='f0000000-0000-4000-8000-000000000019'
        appId='f0000000-0000-4000-8000-000000000018'
        clientId='f0000000-0000-4000-8000-000000000034'
        resourceId='f0000000-0000-4000-8000-000000000029'
        adminId='f0000000-0000-4000-8000-000000000031'
        principalIds=@('f0000000-0000-4000-8000-00000000001a','f0000000-0000-4000-8000-000000000005')
        scopes=@('AppCatalog.Read.All','TeamsAppInstallation.ReadWriteForUser')
        previewSha256='d5d266f31866190c7bb501a5bd24bb9194ffeb1d300c4d01b1f8e2398a7f590f'
        approvedAt='2026-10-07T22:53:44.262Z'
        latestRestoreAt='2026-10-08T00:00:00Z'
        recordPath=(Join-Path $env:LOCALAPPDATA 'CrossTeamKnowledgePublicExample\operator-configured\temporary-app-install-consent.json')
        azureConfigDirectory=(Join-Path $env:LOCALAPPDATA 'CrossTeamKnowledgePublicExample\operator-configured\azure-admin-auth')
        eventName='Local\PUBLIC-EXAMPLE-AppInstallConsentRestore'
        mutexName='Local\PUBLIC-EXAMPLE-AppInstallConsentMutation'
    }
}

function Get-InstallConsentNow {
    [DateTimeOffset]::UtcNow
}

function Get-InstallConsentScopes($Scope) {
    if ($Scope -isnot [string] -or
        ($Scope.Length -gt 0 -and $Scope -cnotmatch '^[A-Za-z][A-Za-z0-9_.-]*(?: [A-Za-z][A-Za-z0-9_.-]*)*$')) {
        throw 'Malformed grant scope: expected a space-separated scope string, without control characters.'
    }
    if ($Scope.Length) {$Scope.Split(' ') | Sort-Object -CaseSensitive -Unique}
}

function Get-InstallConsentProjection($Grants) {
    $rows=@(foreach ($grant in @($Grants | Sort-Object id)) {
        [ordered]@{
            id=$grant.id;clientId=$grant.clientId;resourceId=$grant.resourceId
            consentType=$grant.consentType;principalId=$grant.principalId
            scope=(@(Get-InstallConsentScopes $grant.scope) -join ' ')
        }
    })
    ConvertTo-Json -InputObject $rows -Depth 10 -Compress
}

function Invoke-InstallConsentGraph([string]$Token,[string]$Path,[string]$Method='GET',$Body,[string]$ETag) {
    if ($Path -notmatch '^/(oauth2PermissionGrants(?:[/?]|$)|me\?|organization\?|servicePrincipals/)') {
        throw 'Unexpected consent API path.'
    }
    if ($Method -ne 'GET' -and $Path -notmatch '^/oauth2PermissionGrants(?:/[A-Za-z0-9_-]{1,256})?$') {
        throw 'Consent mutations may only target oauth2PermissionGrants.'
    }
    $args=@{
        Uri="https://graph.microsoft.com/v1.0$Path";Method=$Method
        Headers=@{Authorization="Bearer $Token";'client-request-id'=[guid]::NewGuid().ToString()}
        TimeoutSec=60;MaximumRedirection=0;SkipHttpErrorCheck=$true
    }
    if ($ETag) {$args.Headers['If-Match']=$ETag}
    if ($null -ne $Body) {$args.ContentType='application/json';$args.Body=$Body | ConvertTo-Json -Depth 10 -Compress}
    # Never replay a POST after an uncertain response: reconcile its composite identity instead.
    $response=Invoke-WebRequest @args
    if ([int]$response.StatusCode -notin @(200,201,204)) {
        throw "Consent Graph $Method failed HTTP $([int]$response.StatusCode)."
    }
    if ($response.Content) {$response.Content | ConvertFrom-Json -AsHashtable}
}

function Get-InstallConsentAdminToken($Settings) {
    $token=Get-DemoAdminToken
    $me=Invoke-InstallConsentGraph $token '/me?$select=id,userPrincipalName'
    $org=Invoke-InstallConsentGraph $token '/organization?$select=id'
    if ($me.id -ne $Settings.adminId -or $me.userPrincipalName -ne 'admin@example.invalid' -or
        @($org.value).Count -ne 1 -or $org.value[0].id -ne $Settings.tenantId) {
        throw 'Consent administrator or tenant does not match the approved target.'
    }
    return $token
}

function Get-InstallConsentGrants([string]$Token,$Settings) {
    $path="/oauth2PermissionGrants?`$filter=clientId%20eq%20'$($Settings.clientId)'"
    $grants=[Collections.Generic.List[object]]::new()
    $seen=@{}
    for ($page=0; $path -and $page -lt 20; $page++) {
        if ($seen.ContainsKey($path)) {throw 'Repeated grant pagination link.'}
        $seen[$path]=$true
        $response=Invoke-InstallConsentGraph $Token $path
        if ($response.value -isnot [Collections.IList]) {throw 'Grant listing is missing its value array.'}
        foreach ($grant in $response.value) {
            if ($grant.clientId -ne $Settings.clientId -or $grant.id -cnotmatch '^[A-Za-z0-9_-]{1,256}$' -or
                $grant.resourceId -isnot [string] -or -not $grant.resourceId -or
                $grant.consentType -notin @('Principal','AllPrincipals')) {throw 'Unexpected grant listing entry.'}
            $grants.Add($grant)
        }
        $path=$null
        if ($response.'@odata.nextLink') {
            $next=[uri]$response.'@odata.nextLink'
            if ($next.Scheme -ne 'https' -or $next.Host -ne 'graph.microsoft.com' -or
                $next.Port -ne 443 -or $next.AbsolutePath -ne '/v1.0/oauth2PermissionGrants') {throw 'Unexpected grant pagination origin/path.'}
            $path=$next.PathAndQuery.Substring('/v1.0'.Length)
        }
    }
    if ($path) {throw 'Grant pagination exceeded the bounded page limit.'}
    if (@($grants | Group-Object id | Where-Object Count -gt 1).Count) {throw 'Duplicate grant IDs in listing.'}
    return $grants.ToArray()
}

function Get-InstallConsentTarget($Grants,$Settings,[string]$PrincipalId) {
    if ($PrincipalId -notin $Settings.principalIds) {throw 'Unapproved consent principal.'}
    $matches=@($Grants | Where-Object {
        $_.clientId -eq $Settings.clientId -and $_.resourceId -eq $Settings.resourceId -and
        $_.consentType -ceq 'Principal' -and $_.principalId -eq $PrincipalId
    })
    if ($matches.Count -gt 1) {throw "Duplicate Principal grants for $PrincipalId; operator reconciliation required."}
    if ($matches.Count) {
        $null=@(Get-InstallConsentScopes $matches[0].scope)
        return $matches[0]
    }
}

function Get-InstallConsentUnrelated($Grants,$Settings) {
    Get-InstallConsentProjection @($Grants | Where-Object {
        $_.resourceId -ne $Settings.resourceId -or $_.consentType -cne 'Principal' -or
        $_.principalId -notin $Settings.principalIds
    })
}

function Save-InstallConsentRecord($Record,$Settings) {
    $temp="$($Settings.recordPath).tmp"
    $Record | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $temp -Encoding utf8
    Move-Item -LiteralPath $temp -Destination $Settings.recordPath -Force
}

function Add-InstallConsentError($Record,[string]$Phase,[string]$PrincipalId,[string]$Message) {
    $errorEntry=@{at=[DateTimeOffset]::UtcNow.ToString('o');phase=$Phase;principalId=$PrincipalId;message=$Message}
    $Record.errors=@($Record.errors | Where-Object {$null -ne $_})+@($errorEntry)
    Write-Warning "$Phase [$PrincipalId]: $Message" -WarningAction Continue
}

function Save-InstallConsentProgress($Record,$Settings) {
    try {Save-InstallConsentRecord $Record $Settings;return $true}
    catch {
        Add-InstallConsentError $Record 'LEDGER_WRITE_FAILED' '' $_.Exception.Message
        return $false
    }
}

function Assert-InstallConsentApproval($Settings,[string]$Path,[DateTimeOffset]$Approval,[DateTimeOffset]$Deadline) {
    $now=Get-InstallConsentNow
    if ($Approval -ne [DateTimeOffset]::Parse($Settings.approvedAt) -or $Approval -gt $now -or
        $Deadline -le $now.AddMinutes(2) -or $Deadline -gt $now.AddMinutes(90) -or
        $Deadline -gt [DateTimeOffset]::Parse($Settings.latestRestoreAt)) {
        throw 'Use the exact approval time and a future deadline within 90 minutes, no later than 2026-10-08 09:00 KST.'
    }
    if ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ne $Settings.previewSha256) {
        throw 'Preview SHA256 differs from the specifically approved preview.'
    }
}

function Assert-InstallConsentRecord($Record,$Settings) {
    if ($Record -isnot [Collections.IDictionary] -or $Record.version -ne 1 -or
        $Record.tenantId -ne $Settings.tenantId -or $Record.appId -ne $Settings.appId -or
        $Record.clientId -ne $Settings.clientId -or $Record.resourceId -ne $Settings.resourceId -or
        $Record.previewSha256 -ne $Settings.previewSha256 -or
        $Record.azureConfigDirectory -ne $Settings.azureConfigDirectory -or
        [DateTimeOffset]::Parse($Record.approvedAt) -ne [DateTimeOffset]::Parse($Settings.approvedAt) -or
        [DateTimeOffset]::Parse($Record.restoreBy) -gt [DateTimeOffset]::Parse($Settings.latestRestoreAt) -or
        [DateTimeOffset]::Parse($Record.restoreBy) -le [DateTimeOffset]::Parse($Record.approvedAt) -or
        $Record.principals -isnot [Collections.IDictionary] -or $Record.principals.Count -ne 2 -or
        $Record.unrelatedBefore -isnot [string]) {throw 'Invalid consent restoration record; refusing mutation.'}
    foreach ($id in $Settings.principalIds) {
        $entry=$Record.principals[$id]
        if ($entry -isnot [Collections.IDictionary] -or $entry.principalId -ne $id -or
            $entry.consentType -cne 'Principal' -or $entry.ownedNewGrant -isnot [bool] -or
            $entry.mutationAttempted -isnot [bool] -or $entry.addedScopes -isnot [Collections.IList] -or
            $entry.beforeScopes -isnot [Collections.IList]) {throw 'Invalid consent principal ownership record.'}
        $before=@(Get-InstallConsentScopes ($entry.beforeScopes -join ' '))
        $added=@(Get-InstallConsentScopes ($entry.addedScopes -join ' '))
        if ($before.Count -ne $entry.beforeScopes.Count -or $added.Count -ne $entry.addedScopes.Count -or
            @($entry.beforeScopes+$entry.addedScopes | Where-Object {$_ -isnot [string] -or $_ -match ' '}).Count -or
            @($added | Where-Object {$_ -cnotin $Settings.scopes -or $_ -cin $before}).Count -or
            ($entry.ownedNewGrant -and ($before.Count -or $entry.beforeGrantId)) -or
            (-not $entry.ownedNewGrant -and $entry.beforeGrantId -cnotmatch '^[A-Za-z0-9_-]{1,256}$') -or
            ($entry.grantId -and $entry.grantId -cnotmatch '^[A-Za-z0-9_-]{1,256}$')) {
            throw 'Invalid introduced scopes or grant ownership; refusing mutation.'
        }
        $expectedAdded=@($Settings.scopes | Where-Object {$_ -cnotin $before} | Sort-Object -CaseSensitive)
        if (($added -join ' ') -cne ($expectedAdded -join ' ') -or
            (-not $entry.ownedNewGrant -and $entry.grantId -ne $entry.beforeGrantId)) {
            throw 'Introduced-scope set or original grant identity does not match the ownership baseline.'
        }
    }
    # A syntactically valid projection prevents a forged/malformed baseline from becoming a success.
    $baseline=ConvertFrom-Json -InputObject $Record.unrelatedBefore -AsHashtable -NoEnumerate
    if ($baseline -isnot [Collections.IList] -or
        (Get-InstallConsentProjection $baseline) -cne $Record.unrelatedBefore -or
        (Get-InstallConsentUnrelated $baseline $Settings) -cne $Record.unrelatedBefore) {
        throw 'Invalid unrelated-grant baseline.'
    }
}

function New-InstallConsentRecord([string]$Token,$Settings,[DateTimeOffset]$Deadline) {
    $client=Invoke-InstallConsentGraph $Token "/servicePrincipals/$($Settings.clientId)?`$select=id,appId"
    $graph=Invoke-InstallConsentGraph $Token "/servicePrincipals/$($Settings.resourceId)?`$select=id,appId"
    if ($client.id -ne $Settings.clientId -or $client.appId -ne $Settings.appId -or
        $graph.id -ne $Settings.resourceId -or $graph.appId -ne '00000003-0000-0000-c000-000000000000') {
        throw 'Reader or Graph service principal does not match the approved identity.'
    }
    $grants=@(Get-InstallConsentGrants $Token $Settings)
    $record=@{
        version=1;tenantId=$Settings.tenantId;appId=$Settings.appId;clientId=$Settings.clientId;resourceId=$Settings.resourceId
        previewSha256=$Settings.previewSha256;approvedAt=$Settings.approvedAt;restoreBy=$Deadline.ToUniversalTime().ToString('o')
        azureConfigDirectory=$Settings.azureConfigDirectory;startedAt=[DateTimeOffset]::UtcNow.ToString('o')
        status='PREFLIGHT';principals=@{};errors=@();unrelatedBefore=(Get-InstallConsentUnrelated $grants $Settings)
    }
    foreach ($id in $Settings.principalIds) {
        $current=Get-InstallConsentTarget $grants $Settings $id
        $before=@(if ($current) {Get-InstallConsentScopes $current.scope})
        $record.principals[$id]=@{
            principalId=$id;consentType='Principal';ownedNewGrant=($null -eq $current)
            beforeGrantId=$current.id;grantId=$current.id;beforeScopes=@($before)
            addedScopes=@($Settings.scopes | Where-Object {$_ -cnotin $before});mutationAttempted=$false
        }
    }
    Assert-InstallConsentRecord $record $Settings
    return $record
}

function Apply-InstallConsentPrincipal($Record,$Settings,[string]$Token,[string]$PrincipalId) {
    $entry=$Record.principals[$PrincipalId]
    $grants=@(Get-InstallConsentGrants $Token $Settings)
    if ((Get-InstallConsentUnrelated $grants $Settings) -cne $Record.unrelatedBefore) {throw 'Unrelated grants changed during preflight.'}
    $current=Get-InstallConsentTarget $grants $Settings $PrincipalId
    $currentScopes=@(if ($current) {Get-InstallConsentScopes $current.scope})
    if ($current.id -ne $entry.beforeGrantId -or
        (($currentScopes | Sort-Object -CaseSensitive) -join ' ') -cne (($entry.beforeScopes | Sort-Object -CaseSensitive) -join ' ')) {
        throw "Principal grant changed during preflight: $PrincipalId."
    }
    if (-not $entry.addedScopes.Count) {$entry.applyStatus='ALREADY_PRESENT';return}
    $entry.mutationAttempted=$true
    $Record.status='APPLYING'
    try {Save-InstallConsentRecord $Record $Settings}
    catch {$entry.mutationAttempted=$false;throw}
    $scope=(@($currentScopes+$entry.addedScopes | Sort-Object -CaseSensitive -Unique) -join ' ')
    if ((Get-InstallConsentNow) -ge [DateTimeOffset]::Parse($Record.restoreBy)) {
        throw 'Consent deadline reached during preflight; no new mutation may start.'
    }
    try {
        if ($entry.ownedNewGrant) {
            Invoke-InstallConsentGraph $Token '/oauth2PermissionGrants' POST @{
                clientId=$Settings.clientId;resourceId=$Settings.resourceId;consentType='Principal';principalId=$PrincipalId;scope=$scope
            } | Out-Null
        } else {
            Invoke-InstallConsentGraph $Token "/oauth2PermissionGrants/$($current.id)" PATCH @{scope=$scope} $current.'@odata.etag' | Out-Null
        }
    } catch {
        Add-InstallConsentError $Record 'APPLY_RESPONSE_UNCERTAIN' $PrincipalId $_.Exception.Message
        Save-InstallConsentRecord $Record $Settings
    }
    $actual=Get-InstallConsentTarget @(Get-InstallConsentGrants $Token $Settings) $Settings $PrincipalId
    if (-not $actual -or @($scope.Split(' ') | Where-Object {$_ -cnotin @(Get-InstallConsentScopes $actual.scope)}).Count -or
        (-not $entry.ownedNewGrant -and $actual.id -ne $entry.beforeGrantId)) {
        throw "Consent application not verified for $PrincipalId; restoration will reconcile by composite identity."
    }
    $entry.grantId=$actual.id
    $entry.applyStatus='VERIFIED'
    $entry.appliedAt=[DateTimeOffset]::UtcNow.ToString('o')
    Save-InstallConsentRecord $Record $Settings
}

function Restore-InstallConsentPrincipal($Entry,$Settings,[string]$Token) {
    $current=Get-InstallConsentTarget @(Get-InstallConsentGrants $Token $Settings) $Settings $Entry.principalId
    if (-not $current) {return 'ABSENT'}
    $expectedId=if ($Entry.grantId) {$Entry.grantId} else {$Entry.beforeGrantId}
    if ($expectedId -and $current.id -ne $expectedId) {throw 'Target grant identity changed; refusing to mutate a replacement grant.'}
    $scopes=@(Get-InstallConsentScopes $current.scope)
    $remaining=@($scopes | Where-Object {$_ -cnotin $Entry.addedScopes})
    if ($remaining.Count -ne $scopes.Count) {
        if (-not $remaining.Count -and $Entry.ownedNewGrant) {
            # Re-read immediately before deleting: an unrelated concurrent scope turns this into a PATCH.
            $latest=Get-InstallConsentTarget @(Get-InstallConsentGrants $Token $Settings) $Settings $Entry.principalId
            if (-not $latest) {return 'ABSENT'}
            if ($latest.id -ne $current.id) {throw 'Grant changed before deletion.'}
            $current=$latest
            $remaining=@(Get-InstallConsentScopes $current.scope | Where-Object {$_ -cnotin $Entry.addedScopes})
        }
        if (-not $remaining.Count -and $Entry.ownedNewGrant) {
            Invoke-InstallConsentGraph $Token "/oauth2PermissionGrants/$($current.id)" DELETE $null $current.'@odata.etag' | Out-Null
        } else {
            Invoke-InstallConsentGraph $Token "/oauth2PermissionGrants/$($current.id)" PATCH @{scope=($remaining -join ' ')} $current.'@odata.etag' | Out-Null
        }
    }
    $actual=Get-InstallConsentTarget @(Get-InstallConsentGrants $Token $Settings) $Settings $Entry.principalId
    $actualScopes=@(if ($actual) {Get-InstallConsentScopes $actual.scope})
    if (($actual -and $actual.id -ne $current.id) -or
        @($actualScopes | Where-Object {$_ -cin $Entry.addedScopes}).Count -or
        @($remaining | Where-Object {$_ -cnotin $actualScopes}).Count) {
        throw 'Consent restoration readback mismatch; introduced scopes remain or unrelated scopes disappeared.'
    }
    return $(if ($actual) {'INTRODUCED_SCOPES_REMOVED'} else {'OWNED_GRANT_ABSENT'})
}

function Restore-InstallConsentRecord($Record,$Settings) {
    Assert-InstallConsentRecord $Record $Settings
    $pending=@($Settings.principalIds | Where-Object {$Record.principals[$_].mutationAttempted})
    $delays=@(5,15,30,60,120)
    $Record.status='RESTORING';$Record.Remove('restoredAt')
    $Record.pendingPrincipalIds=$pending
    $persistenceFailed=-not (Save-InstallConsentProgress $Record $Settings)
    $token=$null
    try {
        for ($round=0; $pending.Count -and $round -le $delays.Count; $round++) {
            foreach ($id in @($pending)) {
                $entry=$Record.principals[$id]
                $entry.restoreAttempts=[int]$entry.restoreAttempts+1
                $entry.Remove('restoredAt')
                $entry.Remove('restoreObservation')
                try {
                    $token=Get-InstallConsentAdminToken $Settings
                    $entry.restoreObservation=Restore-InstallConsentPrincipal $entry $Settings $token
                    $entry.restoredAt=[DateTimeOffset]::UtcNow.ToString('o')
                    $entry.restoreStatus='RESTORED'
                    $pending=@($pending | Where-Object {$_ -ne $id})
                } catch {
                    $entry.restoreStatus='RESTORE_FAILED'
                    Add-InstallConsentError $Record 'RESTORE_FAILED' $id $_.Exception.Message
                } finally {$token=$null}
                $Record.pendingPrincipalIds=$pending
                if (-not (Save-InstallConsentProgress $Record $Settings)) {$persistenceFailed=$true}
            }
            if ($pending.Count -and $round -lt $delays.Count) {Start-Sleep -Seconds $delays[$round]}
        }
        $Record.unrelatedVerified=$false
        try {
            $token=Get-InstallConsentAdminToken $Settings
            $Record.unrelatedAfter=Get-InstallConsentUnrelated @(Get-InstallConsentGrants $token $Settings) $Settings
            if ($Record.unrelatedAfter -cne $Record.unrelatedBefore) {throw 'Unrelated grant projection changed; no unrelated grants were reverted.'}
            $Record.unrelatedVerified=$true
        } catch {Add-InstallConsentError $Record 'UNRELATED_VERIFICATION_FAILED' '' $_.Exception.Message}
        $Record.status=if ($pending.Count -or -not $Record.unrelatedVerified) {'RESTORE_FAILED'} elseif ($persistenceFailed) {'RESTORE_LEDGER_FAILED'} else {'RESTORED'}
        $Record.restoreFinishedAt=[DateTimeOffset]::UtcNow.ToString('o')
        if ($Record.status -eq 'RESTORED') {$Record.restoredAt=$Record.restoreFinishedAt}
        if (-not (Save-InstallConsentProgress $Record $Settings)) {$persistenceFailed=$true}
        if ($Record.status -ne 'RESTORED' -or $persistenceFailed) {
            throw "CRITICAL: consent cleanup not fully verified. Pending principals: $($pending -join ','); unrelated verified: $($Record.unrelatedVerified); ledger failed: $persistenceFailed. Inspect $($Settings.recordPath)."
        }
        Write-Output 'Introduced installation scopes removed; unrelated grants unchanged on readback. Previously issued tokens are not revoked.'
    } finally {$token=$null}
}

function Request-InstallConsentRestore($Settings) {
    $event=$null
    try {$event=[Threading.EventWaitHandle]::OpenExisting($Settings.eventName)}
    catch [Threading.WaitHandleCannotBeOpenedException] {return $false}
    try {$event.Set() | Out-Null;return $true} finally {$event.Dispose()}
}

if ($Apply -eq $Restore) {throw 'Choose exactly one of -Apply or -Restore.'}
$settings=Get-InstallConsentSettings
if ($Apply) {Assert-InstallConsentApproval $settings $PreviewPath $ApprovedAt $RestoreAt}
$env:AZURE_CONFIG_DIR=$settings.azureConfigDirectory
Import-Module (Join-Path $PSScriptRoot 'Demo.psm1') -Force
if ($Restore) {
    $saved=Get-Content -Raw -LiteralPath $settings.recordPath | ConvertFrom-Json -AsHashtable -DateKind String
    Assert-InstallConsentRecord $saved $settings
    if (Request-InstallConsentRestore $settings) {
        Write-Output 'Consent restoration requested from its watchdog; this is NOT restoration verification.'
        return
    }
}
$mutex=[Threading.Mutex]::new($false,$settings.mutexName)
$locked=$false;$event=$null;$record=$null;$token=$null
try {
    try {$locked=$mutex.WaitOne(0)} catch [Threading.AbandonedMutexException] {$locked=$true}
    if (-not $locked) {throw 'Another consent operation is running. Retry -Restore if its watchdog is still starting.'}
    if ($Restore) {
        $saved=Get-Content -Raw -LiteralPath $settings.recordPath | ConvertFrom-Json -AsHashtable -DateKind String
        Restore-InstallConsentRecord $saved $settings
        return
    }
    if (Test-Path -LiteralPath $settings.recordPath) {throw 'This bounded consent approval already has a ledger. Use -Restore; a second Apply requires separate approval.'}
    $created=$false
    $event=[Threading.EventWaitHandle]::new($false,[Threading.EventResetMode]::ManualReset,$settings.eventName,[ref]$created)
    if (-not $created) {throw 'A consent watchdog event already exists; refusing another Apply.'}
    $token=Get-InstallConsentAdminToken $settings
    $record=New-InstallConsentRecord $token $settings $RestoreAt
    Save-InstallConsentRecord $record $settings
    foreach ($id in $settings.principalIds) {
        if ($event.WaitOne(0) -or [DateTimeOffset]::UtcNow -ge $RestoreAt) {throw 'Restoration requested or deadline reached before consent application completed.'}
        Apply-InstallConsentPrincipal $record $settings $token $id
    }
    if ((Get-InstallConsentUnrelated @(Get-InstallConsentGrants $token $settings) $settings) -cne $record.unrelatedBefore) {throw 'Unrelated grants changed during application.'}
    $record.status='ACTIVE; RESTORATION ARMED'
    Save-InstallConsentRecord $record $settings
    $token=$null
    Write-Output "Only the two approved Principal grants received introduced installation scopes. Restore by $($record.restoreBy)."
    Write-Output 'No global/admin grant, manifest, installation, data ACL, SSO binding or MFA policy is changed. Avoid concurrent consent writers.'
    $remaining=[Math]::Max(0,($RestoreAt-[DateTimeOffset]::UtcNow).TotalMilliseconds)
    $record.restoreTriggeredEarly=$event.WaitOne([int]$remaining)
} catch {
    if ($record) {
        Add-InstallConsentError $record 'APPLY_FAILED' '' $_.Exception.Message
        $record.status='APPLY_FAILED'
        $null=Save-InstallConsentProgress $record $settings
    }
    throw
} finally {
    try {
        if ($record -and ($record.status -eq 'ACTIVE; RESTORATION ARMED' -or
            @($record.principals.Values | Where-Object mutationAttempted).Count)) {
            Restore-InstallConsentRecord $record $settings
        }
    } finally {
        $token=$null
        if ($event) {$event.Dispose()}
        if ($locked) {$mutex.ReleaseMutex()}
        $mutex.Dispose()
    }
}
