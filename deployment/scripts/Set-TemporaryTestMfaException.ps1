# Historical example, not a recommendation to exclude users from MFA. Public defaults are inert.
param(
    [switch]$Apply,
    [switch]$Restore,
    [DateTimeOffset]$RestoreAt = [DateTimeOffset]::UtcNow.AddHours(1),
    [DateTimeOffset]$ApprovedAt = [DateTimeOffset]::MinValue,
    [string]$AzureConfigDirectory
)
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'Demo.psm1') -Force
$state = Get-DemoState
$policyIds = @('f0000000-0000-4000-8000-00000000001c','f0000000-0000-4000-8000-00000000003e')
$userIds = @('f0000000-0000-4000-8000-00000000001a','f0000000-0000-4000-8000-000000000005')
$eventName = 'Local\PUBLIC-EXAMPLE-TestMfaRestore'
$recordPath = Join-Path $env:LOCALAPPDATA 'CrossTeamKnowledgePublicExample\operator-configured\temporary-mfa-exception.json'
if ($Restore -and -not $AzureConfigDirectory -and (Test-Path -LiteralPath $recordPath)) {
    $saved = Get-Content -Raw -LiteralPath $recordPath | ConvertFrom-Json -AsHashtable
    $AzureConfigDirectory = $saved.azureConfigDirectory
}
if ($AzureConfigDirectory) {
    $expectedDirectory = Join-Path $env:LOCALAPPDATA 'CrossTeamKnowledgePublicExample\operator-configured\azure-admin-auth'
    if ([IO.Path]::GetFullPath($AzureConfigDirectory) -ne [IO.Path]::GetFullPath($expectedDirectory)) {
        throw 'Only the isolated demo administrator Azure CLI profile is allowed.'
    }
    $env:AZURE_CONFIG_DIR = $expectedDirectory
}
if (-not $Apply -and -not $Restore) {throw 'Explicit -Apply or -Restore is required.'}
if ($Apply -and $Restore) {throw 'Choose one operation.'}
if ($Apply) {
    $now = [DateTimeOffset]::UtcNow
    if ($RestoreAt -le $now.AddMinutes(2) -or $RestoreAt -gt $now.AddHours(48)) {
        throw 'The approved restoration deadline must be between two minutes and 48 hours from now.'
    }
    if ($ApprovedAt -eq [DateTimeOffset]::MinValue -or $ApprovedAt -gt $now.AddMinutes(5)) {
        throw 'Supply the actual user approval time with -ApprovedAt.'
    }
}
if ($state.selectedTestUsers.reader.id -ne $userIds[0] -or $state.selectedTestUsers.outsider.id -ne $userIds[1]) {
    throw 'The selected accounts do not match the two users approved for this temporary exception.'
}

