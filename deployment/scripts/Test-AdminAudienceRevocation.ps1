param(
    [switch]$Apply,
    [switch]$Restore,
    [string]$EvidencePath
)
$ErrorActionPreference = 'Stop'
$eventName = 'Local\PUBLIC-EXAMPLE-AdminAudienceRestore'
if ($Restore) {
    $event = [Threading.EventWaitHandle]::OpenExisting($eventName)
    try {$event.Set() | Out-Null} finally {$event.Dispose()}
    Write-Output 'Requested immediate restoration by the running test.'
    return
}
if (-not $Apply -or -not $EvidencePath) {throw 'Use -Apply and an evidence path, or -Restore for a running test.'}
Import-Module (Join-Path $PSScriptRoot 'Demo.psm1') -Force
$state = Get-DemoState
$groupId = 'f0000000-0000-4000-8000-000000000016'
$adminId = 'f0000000-0000-4000-8000-000000000031'
if ($state.groups.Readers.id -ne $groupId) {throw 'Unexpected synthetic audience group.'}
$created = $false
$event = [Threading.EventWaitHandle]::new($false,[Threading.EventResetMode]::ManualReset,$eventName,[ref]$created)
if (-not $created) {$event.Dispose();throw 'A revocation test is already running.'}
$result = [ordered]@{
    scope='Temporary removal/restoration of the demo administrator only; not an independent outsider identity.'
    groupId=$groupId
    userId=$adminId
    restoreTimeoutSeconds=300
    status='PREFLIGHT'
}
$attemptedRemoval = $false
$token = $null
function Save-Evidence {
    $directory = Split-Path -Parent $EvidencePath
    if ($directory) {New-Item -ItemType Directory -Path $directory -Force | Out-Null}
    $result | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $EvidencePath -Encoding utf8
}
try {
    $token = Get-DemoAdminToken
    $group = Invoke-DemoGraph $token "/groups/$groupId`?`$select=id,displayName"
    if ($group.displayName -ne 'Example-Readers') {throw 'Unexpected group identity.'}
    $members = (Invoke-DemoGraph $token "/groups/$groupId/members?`$select=id").value.id
    if ($members -notcontains $adminId) {throw 'Admin was not originally a group member; refusing to add access later.'}
    $result.originalMembers = @($members | Sort-Object)
    $result.startedAt = [DateTimeOffset]::UtcNow.ToString('o')
    $result.status = 'REMOVING'
    Save-Evidence
    $attemptedRemoval = $true
    Invoke-DemoGraph $token "/groups/$groupId/members/$adminId/`$ref" DELETE | Out-Null
    $members = (Invoke-DemoGraph $token "/groups/$groupId/members?`$select=id").value.id
    if ($members -contains $adminId) {throw 'Removal not confirmed by group readback.'}
    $result.removedAt = [DateTimeOffset]::UtcNow.ToString('o')
    $result.membersWhileRemoved = @($members | Sort-Object)
    $result.status = 'REMOVED; RESTORATION ARMED'
    Save-Evidence
    Write-Output 'Demo administrator removed from KX Readers; restoration armed for five minutes or -Restore.'
    $result.restoreTriggeredEarly = $event.WaitOne(300000)
} finally {
    try {
        if ($attemptedRemoval) {
            $token = Get-DemoAdminToken
            $members = (Invoke-DemoGraph $token "/groups/$groupId/members?`$select=id").value.id
            if ($members -notcontains $adminId) {
                Invoke-DemoGraph $token "/groups/$groupId/members/`$ref" POST @{
                    '@odata.id'="https://graph.microsoft.com/v1.0/users/$adminId"
                } | Out-Null
            }
            $members = (Invoke-DemoGraph $token "/groups/$groupId/members?`$select=id").value.id
            if ($members -notcontains $adminId) {throw 'CRITICAL: demo admin audience membership restoration not confirmed.'}
            $result.restoredAt = [DateTimeOffset]::UtcNow.ToString('o')
            $result.finalMembers = @($members | Sort-Object)
            $result.originalMembershipSetRestored = (@(Compare-Object $result.originalMembers $result.finalMembers).Count -eq 0)
            $result.status = 'RESTORED'
            Save-Evidence
            Write-Output 'Demo administrator restored; group membership read back.'
        }
    } finally {
        $token=$null
        $event.Dispose()
    }
}