function Canonical-Value($Value) {
    if ($Value -is [System.Collections.IDictionary]) {
        $normalized = [ordered]@{}
        foreach ($key in @($Value.Keys | Sort-Object)) {$normalized[$key] = Canonical-Value $Value[$key]}
        return $normalized
    }
    if ($Value -is [System.Collections.IEnumerable] -and $Value -isnot [string]) {
        $items = [Collections.Generic.List[object]]::new()
        foreach ($item in $Value) {$items.Add((Canonical-Value $item))}
        return ,$items.ToArray()
    }
    return $Value
}
function Canonical-Json($Value) {
    ConvertTo-Json -InputObject (Canonical-Value $Value) -Depth 40 -Compress
}
function Policy-Core($Policy) {
    @{
        displayName=$Policy.displayName
        state=$Policy.state
        conditions=$Policy.conditions
        grantControls=$Policy.grantControls
        sessionControls=$Policy.sessionControls
    }
}
function Save-Record($Record) {
    $temp = "$recordPath.tmp"
    $Record | ConvertTo-Json -Depth 40 | Set-Content -LiteralPath $temp -Encoding utf8
    Move-Item -LiteralPath $temp -Destination $recordPath -Force
}
function Save-RestoreProgress($Record) {
    try {Save-Record $Record; return $true}
    catch {
        $failure = @{at=[DateTimeOffset]::UtcNow.ToString('o');message=$_.Exception.Message}
        $Record.restorePersistenceErrors = @($Record.restorePersistenceErrors | Where-Object {$null -ne $_}) + @($failure)
        Write-Warning "CRITICAL: cannot persist restoration progress to ${recordPath}: $($failure.message)" -WarningAction Continue
        return $false
    }
}
function Restore-Record($Record) {
    if ($Record.tenantId -ne $state.tenantId -or
        $Record.policies -isnot [System.Collections.IDictionary] -or
        (Canonical-Json @($Record.userIds | Sort-Object)) -cne (Canonical-Json @($userIds | Sort-Object)) -or
        @($Record.policies.Keys | Where-Object {$_ -notin $policyIds}).Count) {
        throw 'Invalid restoration record; refusing policy changes.'
    }
    $pending = @($policyIds | Where-Object {$Record.policies[$_].mutationAttempted})
    $fatalPolicies = @{}
    $retryDelays = @(5,15,30,60,120)
    $Record.status = 'RESTORING'
    $Record.Remove('restoredAt')
    $Record.restoreStartedAt = [DateTimeOffset]::UtcNow.ToString('o')
    $Record.pendingPolicyIds = $pending
    $persistenceFailed = -not (Save-RestoreProgress $Record)
    $token = $null
    try {
        for ($attempt=0; $attempt -le $retryDelays.Count -and $pending.Count; $attempt++) {
            # Refresh credentials each round; one failed policy must never prevent the other cleanup.
            $token = $null
            foreach ($id in @($pending | Where-Object {-not $fatalPolicies.ContainsKey($_)})) {
                $entry = $Record.policies[$id]
                $entry.restoreAttempts = [int]$entry.restoreAttempts + 1
                $entry.Remove('restoredAt')
                $entry.restoreStatus = 'RESTORING'
                try {
                    if ($entry.before.conditions.users.excludeUsers -isnot [System.Collections.IList] -or
                        $entry.addedUsers -isnot [System.Collections.IList] -or
                        @($entry.addedUsers | Where-Object {$_ -notin $userIds -or $_ -in $entry.before.conditions.users.excludeUsers}).Count) {
                        $fatalPolicies[$id] = $true
                        throw "Invalid introduced-user ledger for policy $id; refusing to remove unowned exclusions."
                    }
                    if (-not $token) {$token = Get-DemoAdminToken}
                    $current = Invoke-DemoGraph $token "/identity/conditionalAccess/policies/$id"
                    if ($current.conditions.users -isnot [System.Collections.IDictionary] -or
                        $current.conditions.users.excludeUsers -isnot [System.Collections.IList]) {
                        throw "Missing exclusion list in policy $id readback."
                    }
                    $desired = @($current.conditions.users.excludeUsers | Where-Object {$_ -notin $entry.addedUsers})
                    if (@($current.conditions.users.excludeUsers | Where-Object {$_ -in $entry.addedUsers}).Count) {
                        # Patch only this collection, never replay stale conditions or unrelated policy fields.
                        Invoke-DemoGraph $token "/identity/conditionalAccess/policies/$id" PATCH @{conditions=@{users=@{excludeUsers=$desired}}} | Out-Null
                    }
                    $actual = Invoke-DemoGraph $token "/identity/conditionalAccess/policies/$id"
                    if ($actual.conditions.users.excludeUsers -isnot [System.Collections.IList] -or
                        @($actual.conditions.users.excludeUsers | Where-Object {$_ -in $entry.addedUsers}).Count) {
                        throw "Restoration not verified for policy $id; introduced exclusions may remain."
                    }
                    $entry.restoredAt = [DateTimeOffset]::UtcNow.ToString('o')
                    $entry.restoredExclusions = @($actual.conditions.users.excludeUsers)
                    $entry.restoreStatus = 'RESTORED'
                    $entry.lastRestoreError = $null
                    $pending = @($pending | Where-Object {$_ -ne $id})
                } catch {
                    $token = $null
                    $failure = @{
                        at=[DateTimeOffset]::UtcNow.ToString('o');policyId=$id
                        attempt=$entry.restoreAttempts;message=$_.Exception.Message
                        fatal=$fatalPolicies.ContainsKey($id)
                    }
                    $entry.restoreStatus = 'RESTORE_FAILED'
                    $entry.lastRestoreError = $failure
                    $Record.restoreErrors = @($Record.restoreErrors | Where-Object {$null -ne $_}) + @($failure)
                    Write-Warning "Restoration attempt $($entry.restoreAttempts) failed for ${id}: $($failure.message)" -WarningAction Continue
                }
                $Record.pendingPolicyIds = $pending
                if (-not (Save-RestoreProgress $Record)) {$persistenceFailed=$true}
            }
            if (-not @($pending | Where-Object {-not $fatalPolicies.ContainsKey($_)}).Count) {break}
            if ($attempt -lt $retryDelays.Count) {
                $Record.status = 'RESTORE_RETRYING'
                $Record.nextRetryAt = [DateTimeOffset]::UtcNow.AddSeconds($retryDelays[$attempt]).ToString('o')
                if (-not (Save-RestoreProgress $Record)) {$persistenceFailed=$true}
                Start-Sleep -Seconds $retryDelays[$attempt]
            }
        }
        $Record.Remove('nextRetryAt')
        $Record.restoreFinishedAt = [DateTimeOffset]::UtcNow.ToString('o')
        $Record.status = if ($pending.Count) {'RESTORE_FAILED'} elseif ($persistenceFailed) {'RESTORE_LEDGER_FAILED'} else {'RESTORED'}
        if (-not $pending.Count) {$Record.restoredAt=$Record.restoreFinishedAt}
        if (-not (Save-RestoreProgress $Record)) {$persistenceFailed=$true}
        if ($pending.Count -or $persistenceFailed) {
            throw "CRITICAL: restoration requires operator attention. Unverified policies: $($pending -join ', '); ledger write failure: $persistenceFailed. Inspect $recordPath and restore/verify outstanding exclusions; no success is claimed."
        }
        Write-Output 'Temporary test-user MFA exclusions removed; policy readback confirmed.'
    } finally {$token=$null}
}

if ($Restore) {
    $event = $null
    try {$event=[Threading.EventWaitHandle]::OpenExisting($eventName)}
    catch [Threading.WaitHandleCannotBeOpenedException] {
        if (-not (Test-Path -LiteralPath $recordPath)) {throw 'No restoration record exists.'}
        Restore-Record (Get-Content -Raw -LiteralPath $recordPath | ConvertFrom-Json -AsHashtable)
        return
    }
    try {$event.Set() | Out-Null} finally {$event.Dispose()}
    Write-Output 'Immediate restoration requested from the active watchdog.'
    return
}

$created = $false
$event = [Threading.EventWaitHandle]::new($false,[Threading.EventResetMode]::ManualReset,$eventName,[ref]$created)
if (-not $created) {$event.Dispose();throw 'A temporary MFA exception run is already active.'}
$record = $null
$token = $null
try {
    if (Test-Path -LiteralPath $recordPath) {
        $previous = Get-Content -Raw -LiteralPath $recordPath | ConvertFrom-Json -AsHashtable
        if ($previous.status -ne 'RESTORED') {throw 'A prior exception is not restored; use -Restore before a new run.'}
        $archive = "$recordPath.$([DateTimeOffset]::UtcNow.ToString('yyyyMMddHHmmss')).previous"
        Copy-Item -LiteralPath $recordPath -Destination $archive
    }
    $token = Get-DemoAdminToken
    $record = @{
        tenantId=$state.tenantId
        userIds=$userIds
        approvedAt=$ApprovedAt.ToUniversalTime().ToString('o')
        startedAt=[DateTimeOffset]::UtcNow.ToString('o')
        restoreBy=$RestoreAt.ToUniversalTime().ToString('o')
        status='PREFLIGHT'
        azureConfigDirectory=$AzureConfigDirectory
        policies=@{}
        riskBasedPoliciesChanged=$false
        securityDefaultsChanged=$false
        perUserMfaChanged=$false
    }
    foreach ($id in $policyIds) {
        $policy = Invoke-DemoGraph $token "/identity/conditionalAccess/policies/$id"
        if ($policy.state -ne 'enabled' -or
            $policy.displayName -ne 'Multifactor authentication for Microsoft partners and vendors' -or
            (Canonical-Json @($policy.grantControls.builtInControls)) -cne '["mfa"]' -or
            $policy.conditions.userRiskLevels.Count -ne 0 -or $policy.conditions.signInRiskLevels.Count -ne 0 -or
            $policy.conditions.users.includeUsers -notcontains 'All') {
            throw "Policy $id does not match the approved always-on MFA policy."
        }
        $record.policies[$id] = @{
            before=$policy
            addedUsers=@($userIds | Where-Object {$_ -notin $policy.conditions.users.excludeUsers})
            mutationAttempted=$false
        }
    }
    Save-Record $record
    foreach ($id in $policyIds) {
        $entry = $record.policies[$id]
        $current = Invoke-DemoGraph $token "/identity/conditionalAccess/policies/$id"
        if ((Canonical-Json (Policy-Core $current)) -cne (Canonical-Json (Policy-Core $entry.before))) {
            throw "Policy $id changed during preflight; aborting."
        }
        $desired = $current.conditions | ConvertTo-Json -Depth 30 | ConvertFrom-Json -AsHashtable
        $desired.users.excludeUsers = @(@($current.conditions.users.excludeUsers) + $userIds | Sort-Object -Unique)
        $expected = Policy-Core $current
        $expected.conditions = $desired
        $entry.mutationAttempted = $true
        $record.status = 'APPLYING'
        Save-Record $record
        Invoke-DemoGraph $token "/identity/conditionalAccess/policies/$id" PATCH @{conditions=$desired} | Out-Null
        $actual = Invoke-DemoGraph $token "/identity/conditionalAccess/policies/$id"
        if ((Canonical-Json (Policy-Core $actual)) -cne (Canonical-Json $expected)) {
            throw "Policy $id readback mismatch; restoring introduced exclusions."
        }
        $entry.appliedAt = [DateTimeOffset]::UtcNow.ToString('o')
        $entry.appliedExclusions = @($actual.conditions.users.excludeUsers)
        Save-Record $record
    }
    $record.status = 'ACTIVE; RESTORATION ARMED'
    Save-Record $record
    Write-Output "Only Reader and Outsider excluded from the two always-on MFA policies. Restore by $($record.restoreBy)."
    Write-Output 'Risk-based policies, administrator, other users, Security Defaults and per-user MFA remain unchanged.'
    $remaining = [Math]::Max(0,([DateTimeOffset]::Parse($record.restoreBy)-[DateTimeOffset]::UtcNow).TotalMilliseconds)
    $record.restoreTriggeredEarly = $event.WaitOne([int]$remaining)
} finally {
    try {
        if ($record) {Restore-Record $record}
    } finally {
        $token=$null
        $event.Dispose()
    }
}
